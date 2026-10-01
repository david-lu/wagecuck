import { DEFAULT_SETTINGS, SETTINGS_KEY, LOGS_KEY, loadSettings } from './lib/config.js';
import { AgentError, combinePageSnapshots, errorToPublic, generateAnswer, validateFieldAndPage } from './lib/agent.js';
import { logEvent } from './lib/logging.js';

const activeRequests = new Map();
const REQUEST_ID = /^[a-zA-Z0-9_.:-]{1,120}$/;
// Restrict before any configuration access. No key/profile/resume is sent to content scripts.
const storageReady = chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
storageReady.catch(() => console.error('[Wagecuck Input Filler] Could not restrict settings storage. Generation is disabled.'));

function trustedSender(sender) {
  return sender?.id === chrome.runtime.id;
}

function trustedExtensionPage(sender) {
  return trustedSender(sender) && typeof sender.url === 'string' && sender.url.startsWith(chrome.runtime.getURL(''));
}

function trustedContent(sender) {
  return trustedSender(sender) && Number.isInteger(sender.tab?.id) &&
    (/^https?:\/\//i.test(sender.url || '') || (/^about:(blank|srcdoc)$/i.test(sender.url || '') && /^https?:\/\//i.test(sender.origin || '')));
}

function post(port, message) {
  try { port.postMessage(message); return true; }
  catch { return false; }
}

function withDeadline(promise, milliseconds = 4000) {
  let timeout;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('Frame context timed out.')), milliseconds); }),
  ]).finally(() => clearTimeout(timeout));
}

async function gatherPageContext(sender, clickedPage) {
  let frames;
  try { frames = await withDeadline(chrome.webNavigation.getAllFrames({ tabId: sender.tab.id })); }
  catch { return combinePageSnapshots(clickedPage, [], 1); }
  if (!Array.isArray(frames)) return combinePageSnapshots(clickedPage, [], 1);
  const results = await Promise.allSettled(frames.filter(frame => frame.frameId !== sender.frameId).map(frame =>
    withDeadline(chrome.tabs.sendMessage(sender.tab.id, { type: 'WC_PAGE_CONTEXT' }, { frameId: frame.frameId })),
  ));
  const snapshots = [];
  let unavailable = 0;
  for (const result of results) {
    if (result.status === 'fulfilled' && result.value && typeof result.value.text === 'string') snapshots.push(result.value);
    else unavailable++;
  }
  return combinePageSnapshots(clickedPage, snapshots, unavailable);
}

chrome.runtime.onInstalled.addListener(() => {
  void storageReady.then(async () => {
    const values = await chrome.storage.local.get(SETTINGS_KEY);
    if (!values[SETTINGS_KEY]) await chrome.storage.local.set({ [SETTINGS_KEY]: { ...DEFAULT_SETTINGS } });
    await logEvent('extension.ready');
  }).catch(() => {});
});

