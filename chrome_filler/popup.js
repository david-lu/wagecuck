import { DEFAULT_SETTINGS, EMPTY_PROFILE_FACTS, loadSettings, saveSettings } from './lib/config.js';

const $ = (id) => document.getElementById(id);
const PROFESSIONAL = 'Write professionally, clearly, and in the first person.';
const CASUAL = DEFAULT_SETTINGS.writingInstructions;
const MAX_TEXT_BYTES = 1024 * 1024;
const MAX_TEXT_LENGTH = 100000;
const MAX_PDF_BYTES = 5 * 1024 * 1024;
const PROFILE_FACT_KEYS = Object.keys(EMPTY_PROFILE_FACTS);
let settings = { ...DEFAULT_SETTINGS };
let resumeFile = null;
let loaded = false;
let importing = false;
let writeQueue = Promise.resolve();
let pendingWrites = 0;
let lastError = '';
let pageTabId = null;
let customModel = '';

function setFillButtonMode(refill) {
  const button = $('do-all-button');
  button.dataset.refill = String(refill);
  button.querySelector('.action-label').textContent = refill ? 'REFILL' : 'FILL';
  button.setAttribute('aria-label', refill ? 'REFILL: regenerate previously filled answers and fill empty fields' : 'FILL: fill unanswered scanned fields');
}

function selectedModel() {
  return $('model-preset').value === 'custom' ? $('model').value.trim() : $('model-preset').value;
}

function renderModel(model) {
  const preset = [...$('model-preset').options].some(option => option.value === model && option.value !== 'custom');
  $('model-preset').value = preset ? model : 'custom';
  $('model').value = preset ? '' : model;
  $('model-custom').hidden = preset;
  customModel = preset ? '' : model;
}

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
    model: selectedModel(),
    profile: $('profile').value.trim(),
    profileFacts: Object.fromEntries(PROFILE_FACT_KEYS.map(key => [key, $(`fact-${key}`).value.trim()])),
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

function sameProfile(a, b) {
  const comparable = (value) => typeof value === 'string' ? value.trim() : value;
  return ['profile', 'writingInstructions', 'resumeText'].every((key) => comparable(a[key]) === comparable(b[key])) &&
    PROFILE_FACT_KEYS.every(key => comparable(a.profileFacts?.[key] || '') === comparable(b.profileFacts?.[key] || '')) && sameResume(a.resumeFile, b.resumeFile);
}

function sameConnection(a, b) {
  return ['apiKey', 'model', 'debug'].every(key => a[key] === b[key]);
}

function updateSetup() {
  const banner = $('setup-banner');
  const connected = Boolean(settings.apiKey);
  const hasBackground = Boolean(settings.profile || settings.resumeText || settings.resumeFile || PROFILE_FACT_KEYS.some(key => settings.profileFacts?.[key]));
  banner.classList.toggle('ready', connected && hasBackground);
  banner.hidden = connected && hasBackground;
  $('setup-title').textContent = connected ? 'Make your answers personal' : 'Connect AI to start';
  $('setup-description').textContent = !connected ? 'Add your API key to start writing.' : 'Add your resume or a few profile details.';
  $('setup-action').textContent = connected ? 'Edit profile' : 'AI settings';
}

function updateFooter() {
  $('save-button').disabled = !loaded || importing || pendingWrites > 0;
  if (pendingWrites) $('settings-form').setAttribute('aria-busy', 'true');
  else $('settings-form').removeAttribute('aria-busy');
  if (!loaded) return;
  if (lastError) setStatus('save-status', lastError, 'error');
  else if (pendingWrites) setStatus('save-status', 'Saving…');
  else if ($('panel-profile').hidden ? !sameConnection(collectSettings(), settings) : !sameProfile(collectSettings(), settings)) setStatus('save-status', 'Unsaved changes');
  else setStatus('save-status', 'Saved', 'success');
}

function markChanged() {
  updateToneChips();
  renderResume();
  if (!loaded) return;
  lastError = '';
  if (/^[a-zA-Z0-9._:-]{1,120}$/.test(selectedModel())) $('model').removeAttribute('aria-invalid');
  updateFooter();
}

function renderResume() {
  const hasText = Boolean($('resume-text').value.trim());
  $('resume-attachment').hidden = !resumeFile && !hasText;
  $('resume-file-name').textContent = resumeFile?.name || (hasText ? 'Résumé text added' : '');
}

function renderSettings(view = settings) {
  $('enable-toggle').checked = view.enabled;
  $('api-key').value = view.apiKey;
  renderModel(view.model);
  $('profile').value = view.profile;
  for (const key of PROFILE_FACT_KEYS) $(`fact-${key}`).value = view.profileFacts?.[key] || '';
  $('writing-instructions').value = view.writingInstructions;
  $('resume-text').value = view.resumeText;
  $('debug-toggle').checked = view.debug;
  resumeFile = view.resumeFile;
  renderResume();
  updateToneChips();
}

