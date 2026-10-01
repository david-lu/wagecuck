import { DEFAULT_SETTINGS, loadSettings, saveSettings } from './lib/config.js';

const $ = (id) => document.getElementById(id);
const PROFESSIONAL = 'Write professionally, clearly, and in the first person.';
const CASUAL = 'Write in a friendly, conversational tone and in the first person. Keep it clear and natural.';
const MAX_TEXT_BYTES = 1024 * 1024;
const MAX_TEXT_LENGTH = 100000;
const MAX_PDF_BYTES = 5 * 1024 * 1024;
const DRAFT_KEY = 'wcPopupDraft';
const AUTOSAVE_MS = 650;
let settings = { ...DEFAULT_SETTINGS };
let resumeFile = null;
let loaded = false;
let importing = false;
let revision = 0;
let autosaveTimer;
let writeQueue = Promise.resolve();
let draftWrite = Promise.resolve();
let pendingWrites = 0;
let lastQueuedRevision = -1;
let draftStored = false;
let lastError = '';

function setStatus(id, message, kind = '') {
  const element = $(id);
  element.textContent = message;
  element.classList.remove('error', 'success');
  if (kind) element.classList.add(kind);
  element.hidden = !message;
}

function collectSettings() {
  return {
    ...settings,
    enabled: $('enable-toggle').checked,
    apiKey: $('api-key').value.trim(),
    model: $('model').value.trim(),
    profile: $('profile').value.trim(),
    writingInstructions: $('writing-instructions').value.trim(),
    resumeText: $('resume-text').value.trim(),
    resumeFile,
    debug: $('debug-toggle').checked,
  };
}

function updateToneChips() {
  const value = $('writing-instructions').value.trim();
  $('tone-professional').setAttribute('aria-pressed', String(value.startsWith(PROFESSIONAL)));
  $('tone-casual').setAttribute('aria-pressed', String(value.startsWith(CASUAL)));
}

function sameResume(a, b) {
  if (!a || !b) return a === b;
  return ['name', 'type', 'size', 'dataUrl'].every((key) => a[key] === b[key]);
}

function sameSettings(a, b) {
  const comparable = (value) => typeof value === 'string' ? value.trim() : value;
  return ['enabled', 'apiKey', 'model', 'profile', 'writingInstructions', 'resumeText', 'debug'].every((key) => comparable(a[key]) === comparable(b[key])) && sameResume(a.resumeFile, b.resumeFile);
}

function updateSetup() {
  const banner = $('setup-banner');
  const connected = Boolean(settings.apiKey);
  const hasBackground = Boolean($('profile').value.trim() || $('resume-text').value.trim() || resumeFile);
  banner.classList.toggle('ready', connected && hasBackground);
  $('setup-action').hidden = connected && hasBackground;
  $('setup-title').textContent = connected && hasBackground ? 'Ready to write' : connected ? 'Make your answers personal' : 'Start with a quick connection';
  $('setup-description').textContent = !connected ? 'Connect AI, then add a little about yourself.' : !hasBackground ? 'Add a résumé or a few facts about yourself.' : 'Click “Write” or “AI” beside a field on the page.';
  $('setup-action').textContent = connected ? 'Add background' : 'Connect AI';
}

function updateFooter() {
  $('save-button').disabled = !loaded || importing || pendingWrites > 0;
  if (pendingWrites) $('settings-form').setAttribute('aria-busy', 'true');
  else $('settings-form').removeAttribute('aria-busy');
  if (!loaded) return;
  if (lastError) setStatus('save-status', lastError, 'error');
  else if (pendingWrites) setStatus('save-status', 'Saving…');
  else if (!sameSettings(collectSettings(), settings)) setStatus('save-status', 'Unsaved changes');
  else setStatus('save-status', 'Saved', 'success');
}

function persistDraft() {
  if (!loaded || !chrome.storage.session) return;
  const currentRevision = revision;
  const draft = collectSettings();
  // The saved PDF can be ~6.7 MB. Do not copy it on each keystroke.
  const resumeFileFromSaved = sameResume(draft.resumeFile, settings.resumeFile);
  draftStored = false;
  draftWrite = chrome.storage.session.set({ [DRAFT_KEY]: {
    version: 1, updatedAt: Date.now(), resumeFileFromSaved,
    settings: { ...draft, resumeFile: resumeFileFromSaved ? null : draft.resumeFile },
  } }).then(() => { if (revision === currentRevision) draftStored = true; }, () => { draftStored = false; });
}