chrome.runtime.onConnect.addListener(port => {
  if (port.name !== 'wc-fill' || !trustedContent(port.sender)) { port.disconnect(); return; }
  let current = null;
  let disconnected = false;
  port.onDisconnect.addListener(() => {
    disconnected = true;
    current?.controller.abort();
  });
  port.onMessage.addListener(message => {
    if (!message || typeof message !== 'object' || !REQUEST_ID.test(message.requestId || '')) {
      post(port, { type: 'error', code: 'INVALID_REQUEST', message: 'The fill request is invalid. Reload the page and try again.', requestId: '' });
      return;
    }
    if (message.type === 'cancel') {
      if (current?.requestId === message.requestId) current.controller.abort();
      return;
    }
    if (message.type !== 'generate') return;
    if (current || activeRequests.size >= 4) {
      post(port, { type: 'error', code: 'BUSY', message: 'A fill is already running. Wait for it to finish or cancel it.', requestId: message.requestId });
      return;
    }
    const operation = { requestId: message.requestId, controller: new AbortController(), state: 'waiting' };
    current = operation;
    activeRequests.set(operation, true);
    const started = Date.now();
    const heartbeat = setInterval(() => {
      if (!post(port, { type: 'state', state: operation.state, requestId: operation.requestId })) operation.controller.abort();
    }, 20_000);
    post(port, { type: 'state', state: 'waiting', requestId: operation.requestId });
    void (async () => {
      try {
        await storageReady;
        validateFieldAndPage(message.field, message.page);
        const settings = await loadSettings();
        if (!settings.enabled) throw new AgentError('DISABLED', 'Enable the extension in its settings first.');
        if (!settings.apiKey) throw new AgentError('NOT_CONFIGURED', 'Add your OpenAI API key in the extension settings first.');
        const page = await gatherPageContext(port.sender, message.page);
        if (operation.controller.signal.aborted) throw new AgentError('CANCELLED', 'Generation cancelled.');
        await logEvent('generation.started', { requestId: operation.requestId, model: settings.model, fieldType: message.field.type, pageChars: page.text.length, frameCount: page.frameCount, unavailableFrames: page.unavailableFrames });
        if (operation.controller.signal.aborted) throw new AgentError('CANCELLED', 'Generation cancelled.');
        const answer = await generateAnswer({ settings, field: message.field, page, signal: operation.controller.signal, onState: state => {
          operation.state = state;
          if (!post(port, { type: 'state', state, requestId: operation.requestId })) operation.controller.abort();
        } });
        if (!disconnected && !operation.controller.signal.aborted) post(port, { type: 'result', answer, requestId: operation.requestId });
        await logEvent('generation.completed', { requestId: operation.requestId, durationMs: Date.now() - started, answerChars: answer.length });
      } catch (error) {
        const publicError = errorToPublic(error);
        if (!disconnected) post(port, { type: 'error', ...publicError, requestId: operation.requestId });
        await logEvent('generation.failed', { requestId: operation.requestId, code: publicError.code, durationMs: Date.now() - started }, publicError.code === 'CANCELLED' ? 'info' : 'warn');
      } finally {
        clearInterval(heartbeat);
        activeRequests.delete(operation);
        if (current === operation) current = null;
      }
    })();
  });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!trustedSender(sender) || !message || typeof message !== 'object') return false;
  if (message.type === 'WC_GET_STATUS' && (trustedContent(sender) || trustedExtensionPage(sender))) {
    void storageReady.then(() => loadSettings()).then(settings => sendResponse({ enabled: settings.enabled, configured: Boolean(settings.apiKey) })).catch(() => sendResponse({ enabled: false, configured: false }));
    return true;
  }
  if (message.type === 'WC_GET_LOGS' && trustedExtensionPage(sender)) {
    void storageReady.then(() => chrome.storage.local.get(LOGS_KEY)).then(values => sendResponse({ logs: Array.isArray(values[LOGS_KEY]) ? values[LOGS_KEY] : [] })).catch(() => sendResponse({ logs: [] }));
    return true;
  }
  if (message.type === 'WC_LOG_EVENT' && trustedContent(sender)) {
    if (!['ui.filled', 'ui.undo', 'ui.edit_preserved', 'ui.cancelled', 'ui.insert_failed'].includes(message.event)) return false;
    const metadata = message.metadata && typeof message.metadata === 'object' ? message.metadata : {};
    void storageReady.then(() => logEvent(message.event, { requestId: metadata.requestId, code: metadata.code, durationMs: metadata.durationMs })).then(() => sendResponse({ logged: true })).catch(() => sendResponse({ logged: false }));
    return true;
  }
  if (message.type === 'WC_OPEN_SETTINGS' && (trustedContent(sender) || trustedExtensionPage(sender))) {
    void (async () => {
      try { await chrome.action.openPopup(); }
      catch { await chrome.tabs.create({ url: chrome.runtime.getURL('popup.html') }); }
    })().then(() => sendResponse({ opened: true })).catch(() => sendResponse({ opened: false }));
    return true;
  }
  return false;
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== 'local' || !changes[SETTINGS_KEY]) return;
  const enabled = changes[SETTINGS_KEY].newValue?.enabled !== false;
  if (!enabled) for (const operation of activeRequests.keys()) operation.controller.abort();
  void chrome.tabs.query({}).then(async tabs => {
    await Promise.allSettled(tabs.filter(tab => Number.isInteger(tab.id)).map(async tab => {
      try {
        const frames = await chrome.webNavigation.getAllFrames({ tabId: tab.id });
        await Promise.allSettled((frames || []).map(frame => chrome.tabs.sendMessage(tab.id, { type: 'WC_SETTINGS_CHANGED', enabled }, { frameId: frame.frameId })));
      } catch { /* Tabs may navigate or close while preferences are being broadcast. */ }
    }));
  }).catch(() => {});
});
