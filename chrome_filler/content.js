(() => {
  'use strict';
  if (globalThis.__wcInputFiller) return;
  globalThis.__wcInputFiller = true;

  const SELECTOR = 'input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="combobox"], [role="radio"], [role="checkbox"], [role="switch"]';
  const TYPES = new Set(['text', 'email', 'tel', 'url', 'number', 'checkbox', 'radio', 'file']);
  const records = new Map();
  const highlighted = new Map();
  const HIGHLIGHT_STYLES = [['outline', '2px solid #8b5cf6'], ['outline-offset', '2px']];
  const comboboxOptions = new WeakMap();
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
  let panelStatus;
  let panelState;
  let panelCount;
  let controlPanel;
  let scannedInputCount = null;
  let localScannedCount = 0;
  let batch = null;
  let batchPending = false;
  let batchFeedback = '';
  let batchFeedbackTimer;
  let scanPending = false;
  let buttonLayer;
  let toastLayer;
  let panelCheckFrame;

  const css = `
    :host { all:initial!important; display:block!important; position:fixed!important; inset:0!important; width:100vw!important; height:100vh!important; z-index:2147483647!important; pointer-events:none!important; color-scheme:light!important; }
    :host([popover]:not(:popover-open)) { display:none!important; }
    *, *::before, *::after { box-sizing:border-box; }
    button { font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; cursor:pointer; }
    .wc-control-panel { position:fixed; right:16px; bottom:16px; z-index:1; width:252px; padding:11px; border:2px solid #e31800; border-radius:15px; background:linear-gradient(145deg,#fffdf4,#fff3cf); box-shadow:0 10px 30px #54150038,0 2px 5px #54150018; color:#4b1708; font:12px/1.3 system-ui,sans-serif; pointer-events:auto; }
    .wc-panel-head { display:flex; align-items:center; gap:9px; margin-bottom:11px; }
    .wc-panel-logo { display:block; width:36px; height:36px; flex:none; border-radius:9px; box-shadow:0 1px 4px #54150022; }
    .wc-panel-copy { min-width:0; }
    .wc-panel-copy strong { display:block; color:#b6200e; font-size:14px; line-height:1.1; letter-spacing:-.2px; }
    .wc-panel-status-line { display:flex; align-items:center; gap:6px; margin-top:4px; }
    .wc-panel-state { width:7px; height:7px; flex:none; border-radius:50%; background:#aaa096; }
    .wc-panel-state[data-state=ready] { background:#e9a900; box-shadow:0 0 0 3px #ffeaaa; }
    .wc-panel-state[data-state=busy] { background:#e31800; box-shadow:0 0 0 3px #ffd8c9; }
    .wc-panel-status { display:block; color:#754633; font-size:10px; }
    .wc-panel-count { display:grid; place-items:center; min-width:34px; height:34px; margin-left:auto; padding:0 7px; flex:none; border:1px solid #d7a500; border-radius:10px; background:#ffcc00; color:#571808; font:800 15px/1 system-ui,sans-serif; }
    .wc-panel-count[hidden] { display:none; }
    .wc-panel-actions { display:flex; gap:7px; }
    .wc-scan-button,.wc-do-all-button { all:initial; box-sizing:border-box; display:flex; flex:1; align-items:center; justify-content:center; gap:7px; min-width:0; min-height:38px; padding:0 8px; border-radius:9px; font:750 12px/1 system-ui,sans-serif; text-align:center; cursor:pointer; transition:background .15s,box-shadow .15s; }
    .wc-scan-button { border:1px solid #bd210f; background:#e31800; color:#fff; box-shadow:0 2px 5px #e3180033; }
    .wc-scan-button:hover { background:#bd210f; }
    .wc-scan-button:focus-visible,.wc-do-all-button:focus-visible { outline:3px solid #ffcc00; outline-offset:2px; }
    .wc-scan-button[disabled] { opacity:.72; cursor:progress; }
    .wc-do-all-button { border:1px solid #d7a500; background:#ffcc00; color:#4b1708; box-shadow:0 2px 5px #ae720033; }
    .wc-do-all-button:hover { background:#efbb00; }
    .wc-do-all-button[disabled] { opacity:.55; cursor:default; }
    .wc-do-all-button[disabled][aria-busy=true] { opacity:.8; cursor:progress; }
    .wc-do-all-button[hidden] { display:none; }
    .wc-scan-button[aria-busy=true]::before,.wc-do-all-button[aria-busy=true]::before { content:''; box-sizing:border-box; width:12px; height:12px; flex:none; border:2px solid currentColor; border-right-color:transparent; border-radius:50%; animation:wc-spin .7s linear infinite; }
    .wc-panel-progress { height:3px; margin-top:9px; overflow:hidden; border-radius:99px; background:#f4d8aa; visibility:hidden; }
    .wc-control-panel[data-busy=true] .wc-panel-progress { visibility:visible; }
    .wc-panel-progress::before { content:''; display:block; width:38%; height:100%; border-radius:inherit; background:#e31800; }
    .wc-control-panel[data-busy=true] .wc-panel-progress::before { animation:wc-progress 1.3s ease-in-out infinite alternate; }
    @keyframes wc-progress { from { transform:translateX(-100%); } to { transform:translateX(265%); } }
    .wc-fill-button { all:initial; box-sizing:border-box; position:fixed; display:flex; align-items:center; justify-content:center; gap:5px; width:80px; height:28px; padding:0 8px; border:1px solid #d7a500; border-radius:8px; background:#ffdc4d; color:#571808; box-shadow:0 1px 4px #4b170824; font:700 12px/1 system-ui,sans-serif; cursor:pointer; opacity:0; visibility:hidden; pointer-events:none; transition:background .15s,box-shadow .15s,opacity .12s; }
    .wc-fill-button[data-show=true] { opacity:1; visibility:visible; pointer-events:auto; }
    .wc-fill-button:hover { background:#ffcc00; box-shadow:0 2px 7px #4b170838; }
    .wc-fill-button:focus-visible, .wc-toast button:focus-visible { outline:3px solid #e31800; outline-offset:2px; }
    .wc-fill-button[aria-busy=true] { background:#fff2b3; border-color:#d7a500; }
    .wc-fill-button[data-compact=true] .wc-button-status { display:none; }
    .wc-spinner { width:11px; height:11px; border:2px solid #ebba8a; border-top-color:#e31800; border-radius:50%; animation:wc-spin .8s linear infinite; }
    @keyframes wc-spin { to { transform:rotate(360deg); } }
    .wc-toasts { position:fixed; left:16px; bottom:16px; display:flex; flex-direction:column; gap:10px; width:min(360px,calc(100vw - 32px)); max-height:calc(100vh - 40px); overflow:auto; padding:3px; pointer-events:none; }
    .wc-toast { padding:14px 15px; border:1px solid #d8d7cd; border-radius:13px; background:#fcfbf7; color:#292d22; box-shadow:0 8px 35px #18241422; font:13px/1.5 system-ui,sans-serif; pointer-events:auto; animation:wc-in .18s ease-out; }
    .wc-toast[data-kind=error] { border-color:#dfbbb1; background:#fff8f5; }
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
    host.style.cssText = 'position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;z-index:2147483647!important;pointer-events:none!important;margin:0!important;padding:0!important;border:0!important;background:transparent!important;';
    if (window.top === window && typeof host.showPopover === 'function') host.popover = 'manual';
    shadow = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = css;
    buttonLayer = document.createElement('div');
    controlPanel = document.createElement('div');
    controlPanel.className = 'wc-control-panel';
    controlPanel.setAttribute('role', 'group');
    controlPanel.setAttribute('aria-label', 'Wagecuck controls');
    const panelHead = document.createElement('div');
    panelHead.className = 'wc-panel-head';
    const logo = document.createElement('img');
    logo.className = 'wc-panel-logo';
    logo.src = chrome.runtime.getURL('icons/wagecuck-mark.svg');
    logo.alt = 'Wagecuck';
    const panelCopy = document.createElement('div');
    panelCopy.className = 'wc-panel-copy';
    const panelTitle = document.createElement('strong');
    panelTitle.textContent = 'Wagecuck';
    panelStatus = document.createElement('span');
    panelStatus.className = 'wc-panel-status';
    panelStatus.textContent = 'Scan to find fields';
    panelState = document.createElement('span');
    panelState.className = 'wc-panel-state';
    panelState.setAttribute('aria-hidden', 'true');
    const panelStatusLine = document.createElement('div');
    panelStatusLine.className = 'wc-panel-status-line';
    panelStatusLine.append(panelState, panelStatus);
    panelCopy.append(panelTitle, panelStatusLine);
    panelCount = document.createElement('span');
    panelCount.className = 'wc-panel-count';
    panelCount.hidden = true;
    panelHead.append(logo, panelCopy, panelCount);
    const panelActions = document.createElement('div');
    panelActions.className = 'wc-panel-actions';
    scanButton = document.createElement('button');
    scanButton.type = 'button';
    scanButton.className = 'wc-scan-button';
    scanButton.textContent = 'Scan page';
    scanButton.setAttribute('aria-label', 'Scan page for fillable fields');
    scanButton.addEventListener('click', event => {
      if (!event.isTrusted || scanPending) return;
      scanPending = true;
      updateScanButton();
      if (doAllButton) doAllButton.hidden = true;
      chrome.runtime.sendMessage({ type: 'WC_SCAN_TAB' }).then(result => {
        if (result?.error) toast('Couldn’t scan this page', result.error, 'error', [], 12000);
      }).catch(() => toast('Couldn’t scan this page', 'Reload the page and try again.', 'error', [], 12000)).finally(() => {
        scanPending = false;
        updateScanButton();
        if (scanned) scan();
      });
    });
    doAllButton = document.createElement('button');
    doAllButton.type = 'button';
    doAllButton.className = 'wc-do-all-button';
    doAllButton.textContent = 'FILL';
    doAllButton.title = 'Fill empty scanned fields and attach your saved PDF resume; never submit';
    doAllButton.setAttribute('aria-label', 'FILL: fill empty scanned fields');
    doAllButton.hidden = true;
    doAllButton.addEventListener('click', event => {
      if (!event.isTrusted || doAllButton.disabled) return;
      if (batch) {
        batch.stopping = true;
        updateBatchButton();
        chrome.runtime.sendMessage({ type: 'WC_STOP_ALL_TAB' }).catch(() => toast('Couldn’t stop FILL', 'Reload the page and try again.', 'error', [], 12000));
        return;
      }
      batchPending = true;
      updateBatchButton();
      chrome.runtime.sendMessage({ type: 'WC_FILL_ALL_TAB' }).then(result => {
        if (result?.error) toast('Couldn’t start FILL', result.error, 'error', [], 12000);
      }).catch(() => toast('Couldn’t start FILL', 'Reload the page and try again.', 'error', [], 12000)).finally(() => {
        batchPending = false;
        updateBatchButton();
      });
    });
    toastLayer = document.createElement('div');
    toastLayer.className = 'wc-toasts';
    toastLayer.setAttribute('aria-live', 'assertive');
    toastLayer.setAttribute('aria-relevant', 'additions text');
    panelActions.append(scanButton, doAllButton);
    const panelProgress = document.createElement('div');
    panelProgress.className = 'wc-panel-progress';
    panelProgress.setAttribute('aria-hidden', 'true');
    controlPanel.append(panelHead, panelActions, panelProgress);
    shadow.append(style, buttonLayer);
    if (window.top === window) shadow.append(controlPanel);
    shadow.append(toastLayer);
    document.documentElement.append(host);
    showPanelOnTop();
    for (const record of records.values()) buttonLayer.append(record.button);
  }

  function showPanelOnTop() {
    if (window.top !== window || !host?.isConnected || host.popover !== 'manual') return;
    try {
      if (!host.matches(':popover-open')) host.showPopover();
    } catch { /* The page may be replacing its document. The next scan will retry. */ }
  }

  function checkPanelOnTop() {
    panelCheckFrame = null;
    if (!enabled || window.top !== window || !host?.isConnected || !controlPanel) return;
    // A modal dialog makes everything outside it inert, including a popover.
    // Keep the extension host inside the active dialog until it closes.
    const modal = [...document.querySelectorAll('dialog:modal')].at(-1);
    const parent = modal || document.documentElement;
    if (host.parentNode !== parent) {
      try { if (host.matches(':popover-open')) host.hidePopover(); } catch { /* Continue remounting. */ }
      parent.append(host);
    }
    showPanelOnTop();
    if (host.popover !== 'manual' || !host.matches(':popover-open')) return;
    const rect = controlPanel.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const front = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    if (front === host || front?.getRootNode() === shadow) return;
    if (!front?.closest?.('dialog:modal,[popover]:popover-open,:fullscreen')) return;
    try {
      host.hidePopover();
      host.showPopover();
    } catch { /* A subsequent page mutation or visibility check will retry. */ }
  }

  function schedulePanelCheck() {
    if (panelCheckFrame) return;
    panelCheckFrame = requestAnimationFrame(checkPanelOnTop);
  }

  function updateScanButton() {
    if (!scanButton) return;
    scanButton.disabled = scanPending;
    scanButton.setAttribute('aria-busy', String(scanPending));
    scanButton.textContent = scanPending ? 'Scanning…' : scanned ? 'Scan again' : 'Scan page';
    scanButton.setAttribute('aria-label', scanPending ? 'Scanning page for fillable fields' : scanned ? 'Scan page again for fillable fields' : 'Scan page for fillable fields');
    updatePanelStatus();
  }

  function updateBatchButton() {
    if (!doAllButton) return;
    doAllButton.disabled = scanPending || batchPending || Boolean(batch?.stopping);
    doAllButton.setAttribute('aria-busy', String(batchPending || Boolean(batch)));
    doAllButton.textContent = batch ? batch.stopping ? 'Stopping…' : 'Stop' : batchPending ? 'Starting…' : batchFeedback || 'FILL';
    doAllButton.setAttribute('aria-label', batch ? batch.stopping ? 'Stopping FILL' : 'Stop filling' : batchPending ? 'Starting FILL' : 'FILL: fill empty scanned fields');
    updatePanelStatus();
  }

  function updatePanelStatus() {
    if (!panelStatus) return;
    const busy = scanPending || batchPending || Boolean(batch);
    const count = scannedInputCount ?? records.size;
    panelStatus.textContent = scanPending ? 'Finding inputs…' : batch?.stopping ? 'Stopping fill…' : batch ? 'Writing answers…' : batchPending ? 'Preparing answers…' : scanned ? `${count} ${count === 1 ? 'input' : 'inputs'} found` : 'Scan to find inputs';
    panelCount.hidden = !scanned || scanPending;
    if (!panelCount.hidden) {
      panelCount.textContent = String(count);
      panelCount.setAttribute('aria-label', `${count} fillable ${count === 1 ? 'input' : 'inputs'} found`);
    }
    panelState.dataset.state = busy ? 'busy' : scanned && count ? 'ready' : 'idle';
    controlPanel.dataset.busy = String(busy);
  }

  function readValue(field) {
    const yesNo = ashbyYesNo(field);
    if (yesNo) return yesNo.buttons.find(button => button.getAttribute('aria-pressed') === 'true')?.textContent?.trim() || '';
    if (isSupportedCombobox(field)) return comboboxSelectedText(field) || field.value || field.getAttribute('aria-valuetext') || '';
    if (field instanceof HTMLInputElement && field.type === 'file') return field.files?.length || 0;
    const checkbox = globalThis.WCFieldContext.checkboxDetails(field);
    if (checkbox) return JSON.stringify(checkbox.options.filter(option => globalThis.WCFieldContext.choiceChecked(option.element)).map(option => option.value));
    if (globalThis.WCFieldContext.choiceKind(field) === 'checkbox') return globalThis.WCFieldContext.choiceChecked(field);
    if (globalThis.WCFieldContext.choiceKind(field) === 'radio') return globalThis.WCFieldContext.radioChoices(field).find(option => globalThis.WCFieldContext.choiceChecked(option.element))?.value || '';
    return 'value' in field ? field.value : field.innerText;
  }

  function hasAnswer(field) {
    if (field instanceof HTMLInputElement && field.type === 'file') return Boolean(field.files?.length);
    if (globalThis.WCFieldContext.checkboxDetails(field)) return readValue(field) !== '[]';
    const value = readValue(field);
    return typeof value === 'boolean' ? value : Boolean(String(value).trim());
  }

  function selectableOption(field, value) {
    if (ashbyYesNo(field)) return ['Yes', 'No'].includes(value);
    if (isSupportedCombobox(field)) return (comboboxOptions.get(field) || []).some(option => option.value === value);
    if (globalThis.WCFieldContext.choiceKind(field) === 'radio') return globalThis.WCFieldContext.radioChoices(field).some(option => option.value === value);
    return field instanceof HTMLSelectElement && [...field.options].some(option => option.value === value && value.trim() && !option.disabled && !option.hidden && !option.closest('optgroup[disabled]'));
  }

  function normalizedAnswer(field, answer) {
    if (ashbyYesNo(field)) return typeof answer === 'string' && selectableOption(field, answer) ? answer : null;
    const checkbox = globalThis.WCFieldContext.checkboxDetails(field);
    if (checkbox) {
      const values = new Set(checkbox.options.map(option => option.value));
      return Array.isArray(answer) && answer.length <= values.size && new Set(answer).size === answer.length && answer.every(value => typeof value === 'string' && values.has(value)) ? JSON.stringify(checkbox.options.filter(option => answer.includes(option.value)).map(option => option.value)) : null;
    }
    if (globalThis.WCFieldContext.choiceKind(field) === 'checkbox') return typeof answer === 'boolean' ? answer : null;
    if (field instanceof HTMLSelectElement || isSupportedCombobox(field) || globalThis.WCFieldContext.choiceKind(field) === 'radio') return typeof answer === 'string' && selectableOption(field, answer) ? answer : null;
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
    const minSize = ['checkbox', 'radio'].includes(globalThis.WCFieldContext.choiceKind(field)) ? 10 : 18;
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

  function comboboxShell(field) {
    return field instanceof HTMLInputElement && field.getAttribute('role') === 'combobox' && field.getAttribute('aria-autocomplete') === 'list'
      ? field.closest('.select-shell') : null;
  }

  function isReactSelect(field) {
    return Boolean(field.id && comboboxShell(field)?.querySelector('.select__control'));
  }

  function isAshbyAutocomplete(field) {
    return field instanceof HTMLInputElement && field.matches('input.ashby-application-form-input-autocomplete[role="combobox"][aria-autocomplete="list"][aria-haspopup="listbox"]');
  }

  function ashbyYesNo(field) {
    if (!(field instanceof HTMLInputElement) || field.type !== 'checkbox') return null;
    const container = field.closest('.ashby-application-form-input-yesno');
    if (!container || container.querySelectorAll('input[type="checkbox"]').length !== 1) return null;
    const buttons = ['yes', 'no'].map(value => container.querySelector(`button[data-option="${value}"]`));
    return buttons.every(Boolean) ? { container, buttons } : null;
  }

  function isSupportedCombobox(field) {
    if (isAshbyAutocomplete(field)) return false;
    if (field.getAttribute('role') !== 'combobox' || field.querySelector('[role="combobox"]')) return false;
    const ids = `${field.getAttribute('aria-controls') || ''} ${field.getAttribute('aria-owns') || ''}`.trim();
    return isReactSelect(field) || Boolean(ids && ids.split(/\s+/).every(id => /^[^\s]+$/.test(id)) && field.getAttribute('aria-haspopup') !== 'grid');
  }

  function comboboxTrigger(field) {
    return isReactSelect(field) ? comboboxShell(field).querySelector('.select__control') : field;
  }

  function comboboxSelectedText(field) {
    for (let node = field.parentElement, depth = 0; node && depth < 3 && !node.matches('form,body,html'); node = node.parentElement, depth++) {
      if (node.querySelectorAll('[role="combobox"]').length > 1) break;
      const selected = node.querySelector('.select__single-value,[class*="singleValue"],[data-automation-id="selectedItem"]');
      if (selected) return selected.textContent.replace(/\s+/g, ' ').trim();
    }
    return '';
  }

  function comboboxList(field) {
    const root = field.getRootNode();
    const ids = `${field.getAttribute('aria-controls') || ''} ${field.getAttribute('aria-owns') || ''}`.trim().split(/\s+/).filter(Boolean);
    for (const id of ids) {
      const list = root.getElementById?.(id) || field.ownerDocument.getElementById(id);
      if (list?.matches('[role="listbox"]') || list?.querySelector('[role="option"]')) return list;
    }
    return isReactSelect(field) ? root.getElementById?.(`react-select-${field.id}-listbox`) || null : null;
  }

  function mouseSequence(element) {
    for (const type of ['mousedown', 'mouseup', 'click']) element.dispatchEvent(new MouseEvent(type, { bubbles: true, composed: true, button: 0 }));
  }

  async function waitForComboboxMenu(field) {
    for (let attempt = 0; attempt < 20; attempt++) {
      const list = comboboxList(field);
      if (field.getAttribute('aria-expanded') === 'true' && list?.querySelector('[role="option"]')) return list;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    return null;
  }

  async function discoverComboboxOptions(field) {
    if (!isSupportedCombobox(field) || !field.isConnected) return [];
    comboboxOptions.delete(field);
    const opened = field.getAttribute('aria-expanded') !== 'true';
    if (opened) mouseSequence(comboboxTrigger(field));
    try {
      const list = await waitForComboboxMenu(field);
      if (!list) return [];
      const options = [...list.querySelectorAll('[role="option"]')]
        .filter(option => option.getAttribute('aria-disabled') !== 'true')
        .map(option => option.textContent.replace(/\s+/g, ' ').trim())
        .filter(Boolean);
      if (!options.length || options.length > 300 || new Set(options).size !== options.length) return [];
      const result = options.map(label => ({ value: label, label }));
      comboboxOptions.set(field, result);
      return result;
    } finally {
      if (opened && field.isConnected) field.blur();
    }
  }

  function visualTarget(field) {
    const yesNo = ashbyYesNo(field);
    if (yesNo) return yesNo.container;
    if (isAshbyAutocomplete(field)) return field;
    if (isSupportedCombobox(field)) return comboboxTrigger(field);
    if (field.type === 'file') return resumeVisualTarget(field) || field;
    const checkbox = globalThis.WCFieldContext.checkboxDetails(field);
    if (checkbox) return checkbox.container;
    if (globalThis.WCFieldContext.choiceKind(field) === 'radio') return globalThis.WCFieldContext.radioDetails(field).container || globalThis.WCFieldContext.visibleChoiceLabel(field) || field;
    if (globalThis.WCFieldContext.choiceKind(field) === 'checkbox') return isVisible(field) ? field : globalThis.WCFieldContext.visibleChoiceLabel(field) || field;
    return field;
  }

  function highlightVisual(visual) {
    const existing = highlighted.get(visual);
    if (existing) { existing.count++; return; }
    const previous = HIGHLIGHT_STYLES.map(([property]) => ({
      property, value: visual.style.getPropertyValue(property), priority: visual.style.getPropertyPriority(property),
    }));
    for (const [property, value] of HIGHLIGHT_STYLES) visual.style.setProperty(property, value, 'important');
    const applied = HIGHLIGHT_STYLES.map(([property]) => ({
      value: visual.style.getPropertyValue(property), priority: visual.style.getPropertyPriority(property),
    }));
    highlighted.set(visual, { count: 1, previous, applied });
  }

  function unhighlightVisual(visual) {
    const state = highlighted.get(visual);
    if (!state || --state.count > 0) return;
    highlighted.delete(visual);
    for (let index = 0; index < state.previous.length; index++) {
      const { property, value, priority } = state.previous[index];
      const applied = state.applied[index];
      if (visual.style.getPropertyValue(property) !== applied.value || visual.style.getPropertyPriority(property) !== applied.priority) continue;
      if (value) visual.style.setProperty(property, value, priority);
      else visual.style.removeProperty(property);
    }
  }

  function updateHoverButton(record) {
    record.button.dataset.show = String(Boolean(record.hoverTarget || record.hoverButton || record.active || record.starting || record.visual.matches(':hover, :focus-within') || record.button.matches(':focus')));
  }

  function clearUndo(record) {
    if (!record.undo) return;
    clearTimeout(record.undo.timer);
    record.undo = null;
  }

  function bindHoverButton(record) {
    const visual = record.visual;
    const button = record.button;
    const showTarget = () => { clearTimeout(record.hideTimer); record.hoverTarget = true; updateHoverButton(record); };
    const leaveTarget = () => { record.hoverTarget = false; record.hideTimer = setTimeout(() => updateHoverButton(record), 180); };
    const showButton = () => { clearTimeout(record.hideTimer); record.hoverButton = true; updateHoverButton(record); };
    const leaveButton = () => { record.hoverButton = false; record.hideTimer = setTimeout(() => updateHoverButton(record), 180); };
    visual.addEventListener('pointerenter', showTarget);
    visual.addEventListener('pointerleave', leaveTarget);
    visual.addEventListener('focusin', showTarget);
    visual.addEventListener('focusout', leaveTarget);
    button.addEventListener('pointerenter', showButton);
    button.addEventListener('pointerleave', leaveButton);
    button.addEventListener('focus', showButton);
    button.addEventListener('blur', leaveButton);
    record.unbindHover = () => {
      clearTimeout(record.hideTimer);
      visual.removeEventListener('pointerenter', showTarget);
      visual.removeEventListener('pointerleave', leaveTarget);
      visual.removeEventListener('focusin', showTarget);
      visual.removeEventListener('focusout', leaveTarget);
      button.removeEventListener('pointerenter', showButton);
      button.removeEventListener('pointerleave', leaveButton);
      button.removeEventListener('focus', showButton);
      button.removeEventListener('blur', leaveButton);
      record.hoverTarget = false;
      record.hoverButton = false;
    };
    updateHoverButton(record);
  }

  function resumeQuestionLabel(field) {
    const group = field.closest('[role="group"][aria-labelledby]');
    if (!group || group.querySelectorAll('input[type="file"]').length !== 1) return '';
    return group.getAttribute('aria-labelledby').split(/\s+/)
      .map(id => field.getRootNode().getElementById?.(id))
      .filter(node => node && group.contains(node))
      .map(node => node.textContent.trim()).filter(Boolean).join(' ').slice(0, 500);
  }

  function resumeLabelFor(field) {
    return resumeQuestionLabel(field) || labelFor(field);
  }

  function isResumeField(field) {
    if (!(field instanceof HTMLInputElement) || field.type !== 'file' || field.multiple) return false;
    const ownDescription = `${resumeLabelFor(field)} ${field.name} ${field.id}`.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]/g, ' ');
    if (/\b(cover letter|portfolio|transcript|certificate|photo|identity document)\b/i.test(ownDescription)) return false;
    const trigger = resumeTrigger(field);
    const description = `${ownDescription} ${trigger?.textContent || ''} ${trigger?.getAttribute('aria-label') || ''}`;
    if (!/\b(resume|r[eé]sum[eé]|curriculum vitae|cv)\b/i.test(description)) return false;
    const accepted = field.accept.toLowerCase().split(',').map(value => value.trim()).filter(Boolean);
    return !accepted.length || accepted.some(value => value === '.pdf' || value === 'application/pdf' || value === 'application/*' || value === '*/*');
  }

  function resumeTrigger(field) {
    const label = [...(field.labels || [])].find(item => isVisible(item));
    if (label) return label;
    const localButtons = [...(parentElement(field)?.querySelectorAll('button,[role="button"]') || [])].filter(item => isVisible(item));
    if (localButtons.length === 1) return localButtons[0];
    for (let parent = parentElement(field), depth = 0; parent && depth < 3 && !parent.matches('form,main,body,html'); parent = parentElement(parent), depth++) {
      const buttons = [...parent.querySelectorAll('button,[role="button"]')].filter(item => isVisible(item) && /\b(resume|r[eé]sum[eé]|curriculum vitae|cv)\b/i.test(`${item.textContent} ${item.getAttribute('aria-label') || ''}`));
      if (buttons.length === 1) return buttons[0];
    }
    return null;
  }

  function resumeVisualTarget(field) {
    if (isVisible(field)) return field;
    return resumeTrigger(field);
  }

  function isEligible(field) {
    const choiceKind = globalThis.WCFieldContext.choiceKind(field);
    if (field.getRootNode() === shadow || field.disabled || field.matches(':disabled') || (field.readOnly && !isSupportedCombobox(field)) || field.getAttribute('aria-disabled') === 'true' || (field.getAttribute('aria-readonly') === 'true' && !isSupportedCombobox(field))) return false;
    if (field.tagName === 'INPUT' && !TYPES.has(field.type) && !choiceKind && !isSupportedCombobox(field)) return false;
    if (choiceKind && field.querySelector(`input[type="${choiceKind}"]`)) return false;
    if (field.type === 'file' && !isResumeField(field)) return false;
    if (field.matches('[role="combobox"],[aria-haspopup="listbox"]') && !isSupportedCombobox(field) && !isAshbyAutocomplete(field)) return false;
    if (field.tagName === 'SELECT' && (field.multiple || field.size > 1 || field.options.length > 100 || ![...field.options].some(option => selectableOption(field, option.value)))) return false;
    if (choiceKind === 'radio') {
      const options = globalThis.WCFieldContext.radioChoices(field);
      if (options.length < 2 || options.length > 100 || new Set(options.map(option => option.value)).size !== options.length || options[0].element !== field || !globalThis.WCFieldContext.radioDetails(field).label) return false;
    }
    const checkbox = globalThis.WCFieldContext.checkboxDetails(field);
    if (checkbox && checkbox.options[0].element !== field) return false;
    if (field.tagName !== 'SELECT' && !choiceKind && field.type !== 'file' && field.maxLength === 0 && !isSupportedCombobox(field)) return false;
    if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(field.tagName) && !field.isContentEditable && !choiceKind && !isSupportedCombobox(field)) return false;
    if (field.isContentEditable && parentElement(field)?.isContentEditable) return false;
    if (globalThis.WCFieldContext.isSensitiveField(field) || globalThis.WCFieldContext.isSearchField(field) || globalThis.WCFieldContext.isManualAttestationField(field)) return false;
    if (ashbyYesNo(field)) return ashbyYesNo(field).buttons.every(button => !button.disabled) && isVisible(visualTarget(field));
    return field.type === 'file' ? Boolean(resumeVisualTarget(field)) : isSupportedCombobox(field) ? isVisible(visualTarget(field)) : choiceKind ? globalThis.WCFieldContext.choiceAvailable(field) && isVisible(visualTarget(field)) : isVisible(field);
  }

  function fieldInfo(field) {
    const info = globalThis.WCFieldContext.fieldInfo(field);
    if (ashbyYesNo(field)) {
      info.type = 'select';
      info.options = [{ value: 'Yes', label: 'Yes' }, { value: 'No', label: 'No' }];
      info.currentValue = readValue(field);
      info.required ||= Boolean(field.closest('.ashby-application-form-field-entry')?.querySelector('.ashby-application-form-question-title[class*="required"]'));
    }
    if (isAshbyAutocomplete(field)) {
      info.type = 'text';
      info.context = `${info.context}\nChoose the exact location from this page's autocomplete suggestions.`.trim();
    }
    if (isSupportedCombobox(field)) {
      info.type = 'select';
      info.options = comboboxOptions.get(field) || [];
      info.currentValue = readValue(field);
      info.placeholder = comboboxShell(field)?.querySelector('.select__placeholder')?.textContent?.trim() || field.getAttribute('placeholder') || '';
    }
    return info;
  }

  function targetSignature(info) {
    return JSON.stringify(['label', 'type', 'placeholder', 'context', 'required', 'maxLength', 'min', 'max', 'step', 'pattern', 'options', 'accept'].map(key => info[key]));
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
            record.unbindHover();
            unhighlightVisual(record.visual);
            record.visual = visual;
            highlightVisual(visual);
            bindHoverButton(record);
            resizeObserver.observe(visual);
          }
          if (!record.active && !record.button.hasAttribute('aria-busy')) {
            record.button.setAttribute('aria-label', field.type === 'file' ? `Attach resume: ${resumeLabelFor(field)}` : `Fill with AI: ${labelFor(field)}`);
            record.button.title = field.type === 'file' ? `Attach saved resume to ${resumeLabelFor(field)}` : `Write an answer for ${labelFor(field)}`;
          }
          continue;
        }
        const button = document.createElement('button');
        const id = `wc-field-${++serial}`;
        button.type = 'button';
        button.className = 'wc-fill-button';
        button.dataset.wcField = id;
        button.textContent = field.type === 'file' ? 'Attach PDF' : '✦ Write';
        button.title = field.type === 'file' ? `Attach saved resume to ${resumeLabelFor(field)}` : `Write an answer for ${labelFor(field)}`;
        button.setAttribute('aria-label', field.type === 'file' ? `Attach resume: ${resumeLabelFor(field)}` : `Fill with AI: ${labelFor(field)}`);
        const record = { field, visual: visualTarget(field), button, id, active: null, starting: false };
        button.addEventListener('click', event => {
          if (!event.isTrusted) return;
          if (record.active) record.active.cancel();
          else if (record.undo) void undoFill(record);
          else void startFill(record);
        });
        records.set(field, record);
        highlightVisual(record.visual);
        bindHoverButton(record);
        buttonLayer.append(button);
        resizeObserver.observe(record.visual);
      }
    }
    for (const [field, record] of records) {
      if (!fields.has(field)) {
        record.active?.cancel('Field is no longer available.');
        clearUndo(record);
        record.button.remove();
        record.unbindHover();
        unhighlightVisual(record.visual);
        resizeObserver.unobserve(record.visual);
        records.delete(field);
      }
    }
    for (const root of roots) {
      if (root instanceof ShadowRoot && !root.host.isConnected) roots.delete(root);
    }
    if (scannedInputCount !== null) scannedInputCount = Math.max(0, scannedInputCount + records.size - localScannedCount);
    localScannedCount = records.size;
    if (doAllButton) {
      doAllButton.hidden = window.top !== window || scanPending;
      updateBatchButton();
    }
    updateScanButton();
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
      clearInterval(ending.heartbeat);
      ending.keepalive?.disconnect();
      chrome.runtime.sendMessage({ type: 'WC_STOP_ALL_TAB' }).catch(() => {});
    }
    scanPending = false;
    batchPending = false;
    scannedInputCount = null;
    localScannedCount = 0;
    clearTimeout(batchFeedbackTimer);
    batchFeedback = '';
    if (doAllButton) doAllButton.hidden = true;
    updateScanButton();
    for (const [field, record] of records) {
      record.active?.cancel(reason || 'The page scan ended.');
      clearUndo(record);
      resizeObserver.unobserve(record.visual);
      record.button.remove();
      record.unbindHover();
      unhighlightVisual(record.visual);
    }
    records.clear();
    updateScanButton();
  }

  function activateScan() {
    if (!enabled) return { count: 0, scanned: false, enabled };
    scanned = true;
    scannedUrl = location.href;
    scannedInputCount = null;
    scan();
    updateScanButton();
    return { count: records.size, scanned, enabled };
  }

  function schedulePosition() {
    if (positionFrame) return;
    positionFrame = requestAnimationFrame(positionButtons);
  }

  function positionButtons() {
    positionFrame = null;
    for (const record of records.values()) {
      const { field, visual, button } = record;
      const rect = visual.getBoundingClientRect();
      const choice = field.tagName === 'SELECT' || isSupportedCombobox(field) || ['checkbox', 'radio'].includes(globalThis.WCFieldContext.choiceKind(field)) || field.type === 'file';
      const compact = !choice && (rect.width < 240 || rect.height < 36);
      const width = field.type === 'file' ? 100 : compact ? 45 : 80;
      const height = compact ? 26 : 28;
      let x = choice ? rect.right + 8 : rect.right - width - 4;
      // Straddle the top border on roomy fields so the button does not cover text.
      let y = globalThis.WCFieldContext.choiceKind(field) === 'radio' || globalThis.WCFieldContext.checkboxDetails(field) ? Math.max(2, rect.top + 8) : choice ? Math.max(2, rect.top + (rect.height - height) / 2) : compact ? rect.top + Math.min(5, (rect.height - height) / 2) : rect.top >= 0 ? Math.max(2, rect.top - 12) : rect.top - 12;
      const panelRect = window.top === window ? shadow?.querySelector('.wc-control-panel')?.getBoundingClientRect() : null;
      if (panelRect && x < panelRect.right && x + width > panelRect.left && y < panelRect.bottom && y + height > panelRect.top) {
        const leftOfPanel = panelRect.left - width - 8;
        if (leftOfPanel >= 2) x = leftOfPanel;
        else y = panelRect.top - height - 8;
      }
      button.dataset.compact = String(compact);
      if (!button.hasAttribute('aria-busy')) {
        if (record.undo && readValue(field) !== record.undo.answer) clearUndo(record);
        const text = field.type === 'file' ? hasAnswer(field) ? 'PDF attached' : 'Attach PDF' : record.undo ? '↶ Undo' : compact ? '✦ AI' : hasAnswer(field) ? '✦ Rewrite' : '✦ Write';
        if (button.textContent !== text) button.textContent = text;
        button.setAttribute('aria-label', field.type === 'file' ? `Attach resume: ${resumeLabelFor(field)}` : record.undo ? `Undo generated answer: ${labelFor(field)}` : `Fill with AI: ${labelFor(field)}`);
        if (record.undo) button.title = `Undo the generated answer for ${labelFor(field)}`;
      }
      button.style.width = `${width}px`;
      button.style.height = `${height}px`;
      let visible = rect.right > 0 && rect.left < innerWidth && y >= 0 && y + height <= innerHeight && isVisible(visual);
      for (let node = parentElement(visual); visible && node; node = parentElement(node)) {
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
      updateHoverButton(record);
    }
  }

  function toast(title, detail = '', kind = 'error', actions = [], duration = 0) {
    if (kind !== 'error') throw new Error('Toasts are reserved for errors.');
    ensureUI();
    const element = document.createElement('div');
    element.className = 'wc-toast';
    element.dataset.kind = kind;
    element.setAttribute('role', 'alert');
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

  function setChoiceChecked(field, checked) {
    if (globalThis.WCFieldContext.choiceChecked(field) === checked) return;
    if (field instanceof HTMLInputElement && ['checkbox', 'radio'].includes(field.type)) {
      field.focus({ preventScroll: true });
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set.call(field, checked);
      field.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
      field.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    } else field.click();
  }

  async function writeAshbyAutocomplete(field, answer) {
    const original = field.value;
    field.focus({ preventScroll: true });
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, answer);
    field.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertReplacementText', data: answer }));
    const list = await waitForComboboxMenu(field);
    const normalize = value => value.replace(/\s+/g, ' ').trim().toLocaleLowerCase();
    const options = [...(list?.querySelectorAll('[role="option"]') || [])].filter(option => option.getAttribute('aria-disabled') !== 'true');
    const exact = options.filter(option => normalize(option.textContent) === normalize(answer));
    if (exact.length !== 1) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, original);
      field.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertReplacementText', data: original }));
      field.blur();
      throw new Error('No exact location suggestion was available. Choose a suggestion on the page.');
    }
    const selected = exact[0].textContent.replace(/\s+/g, ' ').trim();
    mouseSequence(exact[0]);
    await new Promise(resolve => setTimeout(resolve, 60));
    if (field.getAttribute('aria-expanded') === 'true' || normalize(field.value) !== normalize(selected)) throw new Error('The page did not accept the location suggestion.');
    return field.value;
  }

  async function writeValue(field, answer) {
    if (isAshbyAutocomplete(field)) return writeAshbyAutocomplete(field, answer);
    const yesNo = ashbyYesNo(field);
    if (yesNo) {
      const target = yesNo.buttons.find(button => button.textContent.trim() === answer);
      if (!target || target.disabled) throw new Error('The Yes/No choice is no longer available.');
      target.click();
      await new Promise(resolve => setTimeout(resolve, 0));
      return;
    }
    if (isSupportedCombobox(field)) {
      if (field.getAttribute('aria-expanded') !== 'true') mouseSequence(comboboxTrigger(field));
      const list = await waitForComboboxMenu(field);
      const option = [...(list?.querySelectorAll('[role="option"]') || [])].find(item => item.getAttribute('aria-disabled') !== 'true' && item.textContent.replace(/\s+/g, ' ').trim() === answer);
      if (!option) { field.blur(); throw new Error('The dropdown option is no longer available.'); }
      mouseSequence(option);
      await new Promise(resolve => setTimeout(resolve, 30));
      if (field.getAttribute('aria-expanded') === 'true') field.blur();
      return;
    }
    const checkbox = globalThis.WCFieldContext.checkboxDetails(field);
    if (checkbox) {
      const selected = new Set(JSON.parse(answer));
      for (const option of checkbox.options) {
        const target = option.element;
        const checked = selected.has(option.value);
        setChoiceChecked(target, checked);
      }
      return;
    }
    if (globalThis.WCFieldContext.choiceKind(field) === 'radio') {
      const choices = globalThis.WCFieldContext.radioChoices(field);
      const selected = choices.find(option => option.value === answer)?.element;
      if (answer && !selected) throw new Error('The radio option is no longer available.');
      if (selected && !(selected instanceof HTMLInputElement && selected.type === 'radio')) {
        setChoiceChecked(selected, true);
        if (choices.some(option => option.element !== selected && globalThis.WCFieldContext.choiceChecked(option.element))) throw new Error('The page kept more than one radio option selected.');
        return;
      }
      for (const option of choices) {
        const target = option.element;
        if (target === selected || !globalThis.WCFieldContext.choiceChecked(target)) continue;
        setChoiceChecked(target, false);
      }
      if (selected) setChoiceChecked(selected, true);
      if (choices.some(option => option.element !== selected && globalThis.WCFieldContext.choiceChecked(option.element))) throw new Error('The page kept more than one radio option selected.');
      return;
    }
    if (globalThis.WCFieldContext.choiceKind(field) === 'checkbox' && !(field instanceof HTMLInputElement && field.type === 'checkbox')) {
      setChoiceChecked(field, answer);
      return;
    }
    let target = field;
    if (field instanceof HTMLInputElement && field.type === 'checkbox') {
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

  async function undoFill(record) {
    const undo = record.undo;
    if (!undo) return;
    clearUndo(record);
    const field = record.field;
    if (!field.isConnected || readValue(field) !== undo.answer) { schedulePosition(); return; }
    try {
      await writeValue(field, undo.original);
      if (readValue(field) !== undo.original) throw new Error('The website did not restore the previous answer.');
      logUI('ui.undo', undo.requestId);
    } catch (error) {
      toast('Couldn’t undo this answer', error.message || 'Edit the field manually.', 'error', [], 14000);
    }
    schedulePosition();
  }

  function logUI(event, requestId, code = '') {
    chrome.runtime.sendMessage({ type: 'WC_LOG_EVENT', event, metadata: { requestId, ...(code ? { code } : {}) } }).catch(() => {});
  }

  function attachResume(record, resumeFile, signature, sourceUrl) {
    const field = record.field;
    if (!records.has(field) || records.get(field) !== record || !enabled || !scanned || location.href !== sourceUrl || !isEligible(field) || hasAnswer(field) || targetSignature(fieldInfo(field)) !== signature) return 'skipped';
    if (!resumeFile) throw new Error('Add a PDF resume in Edit profile first.');
    if (resumeFile.type !== 'application/pdf' || !/\.pdf$/i.test(resumeFile.name) || !Number.isInteger(resumeFile.size) || resumeFile.size < 1 || resumeFile.size > 5 * 1024 * 1024 || !/^data:application\/pdf;base64,[A-Za-z0-9+/]+={0,2}$/.test(resumeFile.dataUrl || '')) throw new Error('The saved resume is invalid. Upload the PDF again in Edit profile.');
    const bytes = Uint8Array.from(atob(resumeFile.dataUrl.slice(resumeFile.dataUrl.indexOf(',') + 1)), character => character.charCodeAt(0));
    if (bytes.length !== resumeFile.size || String.fromCharCode(...bytes.slice(0, 5)) !== '%PDF-') throw new Error('The saved PDF could not be read. Upload it again in Edit profile.');
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], resumeFile.name, { type: 'application/pdf' }));
    field.files = transfer.files;
    field.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    field.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    if (field.files?.length !== 1 || field.files[0].name !== resumeFile.name || field.files[0].size !== resumeFile.size || field.validity && !field.validity.valid) throw new Error('The website did not accept the PDF. Upload it manually.');
    schedulePosition();
    return 'filled';
  }

  async function startResumeAttach(record) {
    const field = record.field;
    const signature = targetSignature(fieldInfo(field));
    const sourceUrl = location.href;
    if (hasAnswer(field)) return { status: 'skipped' };
    record.starting = true;
    try {
      const response = await chrome.runtime.sendMessage({ type: 'WC_GET_RESUME_FOR_UPLOAD' });
      const status = attachResume(record, response?.resumeFile, signature, sourceUrl);
      if (status === 'filled') logUI('ui.filled', crypto.randomUUID());
      return { status };
    } catch (error) {
      toast('Couldn’t attach resume', error.message || 'Upload the PDF manually.', 'error', [{ label: 'Edit profile', run: () => chrome.runtime.sendMessage({ type: 'WC_OPEN_SETTINGS' }).catch(() => {}) }], 16000);
      return { status: 'failed' };
    } finally {
      record.starting = false;
      schedulePosition();
    }
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
    if (record.field.type === 'file') return startResumeAttach(record);
    if (!configured) {
      toast('Set up AI to fill this field', 'Connect your OpenAI account using an API key, then add your background.', 'error', [{ label: 'Set up AI', run: () => chrome.runtime.sendMessage({ type: 'WC_OPEN_SETTINGS' }).catch(() => {}) }], 14000);
      return { status: 'failed', code: 'NOT_CONFIGURED' };
    }
    if ([...records.values()].filter(item => item.active).length >= 3) {
      toast('Couldn’t start another answer', 'Three answers are already in progress. Wait for one to finish or stop one first.', 'error', [], 10000);
      return { status: 'failed', code: 'BUSY' };
    }
    const field = record.field;
    if (isSupportedCombobox(field)) {
      record.starting = true;
      try {
        if (!(await discoverComboboxOptions(field)).length) {
          toast('Couldn’t fill this dropdown', 'This menu did not expose a usable set of options. Choose from it on the page.', 'error', [], 14000);
          return { status: 'skipped' };
        }
      } catch {
        toast('Couldn’t fill this dropdown', 'The website did not expose this menu’s options. Choose from it on the page.', 'error', [], 14000);
        return { status: 'skipped' };
      } finally { record.starting = false; }
      if (!scanned || scannedUrl !== location.href || records.get(field) !== record || !isEligible(field)) return;
    }
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
    const editTargets = globalThis.WCFieldContext.choiceKind(field) === 'radio' ? globalThis.WCFieldContext.radioGroup(field) : globalThis.WCFieldContext.checkboxDetails(field)?.options.map(option => option.element) || [field];
    editTargets.forEach(target => target.addEventListener('input', onEdit));
    function onEdit() { edited = true; }
    record.button.setAttribute('aria-busy', 'true');
    record.button.setAttribute('aria-label', `Cancel writing: ${labelFor(field)}`);
    record.button.textContent = '';
    const spinner = document.createElement('span');
    spinner.className = 'wc-spinner';
    record.button.append(spinner);
    const workingLabel = document.createElement('span');
    workingLabel.className = 'wc-button-status';
    workingLabel.textContent = 'Writing';
    record.button.append(workingLabel);
    record.button.title = 'Writing an answer. Click to stop.';
    const interval = setInterval(() => {
      record.button.title = `${writing ? 'Writing' : 'Thinking'} · ${Math.floor((Date.now() - start) / 1000)}s. Click to stop.`;
    }, 1000);
    const timeout = setTimeout(() => cancel('The answer took too long. Please try again.'), 130000);
    function cleanup() {
      if (finished) return false;
      finished = true;
      clearInterval(interval);
      clearTimeout(timeout);
      editTargets.forEach(target => target.removeEventListener('input', onEdit));
      record.active = null;
      record.button.removeAttribute('aria-busy');
      record.button.textContent = '✦ Write';
      record.button.title = `Write an answer for ${labelFor(field)}`;
      updateHoverButton(record);
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
      if (/too long/i.test(reason)) toast('Answer timed out', reason, 'error', [], 14000);
    }
    record.active = { cancel };
    updateHoverButton(record);
    port.onDisconnect.addListener(() => {
      if (!finished) {
        const message = chrome.runtime.lastError?.message;
        cleanup();
        settle({ status: 'failed', code: 'CONNECTION_INTERRUPTED' });
        toast('Connection interrupted', message ? 'Reload the page and try again.' : 'The AI session stopped. Click AI to try again.', 'error', [], 14000);
      }
    });
    port.onMessage.addListener(async message => {
      if (finished || message.requestId !== requestId) return;
      if (message.type === 'state') {
        writing = message.state === 'writing';
        workingLabel.textContent = writing ? 'Writing' : 'Thinking';
        record.button.title = `${writing ? 'Writing' : 'Thinking'} an answer. Click to stop.`;
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
        toast('Couldn’t fill this field', 'The field is no longer available. Scan the page again.', 'error', [], 12000);
        return;
      }
      if (edited || readValue(field) !== original) {
        settle({ status: 'skipped' });
        logUI('ui.edit_preserved', requestId);
        return;
      }
      if (location.href !== sourceLocation || targetSignature(fieldInfo(field)) !== targetSignature(targetInfo)) {
        settle({ status: 'skipped' });
        logUI('ui.edit_preserved', requestId, 'FIELD_CHANGED');
        return;
      }
      const answer = normalizedAnswer(field, message.answer);
      if (answer === null) {
        settle({ status: 'failed' });
        toast('Answer does not fit this field', 'The generated choice or format is no longer available. Scan again and retry.', 'error', [], 14000);
        return;
      }
      if ((globalThis.WCFieldContext.choiceKind(field) || field.tagName === 'SELECT' || isSupportedCombobox(field)) && answer === original) {
        settle({ status: 'skipped' });
        return;
      }
      try {
        const written = await writeValue(field, answer);
        const expected = written === undefined ? answer : written;
        if (readValue(field) !== expected) throw new Error('The website did not keep the answer.');
        if (field.validity && !field.validity.valid) {
          if (!isSupportedCombobox(field) && !isAshbyAutocomplete(field) && !ashbyYesNo(field)) await writeValue(field, original);
          throw new Error('The answer did not meet the field’s format. Your previous text was restored.');
        }
        logUI('ui.filled', requestId);
        settle({ status: 'filled' });
        if (!isSupportedCombobox(field) && !isAshbyAutocomplete(field) && !ashbyYesNo(field)) {
          const undo = { original, answer: expected, requestId };
          undo.timer = setTimeout(() => { if (record.undo === undo) { clearUndo(record); schedulePosition(); } }, 25000);
          record.undo = undo;
        }
        schedulePosition();
      } catch (error) {
        settle({ status: 'failed' });
        logUI('ui.insert_failed', requestId, 'INSERT_FAILED');
        toast('Couldn’t insert the answer', error.message, 'error', [], 14000);
      }
    });
    try {
      port.postMessage({ type: 'generate', requestId, field: targetInfo, page: collectPage() });
    } catch {
      cleanup();
      settle({ status: 'failed' });
      toast('Couldn’t read this page', 'Reload the page and try again.', 'error', [], 14000);
    }
    return completion;
  }

  async function batchSnapshot() {
    if (!enabled || !scanned || scannedUrl !== location.href) return { scanned: false, enabled };
    scan();
    let unavailableChoices = 0;
    for (const record of [...records.values()]) {
      if (!isSupportedCombobox(record.field) || record.active || record.starting || !isEligible(record.field) || hasAnswer(record.field)) continue;
      try {
        if (!(await discoverComboboxOptions(record.field)).length) unavailableChoices++;
      } catch { unavailableChoices++; }
    }
    return {
      scanned: true, enabled, page: collectPage(), unavailableChoices,
      targets: [...records.values()].filter(record => record.field.type !== 'file' && !record.active && !record.starting && isEligible(record.field) && !hasAnswer(record.field) && (!isSupportedCombobox(record.field) || (comboboxOptions.get(record.field) || []).length))
        .map(record => ({ id: record.id, field: fieldInfo(record.field) })),
      uploadTargets: [...records.values()].filter(record => record.field.type === 'file' && !record.active && !record.starting && isEligible(record.field) && !hasAnswer(record.field))
        .map(record => ({ id: record.id, field: fieldInfo(record.field) })),
    };
  }

  function beginBatch(message) {
    if (!enabled || !scanned || scannedUrl !== location.href || batch) return { started: false };
    const targets = new Map();
    for (const target of [...(message.targets || []), ...(message.uploadTargets || [])]) {
      const record = [...records.values()].find(item => item.id === target.id);
      if (record) targets.set(target.id, { record, signature: targetSignature(target.field) });
    }
    clearTimeout(batchFeedbackTimer);
    batchFeedback = '';
    batchPending = false;
    batch = { id: message.runId, sourceUrl: location.href, targets, stopping: false };
    if (window.top === window) {
      const keepalive = chrome.runtime.connect({ name: 'wc-batch' });
      batch.keepalive = keepalive;
      const heartbeat = () => { try { keepalive.postMessage({ type: 'heartbeat', runId: message.runId }); } catch { /* The worker has disconnected. */ } };
      batch.heartbeat = setInterval(heartbeat, 20_000);
      keepalive.onDisconnect.addListener(() => {
        if (batch?.id === message.runId) finishBatch({ runId: message.runId, stopped: true, errorMessage: 'The extension connection was interrupted. Reload the page and try again.' });
      });
      heartbeat();
    }
    updateBatchButton();
    return { started: true };
  }

  function attachBatchResumes(message) {
    if (!batch || batch.id !== message.runId) return { filled: 0, skipped: message.ids?.length || 0, failed: 0, unchecked: 0 };
    const result = { filled: 0, skipped: 0, failed: 0, unchecked: 0, firstError: '' };
    for (const id of message.ids || []) {
      const target = batch.targets.get(id);
      if (!target || target.record.field.type !== 'file') { result.skipped++; continue; }
      try {
        result[attachResume(target.record, message.resumeFile, target.signature, batch.sourceUrl)]++;
      } catch (error) {
        result.failed++;
        result.firstError ||= error.message || 'The resume could not be attached.';
      }
    }
    return result;
  }

  async function applyBatch(message) {
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
      if (answer === readValue(field) && (globalThis.WCFieldContext.choiceKind(field) || field.tagName === 'SELECT' || isSupportedCombobox(field))) {
        if (globalThis.WCFieldContext.choiceKind(field) === 'checkbox' && answer === false) result.unchecked++;
        else result.skipped++;
        continue;
      }
      const original = readValue(field);
      try {
        const written = await writeValue(field, answer);
        if (readValue(field) !== (written === undefined ? answer : written) || (field.validity && !field.validity.valid)) throw new Error('The page rejected an answer.');
        result.filled++;
        logUI('ui.filled', batch.id);
      } catch {
        if (!isSupportedCombobox(field) && !isAshbyAutocomplete(field) && !ashbyYesNo(field)) try { await writeValue(field, original); } catch { /* The page may have removed the field. */ }
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
    clearInterval(ending.heartbeat);
    ending.keepalive?.disconnect();
    updateBatchButton();
    if (window.top === window) {
      const { filled = 0, failed = 0, skipped = 0, unchecked = 0 } = message.summary || {};
      const userStopped = message.stopped && (!message.errorMessage || ['FILL stopped.', 'FILL ALL stopped.', 'Generation cancelled.'].includes(message.errorMessage));
      const detail = userStopped ? '' : message.errorMessage || message.fieldError || (failed ? `${failed} ${failed === 1 ? 'field could' : 'fields could'} not be filled.` : '');
      if (detail) toast('FILL needs attention', `${detail} ${filled ? `${filled} filled. ` : ''}${skipped ? `${skipped} skipped. ` : ''}${unchecked ? `${unchecked} left unchecked. ` : ''}Review the form before submitting.`, 'error', [], 20000);
      if (!detail) batchFeedback = message.stopped ? 'Stopped' : `${filled} filled`;
      updateBatchButton();
      clearTimeout(batchFeedbackTimer);
      batchFeedbackTimer = setTimeout(() => { batchFeedback = ''; if (!batch) updateBatchButton(); }, 3000);
    }
    return { finished: true };
  }

  function setEnabled(value) {
    enabled = value;
    if (enabled) { scan(); return; }
    resetScan('AI filling was switched off.');
    host?.remove();
  }

  const observationOptions = { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['type', 'disabled', 'readonly', 'hidden', 'inert', 'class', 'style', 'open', 'popover', 'name', 'id', 'for', 'title', 'placeholder', 'maxlength', 'min', 'max', 'step', 'pattern', 'required', 'autocomplete', 'aria-label', 'aria-labelledby', 'aria-describedby', 'aria-description', 'aria-required', 'aria-disabled', 'aria-readonly', 'contenteditable'] };
  const observer = new MutationObserver(mutations => {
    if (mutations.every(change => change.target === host || host?.contains(change.target))) return;
    scheduleScan();
    schedulePanelCheck();
  });
  const resizeObserver = new ResizeObserver(schedulePosition);
  let observedDocumentElement = document.documentElement;
  observer.observe(observedDocumentElement, observationOptions);
  window.addEventListener('scroll', schedulePosition, { capture: true, passive: true });
  window.addEventListener('resize', () => { scheduleScan(); schedulePosition(); }, { passive: true });
  document.addEventListener('focusin', () => { schedulePosition(); scheduleScan(); }, { passive: true });
  document.addEventListener('input', schedulePosition, { passive: true });
  window.addEventListener('load', scheduleScan, { once: true });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) scheduleScan(); });
  // Attaching a shadow root to an existing host does not mutate the document.
  // Discover these late-loaded components without repeatedly rescanning every field.
  setInterval(() => {
    if (document.documentElement !== observedDocumentElement) {
      observedDocumentElement = document.documentElement;
      observer.observe(observedDocumentElement, observationOptions);
      if (enabled) scheduleScan();
    }
    if (enabled && !host?.isConnected) scheduleScan();
    if (enabled && !document.hidden) schedulePanelCheck();
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
    } else if (message?.type === 'WC_TAB_SCAN_COUNT') {
      if (window.top === window && scanned && scannedUrl === location.href && Number.isInteger(message.count) && message.count >= 0) {
        scannedInputCount = message.count;
        localScannedCount = records.size;
        updatePanelStatus();
      }
      respond({ received: true });
    } else if (message?.type === 'WC_GET_PAGE_STATUS') {
      if (scanned && scannedUrl !== location.href) resetScan('The page changed. Scan it again to show fillable fields.');
      respond({ count: records.size, scanned, enabled });
    } else if (message?.type === 'WC_BATCH_SNAPSHOT') {
      void batchSnapshot().then(respond).catch(() => respond({ scanned: false, enabled }));
      return true;
    } else if (message?.type === 'WC_BATCH_BEGIN') {
      respond(beginBatch(message));
    } else if (message?.type === 'WC_BATCH_APPLY') {
      void applyBatch(message).then(respond).catch(() => respond({ filled: 0, skipped: 0, failed: message.answers?.length || 0, unchecked: 0, firstError: 'The page rejected the answers.' }));
      return true;
    } else if (message?.type === 'WC_BATCH_ATTACH_RESUME') {
      respond(attachBatchResumes(message));
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