async function clearDraftIfSaved() {
  await draftWrite;
  if (!sameSettings(collectSettings(), settings) || !chrome.storage.session) return;
  try { await chrome.storage.session.remove(DRAFT_KEY); draftStored = false; } catch { /* A redundant saved draft is harmless. */ }
}

function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  if (loaded && !importing && !sameSettings(collectSettings(), settings)) {
    autosaveTimer = setTimeout(() => flushSettings(), AUTOSAVE_MS);
  }
}

function markChanged() {
  updateToneChips();
  renderResume();
  if (!loaded) return;
  revision += 1;
  lastError = '';
  if (/^[a-zA-Z0-9._:-]{1,120}$/.test($('model').value.trim())) $('model').removeAttribute('aria-invalid');
  persistDraft();
  updateSetup();
  updateFooter();
  scheduleAutosave();
}

function renderResume() {
  const hasText = Boolean($('resume-text').value.trim());
  $('resume-attachment').hidden = !resumeFile && !hasText;
  $('resume-file-name').textContent = resumeFile?.name || (hasText ? 'Résumé text added' : '');
}

function renderSettings(view = settings) {
  $('enable-toggle').checked = view.enabled;
  $('api-key').value = view.apiKey;
  $('model').value = view.model;
  $('profile').value = view.profile;
  $('writing-instructions').value = view.writingInstructions;
  $('resume-text').value = view.resumeText;
  $('debug-toggle').checked = view.debug;
  resumeFile = view.resumeFile;
  renderResume();
  updateToneChips();
}

function selectTab(name, focus = false) {
  for (const tabName of ['profile', 'writing', 'connection']) {
    const active = name === tabName;
    const tab = $(`tab-${tabName}`);
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
    $(`panel-${tabName}`).hidden = !active;
  }
  document.querySelector('.panels').scrollTop = 0;
  if (focus) $(`tab-${name}`).focus();
}

async function withTimeout(promise, milliseconds = 7000) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('The page did not respond. Try refreshing it.')), milliseconds);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

