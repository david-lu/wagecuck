(() => {
  'use strict';
  if (globalThis.__wcInputFiller) return;
  globalThis.__wcInputFiller = true;

  const SELECTOR = 'input, textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"]';
  const TYPES = new Set(['text', 'email', 'tel', 'url', 'number']);
  const records = new Map();
  const roots = new Set([document]);
  let enabled = true;
  let configured = false;
  let serial = 0;
  let scanTimer;
  let positionFrame;
  let host;
  let shadow;
  let buttonLayer;
  let toastLayer;

  const css = `
    :host { all:initial!important; position:fixed!important; inset:0!important; width:100vw!important; height:100vh!important; z-index:2147483646!important; pointer-events:none!important; color-scheme:light!important; }
    *, *::before, *::after { box-sizing:border-box; }
    button { font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; cursor:pointer; }
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
    toastLayer = document.createElement('div');
    toastLayer.className = 'wc-toasts';
    toastLayer.setAttribute('aria-live', 'polite');
    toastLayer.setAttribute('aria-relevant', 'additions text');
    shadow.append(style, buttonLayer, toastLayer);
    document.documentElement.append(host);
    for (const record of records.values()) buttonLayer.append(record.button);
  }

  function readValue(field) {
    return 'value' in field ? field.value : field.innerText;
  }

  function labelFor(field) {
    return globalThis.WCFieldContext.labelFor(field);
  }

  function isVisible(field) {
    if (!field.isConnected || field.closest('[hidden], [inert]')) return false;
    const rect = field.getBoundingClientRect();
    if (rect.width < 60 || rect.height < 18) return false;
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

  function isEligible(field) {
    if (field.getRootNode() === shadow || field.disabled || field.matches(':disabled') || field.readOnly || field.getAttribute('aria-disabled') === 'true' || field.getAttribute('aria-readonly') === 'true') return false;
    if (field.tagName === 'INPUT' && !TYPES.has(field.type)) return false;
    if (field.maxLength === 0) return false;
    if (!['INPUT', 'TEXTAREA'].includes(field.tagName) && !field.isContentEditable) return false;
    if (field.isContentEditable && parentElement(field)?.isContentEditable) return false;
    if (globalThis.WCFieldContext.isSensitiveField(field) || globalThis.WCFieldContext.isSearchField(field)) return false;
    return isVisible(field);
  }

  function fieldInfo(field) {
    return globalThis.WCFieldContext.fieldInfo(field);
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
          records.get(field).button.setAttribute('aria-label', `Fill with AI: ${labelFor(field)}`);
          if (!records.get(field).active) records.get(field).button.title = `Write an answer for ${labelFor(field)}`;
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
        const record = { field, button, id, active: null, starting: false };
        button.addEventListener('click', event => { if (event.isTrusted) startFill(record); });
        records.set(field, record);
        buttonLayer.append(button);
        resizeObserver.observe(field);
      }
    }
    for (const [field, record] of records) {
      if (!fields.has(field)) {
        record.active?.cancel('Field is no longer available.');
        record.button.remove();
        resizeObserver.unobserve(field);
        records.delete(field);
      }
    }
    for (const root of roots) {
      if (root instanceof ShadowRoot && !root.host.isConnected) roots.delete(root);
    }
    schedulePosition();
  }

  function scheduleScan() {
    if (scanTimer) return;
    scanTimer = setTimeout(scan, 120);
  }

  function schedulePosition() {
    if (positionFrame) return;
    positionFrame = requestAnimationFrame(positionButtons);
  }

  function positionButtons() {
    positionFrame = null;
    for (const record of records.values()) {
      const { field, button } = record;
      const rect = field.getBoundingClientRect();
      const compact = rect.width < 240 || rect.height < 36;
      const width = compact ? 45 : 80;
      const height = compact ? 26 : 28;
      const x = rect.right - width - 4;
      // Straddle the top border on roomy fields so the button does not cover text.
      let y = compact ? rect.top + Math.min(5, (rect.height - height) / 2) : rect.top >= 0 ? Math.max(2, rect.top - 12) : rect.top - 12;
      button.dataset.compact = String(compact);
      if (!button.hasAttribute('aria-busy')) {
        const text = compact ? '✦ AI' : readValue(field).trim() ? '✦ Rewrite' : '✦ Write';
        if (button.textContent !== text) button.textContent = text;
      }
      button.style.width = `${width}px`;
      button.style.height = `${height}px`;
      let visible = rect.right > 0 && rect.left < innerWidth && y >= 0 && y + height <= innerHeight && isVisible(field);
      for (let node = parentElement(field); visible && node; node = parentElement(node)) {
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
    field.focus({ preventScroll: true });
    if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
      const prototype = field instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(field, answer);
    } else {
      // innerText inserts safe line breaks; textContent would collapse paragraphs
      // in editors whose whitespace style is the default "normal".
      field.innerText = answer;
    }
    field.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertReplacementText', data: answer }));
    field.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  }

  function logUI(event, requestId, code = '') {
    chrome.runtime.sendMessage({ type: 'WC_LOG_EVENT', event, metadata: { requestId, ...(code ? { code } : {}) } }).catch(() => {});
  }

  async function startFill(record) {
    if (record.active || record.starting || !enabled || !isEligible(record.field)) return;
    record.starting = true;
    try {
      const status = await chrome.runtime.sendMessage({ type: 'WC_GET_STATUS' });
      configured = !!status?.configured;
      enabled = status?.enabled !== false;
      if (!enabled) { setEnabled(false); return; }
    } catch {
      toast('Extension needs a refresh', 'Reload this page after reloading the extension in Chrome.', 'error', [], 14000);
      return;
    } finally {
      record.starting = false;
    }
    if (!records.has(record.field) || record.active || !isEligible(record.field)) return;
    if (!configured) {
      toast('One quick step before you start', 'Connect your OpenAI account using an API key, then add your background.', 'info', [{ label: 'Set up AI', run: () => chrome.runtime.sendMessage({ type: 'WC_OPEN_SETTINGS' }).catch(() => {}) }], 14000);
      return;
    }
    if ([...records.values()].filter(item => item.active).length >= 3) {
      toast('Three answers are already in progress', 'Wait for an answer or cancel one before starting another.', 'info', [], 10000);
      return;
    }
    const field = record.field;
    const original = readValue(field);
    const targetInfo = fieldInfo(field);
    const sourceLocation = location.href;
    const targetSignature = info => JSON.stringify(['label', 'type', 'placeholder', 'context', 'required', 'maxLength', 'min', 'max', 'step', 'pattern'].map(key => info[key]));
    const requestId = crypto.randomUUID();
    const start = Date.now();
    const port = chrome.runtime.connect({ name: 'wc-fill' });
    let finished = false;
    let edited = false;
    let writing = false;
    field.addEventListener('input', onEdit);
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
      field.removeEventListener('input', onEdit);
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
      logUI('ui.cancelled', requestId);
      toast(reason ? 'Answer stopped' : 'Cancelled', reason || 'Your text has not been changed.', 'info', [], 7000);
    }
    record.active = { cancel };
    port.onDisconnect.addListener(() => {
      if (!finished) {
        const message = chrome.runtime.lastError?.message;
        cleanup();
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
        const actions = [{ label: 'Try again', run: element => { element.remove(); startFill(record); } }];
        if (['MISSING_INFORMATION', 'AUTHENTICATION', 'NOT_CONFIGURED', 'ACCESS_DENIED', 'API_REQUEST'].includes(message.code)) actions.push({ label: message.code === 'MISSING_INFORMATION' ? 'Edit profile' : 'Open settings', run: () => chrome.runtime.sendMessage({ type: 'WC_OPEN_SETTINGS' }).catch(() => {}) });
        toast('Couldn’t write this answer', message.message || 'Click Write to try again.', 'error', actions, 20000);
        return;
      }
      if (message.type !== 'result') return;
      cleanup();
      if (!enabled || !isEligible(field)) {
        toast('Field is no longer available', 'The answer was not inserted. Rescan from the extension icon.', 'info', [], 12000);
        return;
      }
      if (edited || readValue(field) !== original) {
        logUI('ui.edit_preserved', requestId);
        toast('Your edits were preserved', 'You changed this field while the AI was writing. Click AI again to use your latest text.', 'info', [], 14000);
        return;
      }
      if (location.href !== sourceLocation || targetSignature(fieldInfo(field)) !== targetSignature(targetInfo)) {
        logUI('ui.edit_preserved', requestId, 'FIELD_CHANGED');
        toast('The question changed', 'The page or field changed while the AI was writing. Click AI again for the current question.', 'info', [], 14000);
        return;
      }
      const answer = typeof message.answer === 'string' ? message.answer.trim() : '';
      if (!answer || (field.maxLength > 0 && answer.length > field.maxLength)) {
        toast('Answer doesn’t fit this field', 'Try again with a shorter answer in your writing preferences.', 'error', [], 14000);
        return;
      }
      if (field.type === 'number' && !Number.isFinite(Number(answer))) {
        toast('A number is needed', 'The AI returned text for a numeric field. Click AI to try again.', 'error', [], 14000);
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
      toast('Couldn’t read this page', 'Reload the page and try again.', 'error', [], 14000);
    }
  }

  function setEnabled(value) {
    enabled = value;
    if (enabled) { scan(); return; }
    for (const { field, button, active } of records.values()) {
      active?.cancel('AI filling was switched off.');
      resizeObserver.unobserve(field);
      button.remove();
    }
    records.clear();
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
    if (enabled && !document.hidden && collectRoots().some(root => !roots.has(root))) scheduleScan();
  }, 2000);
  window.visualViewport?.addEventListener('resize', schedulePosition);
  window.visualViewport?.addEventListener('scroll', schedulePosition);

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type === 'WC_PAGE_CONTEXT') {
      try { respond(collectPage()); } catch { respond(null); }
    } else if (message?.type === 'WC_RESCAN') {
      scan(); respond({ count: records.size, enabled });
    } else if (message?.type === 'WC_GET_PAGE_STATUS') {
      respond({ count: records.size, enabled });
    } else if (message?.type === 'WC_SETTINGS_CHANGED') {
      setEnabled(message.enabled !== false); respond({ count: records.size, enabled });
    }
  });
  chrome.runtime.sendMessage({ type: 'WC_GET_STATUS' }).then(status => {
    configured = !!status?.configured;
    setEnabled(status?.enabled !== false);
  }).catch(() => scan());
})();