function showScreen(name, focus = false) {
  const home = name === 'home';
  $('home-screen').hidden = !home;
  $('settings-form').hidden = home;
  for (const panel of ['profile', 'connection']) $(`panel-${panel}`).hidden = name !== panel;
  if (!home) {
    $('save-label').textContent = name === 'profile' ? 'Save profile' : 'Save settings';
    $(`panel-${name}`).querySelector('.form-scroll').scrollTop = 0;
  }
  updateFooter();
  if (focus) (home ? $('open-profile') : $(`back-${name}`)).focus();
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
  const doAll = $('do-all-button');
  pageTabId = null;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  doAll.disabled = true;
  $('page-indicator').classList.add('busy');
  $('page-indicator').classList.remove('ready');
  $('page-status').textContent = rescan ? 'Scanning page…' : 'Checking page…';
  try {
    const tabs = await withTimeout(chrome.tabs.query({ currentWindow: true }));
    const activeTab = tabs.find(item => item.active);
    const ownTab = await withTimeout(chrome.tabs.getCurrent()).catch(() => null);
    const openedAsTab = ownTab?.id === activeTab?.id;
    const candidates = openedAsTab ? tabs.filter(item => item !== activeTab).sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0)) : activeTab ? [activeTab] : [];
    let tab;
    for (const candidate of candidates) {
      try {
        const frame = await withTimeout(chrome.webNavigation.getFrame({ tabId: candidate.id, frameId: 0 }));
        if (/^https?:\/\//i.test(frame?.url || '')) { tab = candidate; break; }
      } catch { /* Extension and browser pages are not scannable. */ }
    }
    if (!tab?.id) {
      $('page-status').textContent = 'Open an application page.';
      return;
    }
    pageTabId = tab.id;
    const status = await withTimeout(chrome.runtime.sendMessage({ type: rescan ? 'WC_SCAN_TAB' : 'WC_GET_TAB_SCAN_STATUS', tabId: tab.id }));
    if (status?.error) throw new Error('Could not scan this page.');
    // Storage is authoritative; the content script may be applying the toggle.
    const enabled = settings.enabled;
    const count = Number.isFinite(status?.count) ? Math.max(0, status.count) : 0;
    setFillButtonMode(status.refill === true);
    $('page-status').textContent = !enabled ? 'AI filling is paused.' : !status.scanned ? 'Scan page to highlight fillable fields.' : status.filling ? 'Writing answers…' : count ? `${count} ${count === 1 ? 'field' : 'fields'} ready to write` : 'No fillable fields found. Try scanning again after the page loads.';
    $('page-indicator').classList.toggle('ready', enabled && status.scanned && count > 0);
    doAll.disabled = !(enabled && status.scanned && count > 0) || status.filling === true;
  } catch {
    $('page-status').textContent = 'Open an application page, or refresh it.';
  } finally {
    button.disabled = !settings.enabled;
    button.removeAttribute('aria-busy');
    $('page-indicator').classList.remove('busy');
  }
}

function validateConnection(draft) {
  if (!/^[a-zA-Z0-9._:-]{1,120}$/.test(draft.model)) {
    $('model').setAttribute('aria-invalid', 'true');
    lastError = draft.model ? 'Use a valid OpenAI model name.' : 'Enter an OpenAI model name.';
    showScreen('connection');
    $('model').focus();
    updateFooter();
    return false;
  }
  $('model').removeAttribute('aria-invalid');
  if (draft.apiKey && (draft.apiKey.length < 8 || /\s/.test(draft.apiKey))) {
    $('api-key').setAttribute('aria-invalid', 'true');
    lastError = 'Paste the full API key without spaces.';
    showScreen('connection');
    $('api-key').focus();
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
      lastError = 'Could not save. Try Save again.';
    } finally {
      pendingWrites -= 1;
      updateSetup();
      updateFooter();
    }
  });
  return writeQueue;
}

function flushSettings(section) {
  if (!loaded || importing) return Promise.resolve();
  const draft = collectSettings();
  if (section === 'connection' && !validateConnection(draft)) return Promise.resolve();
  if (section === 'profile' ? sameProfile(draft, settings) : sameConnection(draft, settings)) { updateFooter(); return Promise.resolve(); }
  lastError = '';
  return enqueueWrite(async () => {
    const latest = await loadSettings();
    settings = await saveSettings(section === 'profile'
      ? { ...latest, profile: draft.profile, profileFacts: draft.profileFacts, writingInstructions: draft.writingInstructions, resumeText: draft.resumeText, resumeFile: draft.resumeFile }
      : { ...latest, apiKey: draft.apiKey, model: draft.model, debug: draft.debug });
    void refreshPage();
  });
}

function save(event) {
  event.preventDefault();
  void flushSettings($('panel-profile').hidden ? 'connection' : 'profile');
}