async function refreshPage(rescan = false) {
  const button = $('rescan-button');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  $('page-indicator').classList.remove('ready');
  $('page-status').textContent = rescan ? 'Finding fields…' : 'Looking for fields…';
  try {
    const [tab] = await withTimeout(chrome.tabs.query({ active: true, currentWindow: true }));
    if (!tab?.id || (tab.url && !/^https?:\/\//i.test(tab.url))) {
      $('page-status').textContent = 'Open an application page.';
      return;
    }
    const status = await withTimeout(chrome.tabs.sendMessage(tab.id, { type: rescan ? 'WC_RESCAN' : 'WC_GET_PAGE_STATUS' }, { frameId: 0 }));
    if (status?.error) throw new Error('Could not scan this page.');
    // Storage is authoritative; the content script may be applying the toggle.
    const enabled = settings.enabled;
    const count = Number.isFinite(status?.count) ? Math.max(0, status.count) : 0;
    $('page-status').textContent = !enabled ? 'AI filling is paused.' : count ? `${count} ${count === 1 ? 'field' : 'fields'} ready to write` : 'No writable fields. Try an application page.';
    $('page-indicator').classList.toggle('ready', enabled && count > 0);
  } catch {
    $('page-status').textContent = 'Open an application page, or refresh it.';
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
}

function validateDraft(draft, manual) {
  if (!/^[a-zA-Z0-9._:-]{1,120}$/.test(draft.model)) {
    $('model').setAttribute('aria-invalid', 'true');
    lastError = draft.model ? 'Use a valid OpenAI model name.' : 'Enter an OpenAI model name.';
    if (manual) { selectTab('connection'); $('advanced-settings').open = true; $('model').focus(); }
    updateFooter();
    return false;
  }
  $('model').removeAttribute('aria-invalid');
  if (draft.apiKey && (draft.apiKey.length < 8 || /\s/.test(draft.apiKey))) {
    $('api-key').setAttribute('aria-invalid', 'true');
    lastError = 'Paste the full API key without spaces.';
    if (manual) { selectTab('connection'); $('api-key').focus(); }
    updateFooter();
    return false;
  }
  $('api-key').removeAttribute('aria-invalid');
  return true;
}

function enqueueWrite(operation) {
  pendingWrites += 1;
  updateFooter();
  writeQueue = writeQueue.then(async () => {
    try { await operation(); }
    catch {
      lastError = draftStored ? 'Could not save. Draft kept — try Save.' : 'Not saved. Try Save before closing.';
      lastQueuedRevision = -1;
    } finally {
      pendingWrites -= 1;
      updateSetup();
      updateFooter();
    }
  });
  return writeQueue;
}

function flushSettings({ manual = false } = {}) {
  clearTimeout(autosaveTimer);
  if (!loaded || importing) return Promise.resolve();
  const draft = collectSettings();
  if (!validateDraft(draft, manual)) return Promise.resolve();
  if (sameSettings(draft, settings)) { updateFooter(); return clearDraftIfSaved(); }
  if (!manual && lastQueuedRevision === revision) return writeQueue;
  const capturedRevision = revision;
  lastQueuedRevision = capturedRevision;
  lastError = '';
  return enqueueWrite(async () => {
    settings = await saveSettings(draft);
    // Never render a saved snapshot over newer edits made during the write.
    if (revision === capturedRevision && sameSettings(collectSettings(), settings)) await clearDraftIfSaved();
    else { persistDraft(); scheduleAutosave(); }
    void refreshPage();
  });
}

function save(event) {
  event.preventDefault();
  void flushSettings({ manual: true });
}

function persistEnabled() {
  if (!loaded) return;
  const enabled = $('enable-toggle').checked;
  revision += 1;
  lastError = '';
  persistDraft();
  void enqueueWrite(async () => {
    const stored = await loadSettings();
    settings = await saveSettings({ ...stored, enabled });
    if (sameSettings(collectSettings(), settings)) await clearDraftIfSaved();
    else persistDraft();
    void refreshPage();
  });
}

function applyTone(preset) {
  let custom = $('writing-instructions').value.trim();
  let changed = true;
  while (changed) {
    changed = false;
    for (const previous of [PROFESSIONAL, CASUAL]) {
      if (custom.startsWith(previous)) { custom = custom.slice(previous.length).trim(); changed = true; break; }
    }
  }
  $('writing-instructions').value = custom ? `${preset}\n\n${custom}` : preset;
  markChanged();
}

function readDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener('load', () => resolve(reader.result), { once: true });
    reader.addEventListener('error', () => reject(new Error('Could not read that file. Please try again.')), { once: true });
    reader.readAsDataURL(file);
  });
}

function setImporting(value) {
  importing = value;
  if (value) clearTimeout(autosaveTimer);
  $('profile-import').disabled = value;
  $('resume-upload').disabled = value;
  $('profile-import-button').disabled = value;
  $('resume-upload-button').disabled = value;
  updateFooter();
  if (!value) scheduleAutosave();
}

async function uploadResume(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  setImporting(true);
  setStatus('resume-status', 'Reading résumé…');
  try {
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (extension === 'pdf') {
      if (file.size > MAX_PDF_BYTES) throw new Error('The PDF must be 5 MB or smaller.');
      const header = new Uint8Array(await file.slice(0, 5).arrayBuffer());
      if (String.fromCharCode(...header) !== '%PDF-') throw new Error('That file is not a valid PDF.');
      const rawDataUrl = await readDataUrl(file);
      const dataUrl = `data:application/pdf;base64,${String(rawDataUrl).split(',')[1]}`;
      resumeFile = { name: file.name, type: 'application/pdf', dataUrl, size: file.size };
      $('resume-text').value = '';
      $('resume-editor').open = false;
      renderResume();
      setStatus('resume-status', 'PDF attached. AI can use it in your answers.');
    } else if (['txt', 'md'].includes(extension)) {
      if (file.size > MAX_TEXT_BYTES) throw new Error('Text files must be 1 MB or smaller.');
      const text = await file.text();
      if (text.length > MAX_TEXT_LENGTH) throw new Error('Keep résumé text under 100,000 characters.');
      if (text.includes('\0')) throw new Error('Please upload a plain text or Markdown file.');
      $('resume-text').value = text;
      $('resume-editor').open = true;
      resumeFile = null;
      renderResume();
      setStatus('resume-status', 'Résumé added. You can edit the text below.');
    } else {
      throw new Error('Choose a PDF, TXT, or Markdown file.');
    }
    markChanged();
  } catch (error) {
    setStatus('resume-status', error.message || 'Could not import the résumé.', 'error');
  } finally {
    event.target.value = '';
    setImporting(false);
  }
}

