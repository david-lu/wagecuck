(elements) => {
  const text = (el) => (el?.innerText || el?.textContent || '').trim().replace(/\s+/g, ' ');
  const labelText = (el) => {
    const copy = el.cloneNode(true);
    copy.querySelectorAll('input, select, textarea, button, [role=listbox], script, style').forEach(e => e.remove());
    return text(copy);
  };
  const visible = (el) => !!(el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
  const identify = (el) => {
    if (!el.dataset.wagecuckId) el.dataset.wagecuckId = 'wc' + (window.__wcCounter = (window.__wcCounter || 0) + 1);
    return el.dataset.wagecuckId;
  };
  // Structural paths survive DOM replacement without relying on transient control IDs.
  const domPath = (el) => {
    if (!el) return 'document';
    const parts = [];
    for (let node = el; node?.nodeType === Node.ELEMENT_NODE; node = node.parentElement) {
      const siblings = node.parentElement ? [...node.parentElement.children].filter(peer => peer.tagName === node.tagName) : [node];
      parts.unshift(node.tagName.toLowerCase() + ':' + siblings.indexOf(node));
    }
    const root = el.getRootNode();
    return (root.host ? domPath(root.host) + '/shadow/' : '') + parts.join('/');
  };
  const groupIdentity = (el, kind, questionContainer) => {
    if (!['radio', 'checkbox'].includes(kind)) return '';
    const root = el.getRootNode();
    const owner = domPath(root.host) + '|form:' + domPath(el.form);
    // HTML radio groups share a name, form owner, and tree root, even across fieldsets.
    // Custom ATS radios sometimes give every option a generated name. An explicit
    // fieldset/radiogroup is the stronger question boundary in that case.
    if (kind === 'radio') {
      if (el.matches('input[type="radio"]') && el.name) return owner + '|radio:' + el.name;
      if (questionContainer?.matches('fieldset, [role="radiogroup"]')) {
        return owner + '|question:' + domPath(questionContainer);
      }
      return el.name ? owner + '|radio:' + el.name : 'control:' + domPath(el);
    }
    if (questionContainer) return owner + '|question:' + domPath(questionContainer);
    if (el.name) return owner + '|checkbox:' + el.name;
    return 'control:' + domPath(el);
  };
  const referenced = (el) => (el.getAttribute('aria-labelledby') || '').split(/\s+/)
    .map(id => text(el.getRootNode().getElementById?.(id) || document.getElementById(id))).join(' ').trim();
  const container = (el) => el.closest('[data-field], [data-field-path], .application-question, .field, .form-field, .ashby-application-form-field-entry, .MuiFormControl-root');
  const captchaOwned = (el) => {
    const signature = [
      el.id, el.name, el.className, el.getAttribute('data-sitekey'),
      el.getAttribute('data-callback'), el.getAttribute('aria-label')
    ].filter(Boolean).join(' ');
    return /(?:^|[^a-z])(?:re)?captcha(?:[^a-z]|$)|hcaptcha|turnstile/i.test(signature) ||
      !!el.closest('.g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey][class*=captcha]');
  };
  const dynamicPopup = (el) => {
    if (el.getAttribute('aria-autocomplete') === 'list' || el.hasAttribute('list') ||
        el.hasAttribute('aria-controls') || el.hasAttribute('aria-owns') ||
        el.getAttribute('data-uxi-widget-type') === 'selectinput' ||
        el.hasAttribute('data-uxi-multiselect-id')) return true;
    let parent = el.parentElement;
    for (let depth = 0; parent && depth < 5; depth++, parent = parent.parentElement) {
      if (parent.matches('form, body, html')) break;
      if (parent.querySelectorAll('input:not([type=hidden]), textarea, select, [role=combobox]').length > 1) break;
      if (parent.querySelector(
        '[role=listbox], .dropdown-container, [class*=autocomplete][class*=menu], ' +
        '[class*=autocomplete][class*=result], [class*=suggestion]'
      )) return true;
    }
    return false;
  };
  const heading = (parent, el) => {
    if (!parent) return null;
    const root = el.getRootNode();
    const controls = parent.querySelectorAll(
      'input:not([type=hidden]), textarea, select, [role=combobox], [contenteditable=true]'
    );
    return [...parent.querySelectorAll('legend, label, .application-label, .question-label, .ashby-application-form-question-title, h3, h4')]
      .find(node => {
        const target = node.htmlFor && root.getElementById?.(node.htmlFor);
        const ownsOnlyControl = node.htmlFor && !target && controls.length === 1;
        return !node.contains(el) && (!node.htmlFor || target === el || ownsOnlyControl) &&
          !node.querySelector('input, select, textarea');
      });
  };
  const contextHeading = (el) => {
    let parent = el.parentElement;
    for (let depth = 0; parent && depth < 7; depth++, parent = parent.parentElement) {
      // Never borrow a neighbouring field's label or a page/form heading.
      if (parent.matches('form, body') || parent.querySelectorAll('input:not([type=hidden]), textarea, select').length > 1) break;
      const node = heading(parent, el);
      if (node && !/^(attach|upload file|choose file)\W*$/i.test(text(node))) return node;
    }
    return null;
  };
  const questionHeading = (parent, el) => {
    if (!parent) return null;
    return [...parent.querySelectorAll('legend, label, .application-label, .question-label, .ashby-application-form-question-title, h3, h4')]
      .find(node => {
        const target = node.htmlFor && el.getRootNode().getElementById?.(node.htmlFor);
        return !node.querySelector('input, select, textarea') && !(target && target.matches('input, select, textarea'));
      });
  };
  const label = (el) => {
    const labelled = referenced(el);
    const native = [...(el.labels || [])].map(labelText).join(' ');
    const contextual = text(heading(container(el), el) || contextHeading(el));
    const fileLabel = el.type === 'file' && (!native || /^(attach|upload file|choose file)\W*$/i.test(native))
      ? contextual || (/resume|cover.?letter|\bcv\b/i.test(el.name || el.id) ? el.name || el.id : '') : '';
    return (labelled || el.getAttribute('aria-label') || fileLabel || native || contextual || el.getAttribute('placeholder') || el.name || el.id || '').slice(0, 600);
  };
  return elements.filter(el => {
    // CAPTCHA widgets are challenges, not applicant questions. Challenge handling
    // owns them and may hand the browser to CapSolver or the user.
    if (captchaOwned(el)) return false;
    // Career-board search/filter widgets are not application fields.
    if (el.type === 'search' || el.closest('[role="search"]') || /^search(?:\b|[.])/i.test(label(el))) return false;
    if (el.matches(':disabled') || el.closest('[aria-disabled="true"]') || (el.readOnly && el.getAttribute('role') !== 'combobox') || el.type === 'hidden' || el.closest('[aria-hidden="true"]')) return false;
    if (el.type === 'file') return !el.closest('[hidden], [aria-hidden="true"]');
    return visible(el);
  }).map(el => {
    const role = el.getAttribute('role');
    const kind = el.type === 'file' ? 'file'
      : role === 'combobox' ? 'combobox'
      : ['checkbox', 'switch'].includes(role) ? 'checkbox'
      : role === 'radio' ? 'radio'
      : el.tagName === 'SELECT' ? 'select'
      : el.type || 'text';
    const controlType = kind === 'file' ? 'file_upload'
      : kind === 'combobox' ? 'dynamic_combobox'
      : kind === 'select' ? 'native_select'
      : kind === 'checkbox' ? 'checkbox'
      : kind === 'radio' ? 'radio'
      : kind === 'range' ? 'range'
      : el.isContentEditable ? 'contenteditable_text'
      : dynamicPopup(el) ? 'dynamic_combobox'
      : 'text_input';
    const groupEl = el.closest('fieldset, [role="radiogroup"], [role="group"]');
    const localHeading = questionHeading(container(el), el);
    const groupHeading = localHeading || questionHeading(groupEl, el);
    const group = ['radio', 'checkbox'].includes(kind) ? text(localHeading) || (groupEl ? referenced(groupEl) : '') || groupEl?.getAttribute('aria-label') || text(groupHeading) : '';
    const lab = label(el);
    let renderedSelection = '';
    if (controlType === 'dynamic_combobox') {
      let parent = el.parentElement;
      for (let depth = 0; parent && depth < 3; depth++, parent = parent.parentElement) {
        if (parent.matches('form, body, html') ||
            parent.querySelectorAll('[role=combobox]').length > 1) break;
        const selected = [...parent.querySelectorAll(
          '[class*="single-value"], [class*="singleValue"], [class*="multi-value"], [class*="multiValue"], [data-automation-id="selectedItem"]'
        )].filter(node => visible(node));
        const committed = selected.filter(node => !selected.some(parent =>
          parent !== node && parent.contains(node)));
        if (committed.length) { renderedSelection = committed.map(text).join(', '); break; }
      }
    }
    const hasRequired = (node) => !!node && (node.getAttribute('aria-required') === 'true' || (!/\boptional\b|\bnot required\b/i.test(labelText(node)) && (/\*|✱|\brequired\b/i.test(labelText(node)) || /(?:^|[ _-])required(?:[ _-]|$)/i.test(node.className || ''))));
    const required = el.required || el.getAttribute('aria-required') === 'true' || /\*|✱/.test(lab) || !!el.closest('[data-required="true"]') ||
      [...(el.labels || [])].some(hasRequired) || (['radio', 'checkbox'].includes(kind) && (hasRequired(groupEl) || hasRequired(groupHeading))) ||
      (kind === 'file' && hasRequired(contextHeading(el)));
    // Only nearby labels/help text, never control values or the whole page.
    const described = (el.getAttribute('aria-describedby') || '').split(/\s+/)
      .map(id => text(el.getRootNode().getElementById?.(id))).join(' ').trim();
    let scope = container(el) || groupEl || el.parentElement;
    if (scope?.matches('form, body') || (!groupEl && scope?.querySelectorAll('input:not([type=hidden]), select, textarea').length > 1)) scope = null;
    const context = ((scope ? labelText(scope) : lab) + ' ' + described).trim().slice(0, 1600);
    const validityErrors = el.willValidate ? [
      'valueMissing', 'typeMismatch', 'patternMismatch', 'tooLong', 'tooShort',
      'rangeUnderflow', 'rangeOverflow', 'stepMismatch', 'badInput', 'customError'
    ].filter(key => el.validity[key]) : [];
    return {
      id: identify(el), label: lab, name: el.name || el.id || '', kind, control_type: controlType,
      required, autocomplete: el.autocomplete || '', placeholder: el.placeholder || '',
      input_mode: el.inputMode || '', pattern: el.pattern || '', minimum: el.min || '', maximum: el.max || '', step: el.step || '',
      min_length: el.hasAttribute('minlength') && el.minLength >= 0 ? el.minLength : null,
      max_length: el.hasAttribute('maxlength') && el.maxLength >= 0 ? el.maxLength : null,
      group, group_id: groupIdentity(el, kind, groupEl || (localHeading ? container(el) : null)), fact_key: el.getAttribute('data-wagecuck-fact') || '',
      context, required_evidence: required ? 'DOM required marker or constraint' : /optional|not required/i.test(lab + ' ' + described) ? 'DOM optional marker' : '',
      requirement_status: required ? 'required' : /optional|not required/i.test(lab + ' ' + described) ? 'optional' : 'unknown',
      options: el.tagName === 'SELECT' ? [...el.options].filter(o => !o.matches(':disabled') && !o.closest('[hidden], [aria-hidden="true"], [aria-disabled="true"]') && o.value !== '').map(o => ({label: text(o), value: o.value})) : [],
      filled: kind === 'file' ? !!el.files?.length
        : ['checkbox', 'radio'].includes(kind) ? (role ? el.getAttribute('aria-checked') === 'true' : el.checked)
        : el.isContentEditable ? !!text(el) : !!(el.value || renderedSelection),
      invalid: !!(el.getAttribute('aria-invalid') === 'true' || validityErrors.length),
      validity_errors: validityErrors
    };
  });
}
