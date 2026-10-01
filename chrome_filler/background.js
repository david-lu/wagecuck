import { SETTINGS_KEY, LOGS_KEY, loadSettings } from './lib/config.js';
import { AgentError, combinePageSnapshots, errorToPublic, generateAnswer, generateBatchAnswers, validateFieldAndPage } from './lib/agent.js';
import { logEvent } from './lib/logging.js';

const activeRequests = new Map();
const batchRuns = new Map();
const REQUEST_ID = /^[a-zA-Z0-9_.:-]{1,120}$/;
// Restrict storage before configuration access. Only the saved PDF is sent to a content script for an explicit attach action.
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

async function pageScanStatus(tabId, scan = false) {
  const frames = await chrome.webNavigation.getAllFrames({ tabId });
  if (!/^https?:\/\//i.test(frames?.find(frame => frame.frameId === 0)?.url || '')) throw new Error('Open a website to scan it.');
  const frameIds = Array.isArray(frames) && frames.length ? frames.map(frame => frame.frameId) : [0];
  const replies = await Promise.allSettled(frameIds.map(frameId =>
    withDeadline(chrome.tabs.sendMessage(tabId, { type: scan ? 'WC_RESCAN' : 'WC_GET_PAGE_STATUS' }, { frameId }))));
  const statuses = replies.filter(result => result.status === 'fulfilled' && result.value && typeof result.value === 'object').map(result => result.value);
  if (!statuses.length) throw new Error('Reload the page before scanning it.');
  return {
    count: statuses.reduce((total, status) => total + (Number.isFinite(status.count) ? Math.max(0, status.count) : 0), 0),
    scanned: statuses.some(status => status.scanned === true),
    enabled: statuses.some(status => status.enabled !== false),
  };
}

async function startFillAllTab(tabId) {
  if (batchRuns.has(tabId)) return { error: 'FILL ALL is already running.' };
  const frames = await chrome.webNavigation.getAllFrames({ tabId });
  if (!/^https?:\/\//i.test(frames?.find(frame => frame.frameId === 0)?.url || '')) throw new Error('Open an application page first.');
  const run = { id: crypto.randomUUID(), controller: new AbortController(), frames: [] };
  batchRuns.set(tabId, run);
  try {
    const replies = await Promise.allSettled(frames.map(frame =>
      withDeadline(chrome.tabs.sendMessage(tabId, { type: 'WC_BATCH_SNAPSHOT' }, { frameId: frame.frameId }), 15000)));
    if (run.controller.signal.aborted) return { error: 'FILL ALL stopped.' };
    const snapshots = frames.flatMap((frame, index) => {
      const reply = replies[index];
      return reply.status === 'fulfilled' && reply.value?.scanned && reply.value?.enabled !== false && reply.value?.page ? [{ frameId: frame.frameId, ...reply.value }] : [];
    });
    const top = snapshots.find(snapshot => snapshot.frameId === 0);
    if (!top) return { error: 'Scan the page first.' };
    const targets = snapshots.flatMap(snapshot => (snapshot.targets || []).map(target => ({ id: `${snapshot.frameId}:${target.id}`, field: target.field })));
    const uploadTargets = snapshots.flatMap(snapshot => (snapshot.uploadTargets || []).map(target => ({ id: `${snapshot.frameId}:${target.id}`, field: target.field })));
    const unavailableChoices = snapshots.reduce((total, snapshot) => total + (snapshot.unavailableChoices || 0), 0);
    if (!targets.length && !uploadTargets.length) return { error: unavailableChoices ? 'Some dropdowns need a manual choice because their options are unavailable until you search or interact with them.' : 'There are no empty scanned fields to fill.' };
    const page = combinePageSnapshots(top.page, snapshots.filter(snapshot => snapshot !== top).map(snapshot => snapshot.page), frames.length - snapshots.length);
    const begun = await Promise.allSettled(snapshots.map(snapshot => withDeadline(chrome.tabs.sendMessage(tabId, {
      type: 'WC_BATCH_BEGIN', runId: run.id, count: targets.length + uploadTargets.length, targets: snapshot.targets || [], uploadTargets: snapshot.uploadTargets || [],
    }, { frameId: snapshot.frameId }))));
    run.frames = snapshots.filter((_, index) => begun[index].status === 'fulfilled' && begun[index].value?.started).map(snapshot => snapshot.frameId);
    if (run.controller.signal.aborted) return { error: 'FILL ALL stopped.' };
    if (!run.frames.includes(0)) return { error: 'The page changed. Scan it again.' };
    const accepted = targets.filter(target => run.frames.includes(Number(target.id.split(':', 1)[0])));
    const acceptedUploads = uploadTargets.filter(target => run.frames.includes(Number(target.id.split(':', 1)[0])));
    if (!accepted.length && !acceptedUploads.length) return { error: 'The page changed. Scan it again.' };
    run.unavailableChoices = unavailableChoices;
    run.started = true;
    void runBatch(tabId, run, accepted, acceptedUploads, page);
    return { started: true, count: accepted.length + acceptedUploads.length };
  } catch (error) {
    return { error: errorToPublic(error).message };
  } finally {
    if (!run.started) {
      if (batchRuns.get(tabId) === run) batchRuns.delete(tabId);
      await Promise.allSettled(run.frames.map(frameId => withDeadline(chrome.tabs.sendMessage(tabId, {
        type: 'WC_BATCH_DONE', runId: run.id, stopped: true, summary: { filled: 0, skipped: 0, failed: 0, unchecked: 0 }, errorMessage: '',
      }, { frameId }))));
    }
  }
}

async function stopFillAllTab(tabId) {
  const run = batchRuns.get(tabId);
  if (!run) return { stopped: false };
  run.controller.abort();
  return { stopped: true };
}

async function runBatch(tabId, run, targets, uploadTargets, page) {
  let summary = { filled: 0, skipped: run.unavailableChoices || 0, failed: 0, unchecked: 0 };
  let errorMessage = '';
  let fieldError = run.unavailableChoices ? 'Some searchable dropdowns need a manual choice.' : '';
  try {
    await storageReady;
    const settings = await loadSettings();
    if (run.controller.signal.aborted) throw new AgentError('CANCELLED', 'FILL ALL stopped.');
    for (const frameId of run.frames) {
      if (run.controller.signal.aborted) break;
      const ids = uploadTargets.filter(target => target.id.startsWith(`${frameId}:`)).map(target => target.id.slice(String(frameId).length + 1));
      if (!ids.length) continue;
      try {
        const result = await withDeadline(chrome.tabs.sendMessage(tabId, { type: 'WC_BATCH_ATTACH_RESUME', runId: run.id, ids, resumeFile: settings.resumeFile }, { frameId }));
        summary.filled += result?.filled || 0;
        summary.skipped += result?.skipped || 0;
        summary.failed += result?.failed || 0;
        fieldError ||= result?.firstError || '';
      } catch { summary.failed += ids.length; fieldError ||= 'The resume could not be attached.'; }
    }
    if (!targets.length) return;
    await logEvent('generation.started', { requestId: run.id, model: settings.model, fieldType: 'batch', pageChars: page.text.length, frameCount: page.frameCount, unavailableFrames: page.unavailableFrames });
    const answers = await generateBatchAnswers({ settings, targets, page, signal: run.controller.signal });
    if (run.controller.signal.aborted) throw new AgentError('CANCELLED', 'FILL ALL stopped.');
    for (const frameId of run.frames) {
      if (run.controller.signal.aborted) break;
      const local = answers.filter(answer => answer.fieldId.startsWith(`${frameId}:`)).map(answer => ({ ...answer, fieldId: answer.fieldId.slice(String(frameId).length + 1) }));
      if (!local.length) continue;
      try {
        const result = await withDeadline(chrome.tabs.sendMessage(tabId, { type: 'WC_BATCH_APPLY', runId: run.id, answers: local }, { frameId }));
        summary.filled += result?.filled || 0;
        summary.skipped += result?.skipped || 0;
        summary.failed += result?.failed || 0;
        summary.unchecked += result?.unchecked || 0;
        fieldError ||= result?.firstError || '';
      } catch { summary.failed += local.length; }
    }
    await logEvent('generation.completed', { requestId: run.id, answerChars: answers.reduce((total, answer) => total + (answer.answer?.length || 0), 0) });
  } catch (error) {
    errorMessage = errorToPublic(error).message;
    await logEvent('generation.failed', { requestId: run.id, code: errorToPublic(error).code }, run.controller.signal.aborted ? 'info' : 'warn');
  } finally {
    const stopped = run.controller.signal.aborted;
    if (batchRuns.get(tabId) === run) batchRuns.delete(tabId);
    await Promise.allSettled(run.frames.map(frameId => withDeadline(chrome.tabs.sendMessage(tabId, {
      type: 'WC_BATCH_DONE', runId: run.id, stopped, summary, errorMessage, fieldError,
    }, { frameId }))));
  }
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
  void storageReady.then(() => logEvent('extension.ready')).catch(() => {});
});

chrome.runtime.onConnect.addListener(port => {
  if (port.name === 'wc-batch' && trustedContent(port.sender) && port.sender.frameId === 0) {
    const run = batchRuns.get(port.sender.tab.id);
    if (!run) { port.disconnect(); return; }
    port.onMessage.addListener(message => {
      if (message?.type !== 'heartbeat' || message.runId !== run.id) port.disconnect();
    });
    port.onDisconnect.addListener(() => { if (batchRuns.get(port.sender.tab.id) === run) run.controller.abort(); });
    return;
  }
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
        await logEvent('generation.completed', { requestId: operation.requestId, durationMs: Date.now() - started, answerChars: String(answer).length });
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
  if ((message.type === 'WC_SCAN_TAB' || message.type === 'WC_GET_TAB_SCAN_STATUS') && (trustedContent(sender) || trustedExtensionPage(sender))) {
    const tabId = trustedContent(sender) ? sender.tab.id : message.tabId;
    if (!Number.isInteger(tabId) || tabId < 0) { sendResponse({ error: 'No website tab is available.' }); return false; }
    void pageScanStatus(tabId, message.type === 'WC_SCAN_TAB').then(sendResponse).catch(error => sendResponse({ error: error.message }));
    return true;
  }
  if (message.type === 'WC_FILL_ALL_TAB' && (trustedContent(sender) || trustedExtensionPage(sender))) {
    const tabId = trustedContent(sender) ? sender.tab.id : message.tabId;
    if (!Number.isInteger(tabId) || tabId < 0) { sendResponse({ error: 'No website tab is available.' }); return false; }
    void startFillAllTab(tabId).then(sendResponse).catch(error => sendResponse({ error: error.message }));
    return true;
  }
  if (message.type === 'WC_STOP_ALL_TAB' && trustedContent(sender)) {
    void stopFillAllTab(sender.tab.id).then(sendResponse).catch(() => sendResponse({ stopped: false }));
    return true;
  }
  if (message.type === 'WC_GET_STATUS' && (trustedContent(sender) || trustedExtensionPage(sender))) {
    void storageReady.then(() => loadSettings()).then(settings => sendResponse({ enabled: settings.enabled, configured: Boolean(settings.apiKey) })).catch(() => sendResponse({ enabled: false, configured: false }));
    return true;
  }
  if (message.type === 'WC_GET_RESUME_FOR_UPLOAD' && trustedContent(sender)) {
    void storageReady.then(() => loadSettings()).then(settings => sendResponse({ resumeFile: settings.enabled ? settings.resumeFile : null })).catch(() => sendResponse({ resumeFile: null }));
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
  if (!enabled) {
    for (const operation of activeRequests.keys()) operation.controller.abort();
    for (const run of batchRuns.values()) run.controller.abort();
  }
  void chrome.tabs.query({}).then(async tabs => {
    await Promise.allSettled(tabs.filter(tab => Number.isInteger(tab.id)).map(async tab => {
      try {
        const frames = await chrome.webNavigation.getAllFrames({ tabId: tab.id });
        await Promise.allSettled((frames || []).map(frame => chrome.tabs.sendMessage(tab.id, { type: 'WC_SETTINGS_CHANGED', enabled }, { frameId: frame.frameId })));
      } catch { /* Tabs may navigate or close while preferences are being broadcast. */ }
    }));
  }).catch(() => {});
});