function scrubProfile(value) {
  if (Array.isArray(value)) return value.map(scrubProfile).filter((item) => item !== undefined);
  if (value && typeof value === 'object') {
    const clean = Object.create(null);
    for (const [key, item] of Object.entries(value)) {
      if (/(?:password|secret|token|api.?key|credential|private.?key|file.?path|^resume$|^cover_letter$|^path$)/i.test(key)) continue;
      const safe = scrubProfile(item);
      if (safe !== undefined) clean[key] = safe;
    }
    return clean;
  }
  if (typeof value === 'string') {
    if (/\bsk-[a-zA-Z0-9_-]{12,}|(?:[a-zA-Z]:[\\/]|\/(?:Users|home|tmp|workspace)\/|file:\/\/)/.test(value)) return undefined;
  }
  return value;
}

async function importProfile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  setImporting(true);
  setStatus('profile-import-status', 'Reading profile…');
  try {
    if (file.size > MAX_TEXT_BYTES) throw new Error('Profile files must be 1 MB or smaller.');
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (!['json', 'txt', 'md'].includes(extension)) throw new Error('Choose a JSON, TXT, or Markdown profile.');
    const text = await file.text();
    if (text.includes('\0')) throw new Error('Please upload a plain text profile.');
    let imported = text;
    if (extension === 'json') {
      let parsed;
      try { parsed = JSON.parse(text); } catch { throw new Error('That JSON profile is invalid. Check the file and try again.'); }
      if (!parsed || typeof parsed !== 'object') throw new Error('The JSON profile must contain an object or array.');
      imported = JSON.stringify(scrubProfile(parsed), null, 2);
    }
    if (imported.length > MAX_TEXT_LENGTH) throw new Error('Keep profile text under 100,000 characters.');
    $('profile').value = imported;
    markChanged();
    setStatus('profile-import-status', extension === 'json' ? 'Profile imported. File paths and secret fields were excluded.' : 'Profile imported. You can edit it below.');
  } catch (error) {
    setStatus('profile-import-status', error.message || 'Could not import the profile.', 'error');
  } finally {
    event.target.value = '';
    setImporting(false);
  }
}

function safeLogs(logs) {
  // Display only metadata fields even if another component accidentally records more.
  const allowed = new Set(['timestamp', 'time', 'at', 'level', 'event', 'code', 'status', 'durationMs', 'count', 'fieldType', 'model', 'inputCharacters', 'outputCharacters', 'pageChars', 'answerChars', 'frameCount', 'unavailableFrames', 'requestId', 'attempt', 'enabled', 'configured', 'truncated', 'reason']);
  function metadata(value, depth = 0) {
    if (!value || typeof value !== 'object' || depth > 2) return {};
    const clean = {};
    for (const [key, item] of Object.entries(value)) {
      if (['details', 'meta', 'metadata'].includes(key) && item && typeof item === 'object') {
        const nested = metadata(item, depth + 1);
        if (Object.keys(nested).length) clean[key] = nested;
      } else if (allowed.has(key) && ['string', 'number', 'boolean'].includes(typeof item)) {
        clean[key] = typeof item === 'string' ? item.replace(/\bsk-[a-zA-Z0-9_-]{8,}/g, '[redacted]').slice(0, 160) : item;
      }
    }
    return clean;
  }
  return Array.isArray(logs) ? logs.slice(-100).map((entry) => metadata(entry)) : [];
}

async function refreshLogs() {
  try {
    const result = await chrome.storage.local.get('wcLogs');
    const logs = safeLogs(result.wcLogs);
    $('log-output').textContent = logs.length ? logs.map((log) => JSON.stringify(log)).join('\n') : 'No logs yet.';
    return true;
  } catch {
    setStatus('log-status', 'Could not load logs. Try again.', 'error');
    return false;
  }
}

async function copyLogs() {
  if (!await refreshLogs()) return;
  try {
    await navigator.clipboard.writeText($('log-output').textContent);
    setStatus('log-status', 'Logs copied.');
  } catch {
    setStatus('log-status', 'Could not copy. Select the log text to copy it.', 'error');
  }
}