function persistEnabled() {
  if (!loaded) return;
  const enabled = $('enable-toggle').checked;
  lastError = '';
  void enqueueWrite(async () => {
    const stored = await loadSettings();
    settings = await saveSettings({ ...stored, enabled });
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
  $('profile-import').disabled = value;
  $('resume-upload').disabled = value;
  $('profile-import-button').disabled = value;
  $('resume-upload-button').disabled = value;
  updateFooter();
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
      setStatus('resume-status', 'PDF attached. Save profile to use it in answers.');
    } else if (['txt', 'md'].includes(extension)) {
      if (file.size > MAX_TEXT_BYTES) throw new Error('Text files must be 1 MB or smaller.');
      const text = await file.text();
      if (text.length > MAX_TEXT_LENGTH) throw new Error('Keep résumé text under 100,000 characters.');
      if (text.includes('\0')) throw new Error('Please upload a plain text or Markdown file.');
      $('resume-text').value = text;
      $('resume-editor').open = true;
      resumeFile = null;
      renderResume();
      setStatus('resume-status', 'Résumé added. Save profile to use it in answers.');
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

function fillImportedFacts(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return;
  const facts = value.facts && typeof value.facts === 'object' ? value.facts : value;
  const application = value.application && typeof value.application === 'object' ? value.application : {};
  const text = item => typeof item === 'string' ? item.trim() : '';
  const name = text(facts.full_name) || text(facts.name) || [text(facts.first_name), text(facts.last_name)].filter(Boolean).join(' ');
  const candidates = {
    fullName: name, email: text(facts.email), phone: text(facts.phone),
    location: text(facts.location) || text(facts.city), headline: text(application.headline) || text(facts.headline),
    linkedin: text(facts.linkedin) || text(facts.linkedin_url), portfolio: text(facts.website) || text(facts.portfolio),
  };
  for (const [key, candidate] of Object.entries(candidates)) {
    if (candidate && !$(`fact-${key}`).value.trim()) $(`fact-${key}`).value = candidate.slice(0, 500);
  }
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
    let cleanedProfile = null;
    if (extension === 'json') {
      let parsed;
      try { parsed = JSON.parse(text); } catch { throw new Error('That JSON profile is invalid. Check the file and try again.'); }
      if (!parsed || typeof parsed !== 'object') throw new Error('The JSON profile must contain an object or array.');
      cleanedProfile = scrubProfile(parsed);
      imported = JSON.stringify(cleanedProfile, null, 2);
    }
    if (imported.length > MAX_TEXT_LENGTH) throw new Error('Keep profile text under 100,000 characters.');
    $('profile').value = imported;
    if (cleanedProfile) fillImportedFacts(cleanedProfile);
    markChanged();
    setStatus('profile-import-status', extension === 'json' ? 'Profile imported. File paths and secret fields were excluded. Save profile to keep it.' : 'Profile imported. Save profile to keep it.');
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

$('open-profile').addEventListener('click', () => showScreen('profile', true));
$('open-connection').addEventListener('click', () => showScreen('connection', true));
$('back-profile').addEventListener('click', () => showScreen('home', true));
$('back-connection').addEventListener('click', () => showScreen('home', true));

$('settings-form').addEventListener('submit', save);
$('settings-form').addEventListener('input', (event) => { if (event.target.type !== 'file') markChanged(); });
$('settings-form').addEventListener('change', (event) => { if (event.target.tagName === 'SELECT') markChanged(); });
$('model-preset').addEventListener('change', () => {
  const custom = $('model-preset').value === 'custom';
  if (custom) $('model').value = customModel;
  else if (!$('model-custom').hidden) customModel = $('model').value.trim();
  $('model-custom').hidden = !custom;
  if (custom) $('model').focus();
});
$('model').addEventListener('input', () => { customModel = $('model').value; });
$('enable-toggle').addEventListener('change', persistEnabled);
$('setup-action').addEventListener('click', () => {
  const connected = Boolean(settings.apiKey);
  showScreen(connected ? 'profile' : 'connection', true);
});
$('rescan-button').addEventListener('click', () => refreshPage(true));
$('do-all-button').addEventListener('click', async () => {
  if (!pageTabId) return;
  const button = $('do-all-button');
  const refill = button.dataset.refill === 'true';
  const action = refill ? 'REFILL' : 'FILL';
  let started = false;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  $('page-indicator').classList.add('busy');
  $('page-status').textContent = `Starting ${action}…`;
  try {
    const result = await withTimeout(chrome.runtime.sendMessage({ type: 'WC_FILL_ALL_TAB', tabId: pageTabId, refill }));
    started = result?.started === true;
    $('page-status').textContent = started ? `${action} started on ${result.count} ${result.count === 1 ? 'input' : 'inputs'}. Watch the page for progress.` : result?.error || `Could not start ${action}.`;
  } catch {
    $('page-status').textContent = `Could not start ${action}. Reload the page and try again.`;
  } finally {
    if (!started) button.disabled = false;
    button.removeAttribute('aria-busy');
    $('page-indicator').classList.remove('busy');
  }
});
chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === 'WC_FILL_UI_DONE' && sender.tab?.id === pageTabId) void refreshPage(false);
});
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
    // Older versions stored unsaved edits here. They must not be restored as a profile.
    if (chrome.storage.session) void chrome.storage.session.remove('wcPopupDraft').catch(() => {});
    renderSettings(settings);
    loaded = true;
    showScreen('home');
    updateSetup();
    updateFooter();
  } catch {
    setStatus('save-status', 'Could not load settings. Reopen the extension.', 'error');
  }
  await refreshPage();
}

initialize();
