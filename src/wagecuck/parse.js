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
    if (kind === 'radio') return el.name ? owner + '|radio:' + el.name : 'control:' + domPath(el);
    if (questionContainer) return owner + '|question:' + domPath(questionContainer);
    if (el.name) return owner + '|checkbox:' + el.name;
    return 'control:' + domPath(el);
  };
  const referenced = (el) => (el.getAttribute('aria-labelledby') || '').split(/\s+/)
    .map(id => text(el.getRootNode().getElementById?.(id) || document.getElementById(id))).join(' ').trim();
  const container = (el) => el.closest('[data-field], [data-field-path], .application-question, .field, .form-field, .ashby-application-form-field-entry, .MuiFormControl-root');
  const heading = (parent, el) => {
    if (!parent) return null;
    return [...parent.querySelectorAll('legend, label, .application-label, .question-label, .ashby-application-form-question-title, h3, h4')]
      .find(node => !node.contains(el) && (!node.htmlFor || node.htmlFor === el.id) && !node.querySelector('input, select, textarea'));
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
    // Career-board search/filter widgets are not application fields.
    if (el.type === 'search' || el.closest('[role="search"]') || /^search(?:\b|[.])/i.test(label(el))) return false;
    if (el.matches(':disabled') || el.closest('[aria-disabled="true"]') || (el.readOnly && el.getAttribute('role') !== 'combobox') || el.type === 'hidden' || el.closest('[aria-hidden="true"]')) return false;
    if (el.type === 'file') return !el.closest('[hidden], [aria-hidden="true"]');
    return visible(el);
  }).map(el => {
    const kind = el.type === 'file' ? 'file' : el.getAttribute('role') === 'combobox' ? 'combobox' : el.tagName === 'SELECT' ? 'select' : el.type || 'text';
    const controlType = kind === 'file' ? 'file_upload'
      : kind === 'combobox' ? 'dynamic_combobox'
      : kind === 'select' ? 'native_select'
      : kind === 'checkbox' ? 'checkbox'
      : kind === 'radio' ? 'radio'
      : kind === 'range' ? 'range'
      : 'text_input';
    const groupEl = el.closest('fieldset, [role="radiogroup"], [role="group"]');
    const localHeading = questionHeading(container(el), el);
    const groupHeading = localHeading || questionHeading(groupEl, el);
    const group = ['radio', 'checkbox'].includes(kind) ? text(localHeading) || (groupEl ? referenced(groupEl) : '') || groupEl?.getAttribute('aria-label') || text(groupHeading) : '';
    const lab = label(el);
    let renderedSelection = '';
    if (kind === 'combobox') {
      let parent = el.parentElement;
      for (let depth = 0; parent && depth < 3; depth++, parent = parent.parentElement) {
        if (parent.querySelectorAll('[role=combobox]').length > 1) break;
        const selected = parent.querySelectorAll('[class*="single-value"], [class*="singleValue"]');
        if (selected.length === 1) { renderedSelection = text(selected[0]); break; }
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
    return {
      id: identify(el), label: lab, name: el.name || el.id || '', kind, control_type: controlType,
      required, autocomplete: el.autocomplete || '', placeholder: el.placeholder || '',
      input_mode: el.inputMode || '', pattern: el.pattern || '', minimum: el.min || '', maximum: el.max || '', step: el.step || '',
      min_length: el.hasAttribute('minlength') && el.minLength >= 0 ? el.minLength : null,
      max_length: el.hasAttribute('maxlength') && el.maxLength >= 0 ? el.maxLength : null,
      group, group_id: groupIdentity(el, kind, localHeading ? container(el) : groupEl), fact_key: el.getAttribute('data-wagecuck-fact') || '',
      context, required_evidence: required ? 'DOM required marker or constraint' : /optional|not required/i.test(lab + ' ' + described) ? 'DOM optional marker' : '',
      requirement_status: required ? 'required' : /optional|not required/i.test(lab + ' ' + described) ? 'optional' : 'unknown',
      options: el.tagName === 'SELECT' ? [...el.options].filter(o => !o.matches(':disabled') && !o.closest('[hidden], [aria-hidden="true"], [aria-disabled="true"]') && o.value !== '').map(o => ({label: text(o), value: o.value})) : [],
      filled: kind === 'file' ? !!el.files?.length : ['checkbox', 'radio'].includes(kind) ? el.checked : !!(el.value || renderedSelection),
      invalid: !!(el.getAttribute('aria-invalid') === 'true' || (el.willValidate && !el.validity.valid))
    };
  });
}