async function clearLogs() {
  try {
    await chrome.storage.local.set({ wcLogs: [] });
    await refreshLogs();
    setStatus('log-status', 'Logs cleared.');
  } catch {
    setStatus('log-status', 'Could not clear logs. Try again.', 'error');
  }
}

for (const tab of document.querySelectorAll('[role="tab"]')) {
  tab.addEventListener('click', () => selectTab(tab.id.replace('tab-', '')));
  tab.addEventListener('keydown', (event) => {
    const names = ['profile', 'writing', 'connection'];
    const index = names.indexOf(tab.id.replace('tab-', ''));
    const next = { ArrowRight: (index + 1) % 3, ArrowLeft: (index + 2) % 3, Home: 0, End: 2 }[event.key];
    if (next !== undefined) { event.preventDefault(); selectTab(names[next], true); }
  });
}

$('settings-form').addEventListener('submit', save);
$('settings-form').addEventListener('input', (event) => { if (event.target.type !== 'file') markChanged(); });
$('enable-toggle').addEventListener('change', persistEnabled);
$('setup-action').addEventListener('click', () => {
  const connected = Boolean(settings.apiKey);
  selectTab(connected ? 'profile' : 'connection');
  (connected ? $('resume-upload-button') : $('api-key')).focus();
});
$('rescan-button').addEventListener('click', () => refreshPage(true));
$('profile-import-button').addEventListener('click', () => $('profile-import').click());
$('resume-upload-button').addEventListener('click', () => $('resume-upload').click());
$('profile-import').addEventListener('change', importProfile);
$('resume-upload').addEventListener('change', uploadResume);
$('resume-remove').addEventListener('click', () => {
  resumeFile = null;
  $('resume-text').value = '';
  renderResume();
  setStatus('resume-status', 'Résumé removed.');
  markChanged();
});
$('tone-professional').addEventListener('click', () => applyTone(PROFESSIONAL));
$('tone-casual').addEventListener('click', () => applyTone(CASUAL));
$('reveal-key').addEventListener('click', () => {
  const show = $('api-key').type === 'password';
  $('api-key').type = show ? 'text' : 'password';
  $('reveal-key').textContent = show ? 'Hide' : 'Show';
  $('reveal-key').setAttribute('aria-label', show ? 'Hide API key' : 'Show API key');
  $('reveal-key').setAttribute('aria-pressed', String(show));
});
$('troubleshooting').addEventListener('toggle', () => { if ($('troubleshooting').open) refreshLogs(); });
$('log-refresh').addEventListener('click', refreshLogs);
$('log-copy').addEventListener('click', copyLogs);
$('log-clear').addEventListener('click', clearLogs);
chrome.storage.onChanged?.addListener((changes, area) => {
  if (area === 'local' && changes.wcLogs && $('troubleshooting').open) refreshLogs();
});

async function initialize() {
  try {
    settings = await loadSettings();
    let restored = null;
    if (chrome.storage.session) {
      try {
        const values = await chrome.storage.session.get(DRAFT_KEY);
        const draft = values[DRAFT_KEY];
        if (draft?.version === 1 && draft.settings && typeof draft.settings === 'object') {
          const raw = draft.settings;
          if (['apiKey', 'model', 'profile', 'writingInstructions', 'resumeText'].every((key) => typeof raw[key] === 'string') && typeof raw.enabled === 'boolean' && typeof raw.debug === 'boolean') {
            restored = { ...raw, resumeFile: draft.resumeFileFromSaved ? settings.resumeFile : raw.resumeFile };
            draftStored = true;
          }
        }
      } catch { /* Local saved settings remain available if session storage fails. */ }
    }
    renderSettings(restored || settings);
    loaded = true;
    selectTab(settings.apiKey ? 'profile' : 'connection');
    updateSetup();
    updateFooter();
    if (restored && !sameSettings(collectSettings(), settings)) {
      setStatus('save-status', 'Your unsaved changes are back');
      scheduleAutosave();
    } else {
      void clearDraftIfSaved();
    }
  } catch {
    setStatus('save-status', 'Could not load settings. Reopen the extension.', 'error');
  }
  await refreshPage();
}

function flushBeforeClose() {
  if (!loaded || importing) return;
  persistDraft();
  void flushSettings();
}

document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushBeforeClose(); });
window.addEventListener('pagehide', flushBeforeClose);

initialize();
