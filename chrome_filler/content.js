(() => {
  'use strict';
  if (globalThis.__wcInputFiller) return;
  globalThis.__wcInputFiller = true;

  const SELECTOR = 'input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="textbox"]';
  const TYPES = new Set(['text', 'email', 'tel', 'url', 'number', 'checkbox', 'radio']);
  const records = new Map();
  const roots = new Set([document]);
  let enabled = true;
  let configured = false;
  let scanned = false;
  let scannedUrl = '';
  let serial = 0;
  let scanTimer;
  let positionFrame;
  let host;
  let shadow;
  let scanButton;
  let doAllButton;
  let batch = null;
  let scanPending = false;
  let buttonLayer;
  let toastLayer;

  const css = `
    :host { all:initial!important; position:fixed!important; inset:0!important; width:100vw!important; height:100vh!important; z-index:2147483646!important; pointer-events:none!important; color-scheme:light!important; }
    *, *::before, *::after { box-sizing:border-box; }
    button { font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; cursor:pointer; }
    .wc-scan-button { all:initial; box-sizing:border-box; position:fixed; right:16px; bottom:16px; z-index:1; display:flex; align-items:center; justify-content:center; min-height:38px; padding:0 15px; border:1px solid #6d28d9; border-radius:10px; background:#7c3aed; color:#fff; box-shadow:0 4px 16px #3b176b55; font:650 13px/1 system-ui,sans-serif; cursor:pointer; pointer-events:auto; }
    .wc-scan-button:hover { background:#6d28d9; }
    .wc-scan-button:focus-visible { outline:3px solid #c4b5fd; outline-offset:3px; }
    .wc-do-all-button { all:initial; box-sizing:border-box; position:fixed; right:16px; bottom:62px; z-index:1; min-height:38px; padding:0 15px; border:1px solid #4c1d95; border-radius:10px; background:#5b21b6; color:#fff; box-shadow:0 4px 16px #3b176b55; font:700 13px/1 system-ui,sans-serif; cursor:pointer; pointer-events:auto; }
    .wc-do-all-button:hover { background:#4c1d95; }
    .wc-do-all-button:focus-visible { outline:3px solid #c4b5fd; outline-offset:3px; }
    .wc-do-all-button[disabled] { opacity:.55; cursor:default; }
    .wc-do-all-button[hidden] { display:none; }
    .wc-field-outline { box-sizing:border-box; position:fixed; border:2px solid #8b5cf6; border-radius:7px; box-shadow:0 0 0 3px #8b5cf633; pointer-events:none; }
    .wc-fill-button { all:initial; box-sizing:border-box; position:fixed; display:flex; align-items:center; justify-content:center; gap:5px; width:80px; height:28px; padding:0 8px; border:1px solid #b6ca83; border-radius:8px; background:#eaf5c7; color:#263415; box-shadow:0 1px 4px #18241418; font:650 12px/1 system-ui,sans-serif; cursor:pointer; pointer-events:auto; transition:background .15s,box-shadow .15s; }
    .wc-fill-button:hover { background:#d9eda1; box-shadow:0 2px 7px #18241430; }
    .wc-fill-button:focus-visible, .wc-toast button:focus-visible { outline:3px solid #586e2e; outline-offset:2px; }
    .wc-fill-button[aria-busy=true] { background:#f4f1e9; border-color:#d5d0c5; }
    .wc-fill-button[data-compact=true] .wc-button-status { display:none; }
    .wc-spinner { width:11px; height:11px; border:2px solid #a0ad87; border-top-color:#263415; border-radius:50%; animation:wc-spin .8s linear infinite; }
    @keyframes wc-spin { to { transform:rotate(360deg); } }
    .wc-toasts { position:fixed; left:16px; bottom:16px; display:flex; flex-direction:column; gap:10px; width:min(360px,calc(100vw - 32px)); max-height:calc(100vh - 40px); overflow:auto; padding:3px; pointer-events:none; }
    .wc-toast { padding:14px 15px; border:1px solid #d8d7cd; border-radius:13px; background:#fcfbf7; color:#292d22; box-shadow:0 8px 35px #18241422; font:13px/1.5 system-ui,sans-serif; pointer-events:auto; animation:wc-in .18s ease-out; }
    .wc-toast[data-kind=error] { border-color:#dfbbb1; background:#fff8f5; }
    .wc-toast[data-kind=success] { border-color:#ccd99c; }
    .wc-toast-title { display:flex; align-items:center; gap:8px; font-weight:650; }
    .wc-toast-detail { margin-top:4px; color:#64695b; overflow-wrap:anywhere; }
    .wc-toast-actions { display:flex; gap:8px; margin-top:10px; }
    .wc-toast button { padding:8px 12px; min-height:34px; border:1px solid #d3d6c8; border-radius:7px; background:#f1f3e9; color:#344124; font-size:12px; font-weight:600; }
    @keyframes wc-in { from { opacity:0; transform:translateY(5px); } to { opacity:1; transform:translateY(0); } }
    @media(prefers-reduced-motion:reduce) { *, *::before { animation:none!important; transition:none!important; } }
  `;

  function ensureUI() {
    if (host?.isConnected) return;
    host = document.createElement('div');
    host.id = 'wc-ai-root';
    // Keep control styles inside a shadow root to avoid ordinary page CSS collisions.
    host.style.cssText = 'all:initial!important;position:fixed!important;inset:0!important;z-index:2147483646!important;pointer-events:none!important;';
    shadow = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = css;
    buttonLayer = document.createElement('div');
    scanButton = document.createElement('button');
    scanButton.type = 'button';
    scanButton.className = 'wc-scan-button';
    scanButton.textContent = 'Scan page';
    scanButton.setAttribute('aria-label', 'Scan page for fillable fields');
    scanButton.addEventListener('click', event => {
      if (!event.isTrusted) return;
      scanPending = true;
      activateScan();
      chrome.runtime.sendMessage({ type: 'WC_SCAN_TAB' }).catch(() => {}).finally(() => {
        scanPending = false;
        if (scanned) scan();
      });
    });
    doAllButton = document.createElement('button');
    doAllButton.type = 'button';
    doAllButton.className = 'wc-do-all-button';
    doAllButton.textContent = 'DO ALL';
    doAllButton.title = 'Fill empty scanned text fields; never submit';
    doAllButton.setAttribute('aria-label', 'DO ALL: fill empty scanned fields');
    doAllButton.hidden = true;
    doAllButton.addEventListener('click', event => {
      if (!event.isTrusted || doAllButton.disabled) return;
      chrome.runtime.sendMessage({ type: 'WC_FILL_ALL_TAB' }).then(result => {
        if (result?.error) toast('Couldn’t start DO ALL', result.error, 'error', [], 12000);
      }).catch(() => toast('Couldn’t start DO ALL', 'Reload the page and try again.', 'error', [], 12000));
    });
    toastLayer = document.createElement('div');
    toastLayer.className = 'wc-toasts';
    toastLayer.setAttribute('aria-live', 'polite');
    toastLayer.setAttribute('aria-relevant', 'additions text');
    shadow.append(style, buttonLayer);
    if (window.top === window) shadow.append(scanButton, doAllButton);
    shadow.append(toastLayer);
    document.documentElement.append(host);
    for (const record of records.values()) buttonLayer.append(record.outline, record.button);
  }

  function readValue(field) {
    if (field instanceof HTMLInputElement && field.type === 'checkbox') return field.checked;
    if (field instanceof HTMLInputElement && field.type === 'radio') return globalThis.WCFieldContext.radioGroup(field).find(option => option.checked)?.value || '';
    return 'value' in field ? field.value : field.innerText;
  }

  function hasAnswer(field) {
    const value = readValue(field);
    return typeof value === 'boolean' ? value : Boolean(String(value).trim());
  }

  function selectableOption(field, value) {
    if (field instanceof HTMLInputElement && field.type === 'radio') return globalThis.WCFieldContext.radioOptions(field).some(option => option.value === value);
    return field instanceof HTMLSelectElement && [...field.options].some(option => option.value === value && value.trim() && !option.disabled && !option.hidden && !option.closest('optgroup[disabled]'));
  }

  function normalizedAnswer(field, answer) {
    if (field instanceof HTMLInputElement && field.type === 'checkbox') return typeof answer === 'boolean' ? answer : null;
    if (field instanceof HTMLSelectElement || field instanceof HTMLInputElement && field.type === 'radio') return typeof answer === 'string' && selectableOption(field, answer) ? answer : null;
    if (typeof answer !== 'string') return null;
    const value = answer.trim();
    if (!value || (field.maxLength > 0 && value.length > field.maxLength) || (field.type === 'number' && !Number.isFinite(Number(value)))) return null;
    return value;
  }

  function labelFor(field) {
    return globalThis.WCFieldContext.labelFor(field);
  }

  function isVisible(field) {
    if (!field.isConnected || field.closest('[hidden], [inert]')) return false;
    const rect = field.getBoundingClientRect();
    const minSize = field instanceof HTMLInputElement && ['checkbox', 'radio'].includes(field.type) ? 10 : 18;
    if (rect.width < minSize || rect.height < minSize || (minSize !== 10 && rect.width < 60)) return false;
    for (let node = field; node instanceof Element; node = parentElement(node)) {
      if (node.matches('[hidden], [inert]')) return false;
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || style.opacity === '0') return false;
    }
    return true;
  }

  function parentElement(node) {
    return node.parentElement || (node.getRootNode() instanceof ShadowRoot ? node.getRootNode().host : null);
  }

  function visualTarget(field) {
    return field.type === 'radio' ? globalThis.WCFieldContext.radioDetails(field).container || field : field;
  }

  function isEligible(field) {
    if (field.getRootNode() === shadow || field.disabled || field.matches(':disabled') || field.readOnly || field.getAttribute('aria-disabled') === 'true' || field.getAttribute('aria-readonly') === 'true') return false;
    if (field.tagName === 'INPUT' && !TYPES.has(field.type)) return false;
    if (field.matches('[role="combobox"],[aria-haspopup="listbox"]')) return false;
    if (field.tagName === 'SELECT' && (field.multiple || field.size > 1 || field.options.length > 100 || ![...field.options].some(option => selectableOption(field, option.value)))) return false;
    if (field.type === 'radio') {
      const options = globalThis.WCFieldContext.radioOptions(field);
      if (options.length < 2 || options.length > 100 || new Set(options.map(option => option.value)).size !== options.length || options[0] !== field || !globalThis.WCFieldContext.radioDetails(field).label) return false;
    }
    if (field.tagName !== 'SELECT' && !['checkbox', 'radio'].includes(field.type) && field.maxLength === 0) return false;
    if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(field.tagName) && !field.isContentEditable) return false;
    if (field.isContentEditable && parentElement(field)?.isContentEditable) return false;
    if (globalThis.WCFieldContext.isSensitiveField(field) || globalThis.WCFieldContext.isSearchField(field) || globalThis.WCFieldContext.isConsentField(field)) return false;
    return isVisible(field);
  }

  function fieldInfo(field) {
    return globalThis.WCFieldContext.fieldInfo(field);
  }

  function targetSignature(info) {
    return JSON.stringify(['label', 'type', 'placeholder', 'context', 'required', 'maxLength', 'min', 'max', 'step', 'pattern', 'options'].map(key => info[key]));
  }

  function collectRoots(root = document, found = []) {
    found.push(root);
    for (const element of root.querySelectorAll('*')) {
      if (element === host) continue;
      if (element.shadowRoot) collectRoots(element.shadowRoot, found);
    }
    return found;
  }

  function collectPage() {
    // Read all rendered document text, including offscreen sections and open shadow roots.
    // Exclude form values, scripts, hidden text and our extension controls.
    const parts = [];
    const visibleCache = new WeakMap();
    function textVisible(node) {
      if (visibleCache.has(node)) return visibleCache.get(node);
      const style = getComputedStyle(node);
      const visible = node !== host && !node.matches('script,style,noscript,template,input,textarea,select,[contenteditable],[hidden],[inert]') && style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse' && style.opacity !== '0' && (!parentElement(node) || textVisible(parentElement(node)));
      visibleCache.set(node, visible);
      return visible;
    }
    const allRoots = collectRoots();
    for (const root of allRoots) {
      const walker = document.createTreeWalker(root === document ? document.body || document.documentElement : root, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const text = node.textContent.replace(/\s+/g, ' ').trim();
        const parent = parentElement(node);
        if (text && parent && textVisible(parent)) parts.push(text);
      }
    }
    const fields = allRoots.flatMap(root => [...root.querySelectorAll(SELECTOR)]).filter(isEligible).map(field => {
      const { currentValue, ...info } = fieldInfo(field);
      return info;
    });
    const sourceUrl = /^https?:/.test(location.href) ? location.href : document.referrer || location.origin;
    const url = new URL(sourceUrl);
    url.search = '';
    url.hash = '';
    return { title: document.title, url: url.href, text: parts.join('\n'), fields };
  }

  function scan() {
    clearTimeout(scanTimer);
    scanTimer = null;
    if (!enabled) return;
    ensureUI();
    if (scanned && scannedUrl !== location.href) resetScan('The page changed. Scan it again to show fillable fields.');
    if (!scanned) return;
    const fields = new Set();
    for (const root of collectRoots()) {
      if (!roots.has(root)) {
        roots.add(root);
        observer.observe(root, observationOptions);
      }
      for (const field of root.querySelectorAll(SELECTOR)) {
        if (!isEligible(field)) continue;
        fields.add(field);
        if (records.has(field)) {
          const record = records.get(field);
          const visual = visualTarget(field);
          if (record.visual !== visual) {
            resizeObserver.unobserve(record.visual);
            record.visual = visual;
            resizeObserver.observe(visual);
          }
          record.button.setAttribute('aria-label', `Fill with AI: ${labelFor(field)}`);
          if (!record.active) record.button.title = `Write an answer for ${labelFor(field)}`;
          continue;
        }
        const button = document.createElement('button');
        const id = `wc-field-${++serial}`;
        button.type = 'button';
        button.className = 'wc-fill-button';
        button.dataset.wcField = id;
        button.textContent = '✦ Write';
        button.title = `Write an answer for ${labelFor(field)}`;
        button.setAttribute('aria-label', `Fill with AI: ${labelFor(field)}`);
        const outline = document.createElement('div');
        outline.className = 'wc-field-outline';
        outline.setAttribute('aria-hidden', 'true');
        const record = { field, visual: visualTarget(field), button, outline, id, active: null, starting: false };
        button.addEventListener('click', event => { if (event.isTrusted) startFill(record); });
        records.set(field, record);
        buttonLayer.append(outline, button);
        resizeObserver.observe(record.visual);
      }
    }
    for (const [field, record] of records) {
      if (!fields.has(field)) {
        record.active?.cancel('Field is no longer available.');
        record.button.remove();
        record.outline.remove();
        resizeObserver.unobserve(record.visual);
        records.delete(field);
      }
    }
    for (const root of roots) {
      if (root instanceof ShadowRoot && !root.host.isConnected) roots.delete(root);
    }
    if (doAllButton) {
      doAllButton.hidden = records.size === 0;
      doAllButton.disabled = !!batch || scanPending;
    }
    schedulePosition();
  }

  function scheduleScan() {
    if (scanTimer) return;
    scanTimer = setTimeout(scan, 120);
  }

  function resetScan(reason = '') {
    scanned = false;
    scannedUrl = '';
    if (batch) {
      const ending = batch;
      batch = null;
      ending.progress?.element.remove();
      clearInterval(ending.heartbeat);
      ending.keepalive?.disconnect();
      chrome.runtime.sendMessage({ type: 'WC_STOP_ALL_TAB' }).catch(() => {});
    }
    scanPending = false;
    if (doAllButton) doAllButton.hidden = true;
    for (const [field, record] of records) {
      record.active?.cancel(reason || 'The page scan ended.');
      resizeObserver.unobserve(record.visual);
      record.button.remove();
      record.outline.remove();
    }
    records.clear();
  }

  function activateScan() {
    if (!enabled) return { count: 0, scanned: false, enabled };
    scanned = true;
    scannedUrl = location.href;
    scan();
    return { count: records.size, scanned, enabled };
  }

  function schedulePosition() {
    if (positionFrame) return;
    positionFrame = requestAnimationFrame(positionButtons);
  }

  function positionButtons() {
    positionFrame = null;
    for (const record of records.values()) {
      const { field, visual, button, outline } = record;
      const rect = visual.getBoundingClientRect();
      const outlineVisible = rect.right > 0 && rect.left < innerWidth && rect.bottom > 0 && rect.top < innerHeight && isVisible(visual) && isVisible(field);
      outline.style.display = outlineVisible ? 'block' : 'none';
      outline.style.left = `${rect.left}px`;
      outline.style.top = `${rect.top}px`;
      outline.style.width = `${rect.width}px`;
      outline.style.height = `${rect.height}px`;
      const choice = field.tagName === 'SELECT' || ['checkbox', 'radio'].includes(field.type);
      const compact = !choice && (rect.width < 240 || rect.height < 36);
      const width = compact ? 45 : 80;
      const height = compact ? 26 : 28;
      const x = choice ? rect.right + 8 : rect.right - width - 4;
      // Straddle the top border on roomy fields so the button does not cover text.
      let y = field.type === 'radio' ? Math.max(2, rect.top + 8) : choice ? Math.max(2, rect.top + (rect.height - height) / 2) : compact ? rect.top + Math.min(5, (rect.height - height) / 2) : rect.top >= 0 ? Math.max(2, rect.top - 12) : rect.top - 12;
      button.dataset.compact = String(compact);
      if (!button.hasAttribute('aria-busy')) {
        const text = compact ? '✦ AI' : hasAnswer(field) ? '✦ Rewrite' : '✦ Write';
        if (button.textContent !== text) button.textContent = text;
      }
      button.style.width = `${width}px`;
      button.style.height = `${height}px`;
      let visible = rect.right > 0 && rect.left < innerWidth && y >= 0 && y + height <= innerHeight && isVisible(field);
      for (let node = parentElement(field); visible && node; node = parentElement(node)) {
        // The document's scrollport is the viewport, even when BODY has overflow:auto
        // and its layout rect has scrolled above the visible page.
        if (node === document.body || node === document.documentElement) continue;
        const style = getComputedStyle(node);
        if (/(auto|scroll|hidden|clip)/.test(style.overflow + style.overflowX + style.overflowY)) {
          const clip = node.getBoundingClientRect();
          if (!compact && y < clip.top && rect.top >= clip.top) y = rect.top + 4;
          if (x < clip.left || x + width > clip.right || y < clip.top || y + height > clip.bottom) visible = false;
        }
      }
      button.style.display = visible ? 'flex' : 'none';
      button.style.left = `${Math.max(2, Math.min(innerWidth - width - 2, x))}px`;
      button.style.top = `${y}px`;
    }
  }

  function toast(title, detail = '', kind = 'info', actions = [], duration = 0) {
    ensureUI();
    const element = document.createElement('div');
    element.className = 'wc-toast';
    element.dataset.kind = kind;
    element.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    const heading = document.createElement('div');
    heading.className = 'wc-toast-title';
    const titleNode = document.createElement('span');
    titleNode.textContent = title;
    heading.append(titleNode);
    const description = document.createElement('div');
    description.className = 'wc-toast-detail';
    description.textContent = detail;
    element.append(heading, description);
    const controls = document.createElement('div');
    controls.className = 'wc-toast-actions';
    for (const action of actions) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = action.label;
      button.addEventListener('click', event => { if (event.isTrusted) action.run(element); });
      controls.append(button);
    }
    if (duration) {
      const close = document.createElement('button');
      close.type = 'button';
      close.textContent = 'Dismiss';
      close.addEventListener('click', event => { if (event.isTrusted) element.remove(); });
      controls.append(close);
      setTimeout(() => element.remove(), duration);
    }
    element.append(controls);
    toastLayer.append(element);
    return { element, title: titleNode, detail: description };
  }

  function writeValue(field, answer) {
    let target = field;
    if (field instanceof HTMLInputElement && field.type === 'radio') {
      const group = globalThis.WCFieldContext.radioGroup(field);
      target = answer ? group.find(option => option.value === answer) : group.find(option => option.checked);
      if (!target) return;
      target.focus({ preventScroll: true });
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set.call(target, Boolean(answer));
    } else if (field instanceof HTMLInputElement && field.type === 'checkbox') {
      field.focus({ preventScroll: true });
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set.call(field, answer);
    } else if (field instanceof HTMLSelectElement) {
      field.focus({ preventScroll: true });
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(field, answer);
    } else if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
      field.focus({ preventScroll: true });
      const prototype = field instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(field, answer);
    } else {
      field.focus({ preventScroll: true });
      // innerText inserts safe line breaks; textContent would collapse paragraphs
      // in editors whose whitespace style is the default "normal".
      field.innerText = answer;
    }
    target.dispatchEvent(['checkbox', 'radio'].includes(field.type) || field.tagName === 'SELECT'
      ? new Event('input', { bubbles: true, composed: true })
      : new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertReplacementText', data: answer }));
    target.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  }

  function logUI(event, requestId, code = '') {
    chrome.runtime.sendMessage({ type: 'WC_LOG_EVENT', event, metadata: { requestId, ...(code ? { code } : {}) } }).catch(() => {});
  }

  async function startFill(record) {
    if (!scanned || scannedUrl !== location.href || record.active || record.starting || !enabled || !isEligible(record.field)) return;
    record.starting = true;
    try {
      const status = await chrome.runtime.sendMessage({ type: 'WC_GET_STATUS' });
      configured = !!status?.configured;
      enabled = status?.enabled !== false;
      if (!enabled) { setEnabled(false); return { status: 'failed', code: 'DISABLED' }; }
    } catch {
      toast('Extension needs a refresh', 'Reload this page after reloading the extension in Chrome.', 'error', [], 14000);
      return { status: 'failed', code: 'CONNECTION_INTERRUPTED' };
    } finally {
      record.starting = false;
    }
    if (!scanned || scannedUrl !== location.href || !records.has(record.field) || record.active || !isEligible(record.field)) return;
    if (!configured) {
      toast('One quick step before you start', 'Connect your OpenAI account using an API key, then add your background.', 'info', [{ label: 'Set up AI', run: () => chrome.runtime.sendMessage({ type: 'WC_OPEN_SETTINGS' }).catch(() => {}) }], 14000);
      return { status: 'failed', code: 'NOT_CONFIGURED' };
    }
    if ([...records.values()].filter(item => item.active).length >= 3) {
      toast('Three answers are already in progress', 'Wait for an answer or cancel one before starting another.', 'info', [], 10000);
      return { status: 'failed', code: 'BUSY' };
    }
    const field = record.field;
    const original = readValue(field);
    const targetInfo = fieldInfo(field);
    const sourceLocation = location.href;
    const requestId = crypto.randomUUID();
    const start = Date.now();
    const port = chrome.runtime.connect({ name: 'wc-fill' });
    let finished = false;
    let settled = false;
    let resolveCompletion;
    const completion = new Promise(resolve => { resolveCompletion = resolve; });
    function settle(result) {
      if (settled) return;
      settled = true;
      resolveCompletion(result);
    }
    let edited = false;
    let writing = false;
    const editTargets = field.type === 'radio' ? globalThis.WCFieldContext.radioGroup(field) : [field];
    editTargets.forEach(target => target.addEventListener('input', onEdit));
    function onEdit() { edited = true; }
    const waiting = toast('Writing your answer', `${labelFor(field)} · You can keep typing; your edits will be preserved.`, 'info', [{ label: 'Cancel', run: () => cancel() }]);
    record.button.setAttribute('aria-busy', 'true');
    record.button.textContent = '';
    const spinner = document.createElement('span');
    spinner.className = 'wc-spinner';
    record.button.append(spinner);
    const workingLabel = document.createElement('span');
    workingLabel.className = 'wc-button-status';
    workingLabel.textContent = 'Writing';
    record.button.append(workingLabel);
    record.button.title = 'Writing an answer…';
    const interval = setInterval(() => {
      waiting.title.textContent = `${writing ? 'Writing your answer' : 'Thinking about your answer'} · ${Math.floor((Date.now() - start) / 1000)}s`;
    }, 1000);
    const timeout = setTimeout(() => cancel('The answer took too long. Please try again.'), 130000);
    function cleanup() {
      if (finished) return false;
      finished = true;
      clearInterval(interval);
      clearTimeout(timeout);
      waiting.element.remove();
      editTargets.forEach(target => target.removeEventListener('input', onEdit));
      record.active = null;
      record.button.removeAttribute('aria-busy');
      record.button.textContent = '✦ Write';
      record.button.title = `Write an answer for ${labelFor(field)}`;
      schedulePosition();
      port.disconnect();
      return true;
    }
    function cancel(reason = '') {
      if (finished) return;
      try { port.postMessage({ type: 'cancel', requestId }); } catch { /* Already disconnected. */ }
      cleanup();
      settle({ status: 'cancelled' });
      logUI('ui.cancelled', requestId);
      toast(reason ? 'Answer stopped' : 'Cancelled', reason || 'Your text has not been changed.', 'info', [], 7000);
    }
    record.active = { cancel };
    port.onDisconnect.addListener(() => {
      if (!finished) {
        const message = chrome.runtime.lastError?.message;
        cleanup();
        settle({ status: 'failed', code: 'CONNECTION_INTERRUPTED' });
        toast('Connection interrupted', message ? 'Reload the page and try again.' : 'The AI session stopped. Click AI to try again.', 'error', [], 14000);
      }
    });
    port.onMessage.addListener(message => {
      if (finished || message.requestId !== requestId) return;
      if (message.type === 'state') {
        writing = message.state === 'writing';
        waiting.title.textContent = writing ? 'Writing your answer' : 'Thinking about your answer';
        return;
      }
      if (message.type === 'error') {
        cleanup();
        settle({ status: 'failed', code: message.code });
        const actions = [{ label: 'Try again', run: element => { element.remove(); startFill(record); } }];
        if (['MISSING_INFORMATION', 'AUTHENTICATION', 'NOT_CONFIGURED', 'ACCESS_DENIED', 'API_REQUEST'].includes(message.code)) actions.push({ label: message.code === 'MISSING_INFORMATION' ? 'Edit profile' : 'Open settings', run: () => chrome.runtime.sendMessage({ type: 'WC_OPEN_SETTINGS' }).catch(() => {}) });
        toast('Couldn’t write this answer', message.message || 'Click Write to try again.', 'error', actions, 20000);
        return;
      }
      if (message.type !== 'result') return;
      cleanup();
      if (!enabled || !isEligible(field)) {
        settle({ status: 'skipped' });
        toast('Field is no longer available', 'The answer was not inserted. Rescan from the extension icon.', 'info', [], 12000);
        return;
      }
      if (edited || readValue(field) !== original) {
        settle({ status: 'skipped' });
        logUI('ui.edit_preserved', requestId);
        toast('Your edits were preserved', 'You changed this field while the AI was writing. Click AI again to use your latest text.', 'info', [], 14000);
        return;
      }
      if (location.href !== sourceLocation || targetSignature(fieldInfo(field)) !== targetSignature(targetInfo)) {
        settle({ status: 'skipped' });
        logUI('ui.edit_preserved', requestId, 'FIELD_CHANGED');
        toast('The question changed', 'The page or field changed while the AI was writing. Click AI again for the current question.', 'info', [], 14000);
        return;
      }
      const answer = normalizedAnswer(field, message.answer);
      if (answer === null) {
        settle({ status: 'failed' });
        toast('Answer does not fit this field', 'The generated choice or format is no longer available. Scan again and retry.', 'error', [], 14000);
        return;
      }
      if ((['checkbox', 'radio'].includes(field.type) || field.tagName === 'SELECT') && answer === original) {
        settle({ status: 'skipped' });
        toast('No change needed', field.type === 'checkbox' ? 'The answer is to leave this box as it is.' : 'This option is already selected.', 'info', [], 10000);
        return;
      }
      try {
        writeValue(field, answer);
        if (readValue(field) !== answer) throw new Error('The website did not keep the answer.');
        if (field.validity && !field.validity.valid) {
          writeValue(field, original);
          throw new Error('The answer did not meet the field’s format. Your previous text was restored.');
        }
        logUI('ui.filled', requestId);
        settle({ status: 'filled' });
        toast('Answer filled', 'Review the answer before submitting your form.', 'success', [{ label: 'Undo', run: element => {
          if (field.isConnected && readValue(field) === answer) {
            writeValue(field, original);
            logUI('ui.undo', requestId);
            element.remove();
            toast('Undone', 'Your previous text has been restored.', 'info', [], 5000);
          } else {
            logUI('ui.edit_preserved', requestId, 'UNDO_CONFLICT');
            element.remove();
            toast('Your edits were preserved', 'The field changed after filling, so undo did not replace your edits.', 'info', [], 10000);
          }
        } }], 25000);
      } catch (error) {
        settle({ status: 'failed' });
        logUI('ui.insert_failed', requestId, 'INSERT_FAILED');
        toast('Couldn’t insert the answer', error.message, 'error', [], 14000);
      }
    });
    try {
      waiting.detail.textContent = `Reading the full page for “${labelFor(field)}”…`;
      port.postMessage({ type: 'generate', requestId, field: targetInfo, page: collectPage() });
      waiting.detail.textContent = `${labelFor(field)} · Your edits will be preserved.`;
    } catch {
      cleanup();
      settle({ status: 'failed' });
      toast('Couldn’t read this page', 'Reload the page and try again.', 'error', [], 14000);
    }
    return completion;
  }

  function batchSnapshot() {
    if (!enabled || !scanned || scannedUrl !== location.href) return { scanned: false, enabled };
    scan();
    return {
      scanned: true, enabled, page: collectPage(),
      targets: [...records.values()].filter(record => !record.active && !record.starting && isEligible(record.field) && !hasAnswer(record.field))
        .map(record => ({ id: record.id, field: fieldInfo(record.field) })),
    };
  }

  function beginBatch(message) {
    if (!enabled || !scanned || scannedUrl !== location.href || batch) return { started: false };
    const targets = new Map();
    for (const target of message.targets || []) {
      const record = [...records.values()].find(item => item.id === target.id);
      if (record) targets.set(target.id, { record, signature: targetSignature(target.field) });
    }
    batch = { id: message.runId, sourceUrl: location.href, targets, progress: null };
    if (window.top === window) {
      batch.progress = toast('DO ALL', `Writing ${message.count} ${message.count === 1 ? 'answer' : 'answers'} in one agent run...`, 'info', [{ label: 'Stop', run: () => {
        chrome.runtime.sendMessage({ type: 'WC_STOP_ALL_TAB' }).catch(() => {});
        if (batch?.progress) batch.progress.detail.textContent = 'Stopping...';
      } }]);
      const keepalive = chrome.runtime.connect({ name: 'wc-batch' });
      batch.keepalive = keepalive;
      const heartbeat = () => { try { keepalive.postMessage({ type: 'heartbeat', runId: message.runId }); } catch { /* The worker has disconnected. */ } };
      batch.heartbeat = setInterval(heartbeat, 20_000);
      keepalive.onDisconnect.addListener(() => {
        if (batch?.id === message.runId) finishBatch({ runId: message.runId, stopped: true, errorMessage: 'The extension connection was interrupted. Reload the page and try again.' });
      });
      heartbeat();
    }
    if (doAllButton) doAllButton.disabled = true;
    return { started: true };
  }

  function applyBatch(message) {
    if (!batch || batch.id !== message.runId) return { filled: 0, skipped: message.answers?.length || 0, failed: 0, unchecked: 0 };
    const result = { filled: 0, skipped: 0, failed: 0, unchecked: 0, firstError: '' };
    for (const item of message.answers || []) {
      const target = batch.targets.get(item.fieldId);
      const record = target?.record;
      const field = record?.field;
      if (!record || records.get(field) !== record || !enabled || !scanned || batch.sourceUrl !== location.href || record.active || record.starting || !isEligible(field) || hasAnswer(field) || targetSignature(fieldInfo(field)) !== target.signature) {
        result.skipped++;
        continue;
      }
      if (item.error) {
        result.failed++;
        result.firstError ||= item.error.message || 'The agent could not answer a field.';
        continue;
      }
      const answer = normalizedAnswer(field, item.answer);
      if (answer === null) {
        result.failed++;
        result.firstError ||= 'An answer did not match its field or available choices.';
        continue;
      }
      if (answer === readValue(field) && (['checkbox', 'radio'].includes(field.type) || field.tagName === 'SELECT')) {
        if (field.type === 'checkbox' && answer === false) result.unchecked++;
        else result.skipped++;
        continue;
      }
      const original = readValue(field);
      try {
        writeValue(field, answer);
        if (readValue(field) !== answer || (field.validity && !field.validity.valid)) throw new Error('The page rejected an answer.');
        result.filled++;
        logUI('ui.filled', batch.id);
      } catch {
        try { writeValue(field, original); } catch { /* The page may have removed the field. */ }
        result.failed++;
        result.firstError ||= 'The page rejected an answer.';
        logUI('ui.insert_failed', batch.id, 'INSERT_FAILED');
      }
    }
    schedulePosition();
    return result;
  }

  function finishBatch(message) {
    if (!batch || batch.id !== message.runId) return { finished: false };
    const ending = batch;
    batch = null;
    ending.progress?.element.remove();
    clearInterval(ending.heartbeat);
    ending.keepalive?.disconnect();
    if (doAllButton) doAllButton.disabled = false;
    if (window.top === window) {
      const { filled = 0, failed = 0, skipped = 0, unchecked = 0 } = message.summary || {};
      const detail = message.errorMessage || `${filled} filled${unchecked ? `, ${unchecked} left unchecked` : ''}${failed ? `, ${failed} could not be filled` : ''}${skipped ? `, ${skipped} skipped` : ''}. ${message.fieldError ? `${message.fieldError} ` : ''}Review the answers before submitting.`;
      toast(message.stopped ? 'DO ALL stopped' : message.errorMessage ? 'DO ALL failed' : 'DO ALL finished', detail, message.errorMessage ? 'error' : filled ? 'success' : 'info', [], 20000);
    }
    return { finished: true };
  }

  function setEnabled(value) {
    enabled = value;
    if (enabled) { scan(); return; }
    resetScan('AI filling was switched off.');
    host?.remove();
  }

  const observationOptions = { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['type', 'disabled', 'readonly', 'hidden', 'inert', 'class', 'style', 'name', 'id', 'for', 'title', 'placeholder', 'maxlength', 'min', 'max', 'step', 'pattern', 'required', 'autocomplete', 'aria-label', 'aria-labelledby', 'aria-describedby', 'aria-description', 'aria-required', 'aria-disabled', 'aria-readonly', 'contenteditable'] };
  const observer = new MutationObserver(mutations => {
    if (mutations.every(change => change.target === host || host?.contains(change.target))) return;
    scheduleScan();
  });
  const resizeObserver = new ResizeObserver(schedulePosition);
  observer.observe(document.documentElement, observationOptions);
  window.addEventListener('scroll', schedulePosition, { capture: true, passive: true });
  window.addEventListener('resize', () => { scheduleScan(); schedulePosition(); }, { passive: true });
  document.addEventListener('focusin', () => { schedulePosition(); scheduleScan(); }, { passive: true });
  document.addEventListener('input', schedulePosition, { passive: true });
  window.addEventListener('load', scheduleScan, { once: true });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) scheduleScan(); });
  // Attaching a shadow root to an existing host does not mutate the document.
  // Discover these late-loaded components without repeatedly rescanning every field.
  setInterval(() => {
    if (enabled && scanned && location.href !== scannedUrl) resetScan('The page changed. Scan it again to show fillable fields.');
    if (enabled && scanned && !document.hidden && collectRoots().some(root => !roots.has(root))) scheduleScan();
  }, 2000);
  window.visualViewport?.addEventListener('resize', schedulePosition);
  window.visualViewport?.addEventListener('scroll', schedulePosition);

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type === 'WC_PAGE_CONTEXT') {
      try { respond(collectPage()); } catch { respond(null); }
    } else if (message?.type === 'WC_RESCAN') {
      respond(activateScan());
    } else if (message?.type === 'WC_GET_PAGE_STATUS') {
      if (scanned && scannedUrl !== location.href) resetScan('The page changed. Scan it again to show fillable fields.');
      respond({ count: records.size, scanned, enabled });
    } else if (message?.type === 'WC_BATCH_SNAPSHOT') {
      try { respond(batchSnapshot()); } catch { respond({ scanned: false, enabled }); }
    } else if (message?.type === 'WC_BATCH_BEGIN') {
      respond(beginBatch(message));
    } else if (message?.type === 'WC_BATCH_APPLY') {
      respond(applyBatch(message));
    } else if (message?.type === 'WC_BATCH_DONE') {
      respond(finishBatch(message));
    } else if (message?.type === 'WC_SETTINGS_CHANGED') {
      setEnabled(message.enabled !== false); respond({ count: records.size, scanned, enabled });
    }
  });
  chrome.runtime.sendMessage({ type: 'WC_GET_STATUS' }).then(status => {
    configured = !!status?.configured;
    setEnabled(status?.enabled !== false);
  }).catch(() => scan());
})();
