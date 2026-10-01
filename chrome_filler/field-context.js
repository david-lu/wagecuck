(() => {
  'use strict';

  const CONTROL_SELECTOR = 'input,textarea,select,[contenteditable="true"],[contenteditable=""],[role="textbox"]';
  const EXCLUDE_TEXT = 'input,textarea,select,option,button,[role="button"],script,style,noscript,template,svg,[data-file-name],[data-filename],.file-name,.filename,.uploaded-file-name';
  const GLOBAL_CONTAINER = /^(HTML|BODY|FORM|MAIN|NAV|HEADER|FOOTER)$/;
  const LOCAL_CONTAINER = 'fieldset,[role="group"],.form-group,.field,.question,[data-field],.field-container,.input-group';
  const SENSITIVE = /\b(password|passcode|otp|one time code|verification code|security code|credit card|card number|cvv|cvc|social security|social insurance|ssn|api key|access token|secret key|captcha)\b/i;

  function composedParent(node) {
    return node?.parentElement || node?.getRootNode?.()?.host || null;
  }

  function nearest(field, selector) {
    for (let node = field; node; node = composedParent(node)) if (node.matches?.(selector)) return node;
    return null;
  }

  function words(value) {
    return String(value || '').replace(/([a-z\d])([A-Z])/g, '$1 $2').replace(/([A-Z])([A-Z][a-z])/g, '$1 $2').replace(/[_\-.[\]:]+/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function cleanText(value) {
    return String(value || '').replace(/[\u200B-\u200D\uFEFF]/g, '').replace(/\s+/g, ' ').replace(/(?:\s*[＊*])+\s*$/g, '').replace(/^\s*[＊*]\s*/, '').replace(/^\s*(?:\d+[.)]|question \d+[:.]?)\s*/i, '').trim();
  }

  function unique(values) {
    const seen = new Set();
    return values.filter(value => {
      const key = cleanText(value).toLocaleLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function idElement(field, id) {
    const root = field.getRootNode();
    return root.getElementById?.(id) || field.ownerDocument.getElementById(id);
  }

  /** Collect label text without ever reading control values or button captions. */
  function safeText(root, { field, referenced = false, limit = 2400 } = {}) {
    if (!root) return '';
    const parts = [];
    let length = 0;
    let visited = 0;
    function walk(node) {
      if (++visited > 1000 || length > limit) return;
      if (node.nodeType === 3) {
        const text = cleanText(node.textContent);
        if (text && !/^[＊*]+$/.test(text) && !/^\(?required\)?$/i.test(text) && !/^[^\n]{1,160}\.(?:pdf|docx?|rtf|txt)$/i.test(text)) {
          parts.push(text);
          length += text.length + 1;
        }
        return;
      }
      if (node.nodeType !== 1 && node.nodeType !== 11) return;
      if (node.nodeType === 1) {
        if (node === field || node.matches(EXCLUDE_TEXT) || node.isContentEditable) return;
        if (/(?:^|[\s_-])(?:question-number|required-star|required-marker|asterisk)(?:$|[\s_-])/i.test(node.className || '')) return;
        if (node !== root && node.getAttribute('aria-hidden') === 'true') return;
        if (!referenced) {
          if (node.matches('[hidden],[inert]') || node.getAttribute('aria-hidden') === 'true') return;
          const style = getComputedStyle(node);
          if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || style.opacity === '0') return;
        }
        if (node.tagName === 'LABEL' && node.htmlFor && node.htmlFor !== field?.id && field) return;
      }
      for (const child of node.childNodes) walk(child);
      if (node.shadowRoot) for (const child of node.shadowRoot.childNodes) walk(child);
    }
    walk(root);
    return cleanText(parts.join(' ')).slice(0, limit);
  }

  function references(field, attribute) {
    return [...new Set((field.getAttribute(attribute) || '').split(/\s+/).filter(Boolean))].map(id => {
      const element = idElement(field, id);
      return { text: safeText(element, { field, referenced: true }), element };
    }).filter(item => item.text);
  }

  function hasOtherControl(root, field) {
    if (!root?.querySelectorAll) return false;
    const roots = [root];
    let visited = 0;
    for (let index = 0; index < roots.length && index < 20; index++) {
      const current = roots[index];
      const candidates = current.matches?.(CONTROL_SELECTOR) ? [current, ...current.querySelectorAll(CONTROL_SELECTOR)] : [...current.querySelectorAll(CONTROL_SELECTOR)];
      for (const candidate of candidates) {
        if (candidate === field || (candidate.tagName === 'INPUT' && candidate.type === 'hidden')) continue;
        if (candidate.isContentEditable && composedParent(candidate)?.isContentEditable) continue;
        return true;
      }
      for (const element of current.querySelectorAll('*')) {
        if (++visited > 600) return true; // A large ancestor is not a local question container.
        if (element.shadowRoot) roots.push(element.shadowRoot);
      }
      if (current.shadowRoot) roots.push(current.shadowRoot);
    }
    return false;
  }

  function associatedLabels(field) {
    const labels = [...(field.labels || [])];
    if (field.id && !labels.length) {
      // Compare strings directly; arbitrary IDs never enter a CSS selector.
      for (const label of field.getRootNode().querySelectorAll('label[for]')) if (label.htmlFor === field.id && !labels.includes(label)) labels.push(label);
    }
    const enclosing = nearest(field, 'label');
    if (enclosing && !labels.includes(enclosing)) labels.push(enclosing);
    labels.sort((a, b) => a.compareDocumentPosition(b) & 2 ? 1 : -1);
    return labels.map(label => ({ text: safeText(label, { field }), source: label.htmlFor ? 'label[for]' : 'nested-label' })).filter(item => item.text);
  }

  function groupHeading(group, field) {
    const names = references(group, 'aria-labelledby').map(item => item.text);
    const aria = cleanText(group.getAttribute('aria-label'));
    if (names.length) return unique(names).join(' ');
    if (aria) return aria;
    const legend = [...group.children].find(child => child.tagName === 'LEGEND');
    if (legend) return safeText(legend, { field });
    const heading = [...group.children].find(child => /^(H[2-6])$/.test(child.tagName) || /(?:^|[\s_-])(question|label|prompt|heading|title)(?:$|[\s_-])/i.test(child.className || ''));
    return heading ? safeText(heading, { field, limit: 500 }) : '';
  }

  function radioGroup(field) {
    if (field?.tagName !== 'INPUT' || field.type !== 'radio' || !field.name) return [];
    return [...field.getRootNode().querySelectorAll('input[type="radio"]')]
      .filter(option => option.name === field.name && option.form === field.form);
  }

  function radioOptions(field) {
    return radioGroup(field).filter(option => {
      if (!option.value.trim() || !option.isConnected || option.disabled || option.matches(':disabled') || option.getAttribute('aria-disabled') === 'true') return false;
      const rect = option.getBoundingClientRect();
      if (rect.width < 10 || rect.height < 10) return false;
      for (let node = option; node instanceof Element; node = composedParent(node)) {
        if (node.matches('[hidden],[inert]')) return false;
        const style = getComputedStyle(node);
        if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || style.opacity === '0') return false;
      }
      return true;
    });
  }

  function radioDetails(field) {
    const options = radioGroup(field);
    if (options.length < 2) return { container: null, label: '', source: '' };
    let container = composedParent(field);
    while (container && !options.every(option => container.contains(option))) container = composedParent(container);
    for (let depth = 0, candidate = container; candidate && depth < 5 && !GLOBAL_CONTAINER.test(candidate.tagName); candidate = composedParent(candidate), depth++) {
      const label = cleanText(groupHeading(candidate, field));
      if (label) return { container: candidate, label: label.slice(0, 500), source: candidate.tagName === 'FIELDSET' ? 'fieldset-legend' : 'group-label' };
    }
    return { container, label: '', source: '' };
  }

  function radioOptionLabel(option) {
    return associatedLabels(option).map(item => item.text).join(' ') || cleanText(option.getAttribute('aria-label')) || option.value;
  }

  function tableLabel(field) {
    const cell = nearest(field, 'td,th');
    const row = cell && nearest(cell, 'tr');
    if (!cell || !row) return '';
    const cells = [...row.children].filter(child => ['TH', 'TD'].includes(child.tagName));
    const index = cells.indexOf(cell);
    const preceding = cells.slice(0, index).reverse().find(candidate => candidate.tagName === 'TH' && !hasOtherControl(candidate, field));
    if (preceding) return safeText(preceding, { field, limit: 500 });
    const table = nearest(row, 'table');
    const columnHeadings = table?.querySelector('thead tr')?.children;
    const column = columnHeadings?.[index];
    return column?.tagName === 'TH' ? safeText(column, { field, limit: 500 }) : '';
  }

  function definitionLabel(field) {
    const definition = nearest(field, 'dd');
    let sibling = definition?.previousElementSibling;
    while (sibling && sibling.tagName === 'DT') {
      const text = safeText(sibling, { field, limit: 500 });
      if (text) return text;
      sibling = sibling.previousElementSibling;
    }
    return '';
  }

  function siblingCandidate(node, field, container, direction) {
    if (!node) return '';
    if (node.nodeType === 3) {
      const text = cleanText(node.textContent);
      return direction === 'previous' && /\p{L}/u.test(text) && !/^\(?required\)?$/i.test(text) ? text.slice(0, 500) : '';
    }
    if (node.nodeType !== 1 || node.tagName === 'H1' || node.matches(EXCLUDE_TEXT)) return '';
    if (node.tagName === 'LABEL' && node.htmlFor && node.htmlFor !== field.id) return '';
    const descriptiveIds = new Set((field.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    if (descriptiveIds.has(node.id) || node.matches('[role="alert"],[role="status"]') || /(?:^|[\s_-])(help|helper|hint|description|instructions?|errors?|validation|message)(?:$|[\s_-])/i.test(`${node.className || ''} ${node.id || ''}`)) return '';
    if (hasOtherControl(node, field)) return '';
    const text = safeText(node, { field, limit: 501 });
    if (!text || text.length > 500 || !/[\p{L}\p{N}]/u.test(text)) return '';
    const labelish = /(?:^|[\s_-])(label|question|prompt|caption|title)(?:$|[\s_-])/i.test(`${node.className || ''} ${node.id || ''}`);
    if (direction === 'next' && node.tagName !== 'LABEL' && !labelish) return '';
    if (GLOBAL_CONTAINER.test(container?.tagName || '') && /^H[2-6]$/.test(node.tagName) && !labelish && !text.includes('?')) return '';
    if (GLOBAL_CONTAINER.test(container?.tagName || '') && node.tagName === 'DIV' && !labelish && !text.includes('?')) return '';
    if (GLOBAL_CONTAINER.test(container?.tagName || '') && node.tagName === 'P' && !labelish && !text.includes('?')) return '';
    return text;
  }

  function questionStrength(node, text) {
    if (node?.nodeType === 1) {
      if (node.tagName === 'LABEL' || /(?:^|[\s_-])(label|question|prompt|caption)(?:$|[\s_-])/i.test(`${node.className || ''} ${node.id || ''}`)) return 6;
      if (/^H[2-6]$/.test(node.tagName)) return 5;
    }
    if (text.includes('?') || /^(why|how|what|when|where|tell us|describe|explain|please describe)\b/i.test(text)) return 4;
    return 1;
  }

  function nearbyLabel(field) {
    const definition = definitionLabel(field);
    if (definition) return { text: definition, source: 'definition-term' };
    const table = tableLabel(field);
    if (table) return { text: table, source: 'table-header' };
    let current = field;
    for (let depth = 0; depth < 4 && current; depth++, current = composedParent(current)) {
      const container = composedParent(current);
      if (!container) break;
      let previous = current.previousSibling;
      let candidate = null;
      for (let count = 0; count < 6 && previous; count++, previous = previous.previousSibling) {
        if (previous.nodeType === 1 && hasOtherControl(previous, field)) break;
        const text = siblingCandidate(previous, field, container, 'previous');
        if (text) {
          const strength = questionStrength(previous, text);
          if (!candidate || strength > candidate.strength) candidate = { text, strength, source: 'preceding-question' };
        }
      }
      if (candidate) return candidate;
      if (!hasOtherControl(container, field)) {
        let following = current.nextSibling;
        for (let count = 0; count < 3 && following; count++, following = following.nextSibling) {
          if (following.nodeType === 1 && hasOtherControl(following, field)) break;
          const text = siblingCandidate(following, field, container, 'next');
          if (text) return { text, source: 'following-label' };
        }
      }
      if (!GLOBAL_CONTAINER.test(container.tagName) && (container.matches(LOCAL_CONTAINER) || container.hasAttribute('aria-label') || container.hasAttribute('aria-labelledby')) && !hasOtherControl(container, field)) {
        const heading = groupHeading(container, field);
        if (heading) return { text: heading, source: container.tagName === 'FIELDSET' ? 'fieldset-legend' : 'group-label' };
      }
      if (GLOBAL_CONTAINER.test(container.tagName)) break;
    }
    return null;
  }

  function humanizeIdentifier(value) {
    const text = words(value).replace(/\b(?:input|textarea|textbox|field|answer)\b/gi, ' ').replace(/\b\d+\b/g, '').replace(/\s+/g, ' ').trim();
    if (!text || /^[a-f\d\s]{16,}$/i.test(text) || /^r\d+$/i.test(text) || !/[a-zA-Z]/.test(text)) return '';
    return text.split(' ').map(word => /^[A-Z]{2,}$/.test(word) ? word : word[0].toUpperCase() + word.slice(1)).join(' ');
  }

  function labelDetails(field) {
    if (field.type === 'radio') {
      const group = radioDetails(field);
      if (group.label) return { label: group.label, sources: [group.source] };
    }
    const labelled = references(field, 'aria-labelledby');
    if (labelled.length) return { label: unique(labelled.map(item => item.text)).join(' ').slice(0, 500), sources: ['aria-labelledby'] };
    const aria = cleanText(field.getAttribute('aria-label'));
    if (aria) return { label: aria.slice(0, 500), sources: ['aria-label'] };
    const labels = associatedLabels(field);
    if (labels.length) return { label: unique(labels.map(item => item.text)).join(' ').slice(0, 500), sources: unique(labels.map(item => item.source)) };
    const nearby = nearbyLabel(field);
    if (nearby) return { label: nearby.text.slice(0, 500), sources: [nearby.source] };
    for (const [source, value] of [['placeholder', field.getAttribute('placeholder')], ['title', field.getAttribute('title')], ['name', humanizeIdentifier(field.getAttribute('name'))], ['id', humanizeIdentifier(field.id)]]) {
      const label = cleanText(value);
      if (label) return { label: label.slice(0, 500), sources: [source] };
    }
    return { label: field.tagName === 'SELECT' ? 'Dropdown' : field.type === 'checkbox' ? 'Checkbox' : 'Text field', sources: ['fallback'] };
  }

  function labelFor(field) {
    return labelDetails(field).label;
  }

  function fieldInfo(field) {
    const details = labelDetails(field);
    const context = references(field, 'aria-describedby').map(item => item.text);
    const ariaDescription = cleanText(field.getAttribute('aria-description'));
    if (ariaDescription) context.push(ariaDescription);
    for (let group = composedParent(field), depth = 0; group && depth < 4; group = composedParent(group), depth++) {
      if (GLOBAL_CONTAINER.test(group.tagName)) break;
      if (group.matches(LOCAL_CONTAINER) || group.hasAttribute('aria-label') || group.hasAttribute('aria-labelledby')) {
        const heading = groupHeading(group, field);
        if (heading && heading !== details.label) context.push(heading);
      }
      if (!hasOtherControl(group, field)) {
        let text = safeText(group, { field, limit: 2400 });
        if (text.startsWith(details.label)) text = text.slice(details.label.length).trim();
        if (text) context.push(text);
        if (text || group.matches(LOCAL_CONTAINER)) break;
      }
    }
    const options = field.tagName === 'SELECT' ? [...field.options]
      .filter(option => option.value.trim() && !option.disabled && !option.hidden && !option.closest('optgroup[disabled]'))
      .map(option => ({ value: option.value, label: cleanText(option.textContent).slice(0, 200) || option.value }))
      : field.type === 'radio' ? radioOptions(field).map(option => ({ value: option.value, label: radioOptionLabel(option).slice(0, 200) })) : undefined;
    return {
      label: details.label,
      type: field.tagName === 'SELECT' ? 'select' : field.tagName === 'TEXTAREA' || field.isContentEditable ? 'textarea' : field.type || 'text',
      placeholder: field.getAttribute('placeholder') || (field.tagName === 'SELECT' ? cleanText([...field.options].find(option => !option.value.trim())?.textContent) : '') || '',
      required: Boolean(field.required || field.getAttribute('aria-required') === 'true'),
      maxLength: field.maxLength > 0 ? field.maxLength : null,
      min: field.getAttribute('min') || null,
      max: field.getAttribute('max') || null,
      step: field.getAttribute('step') || null,
      pattern: field.getAttribute('pattern') || null,
      context: unique(context).join('\n').slice(0, 2400),
      currentValue: field.type === 'checkbox' ? String(field.checked) : field.type === 'radio' ? radioGroup(field).find(option => option.checked)?.value || '' : 'value' in field ? field.value : field.innerText || '',
      options,
      name: field.getAttribute('name') || '',
      id: field.id || '',
      autocomplete: field.getAttribute('autocomplete') || '',
      accept: field.type === 'file' ? field.accept : '',
      labelSources: details.sources,
    };
  }

  function isSensitiveField(field) {
    if (field.type === 'password') return true;
    const autocomplete = (field.getAttribute('autocomplete') || '').toLowerCase();
    if (/(^|\s)(cc-[\w-]+|one-time-code|current-password|new-password)(\s|$)/.test(autocomplete)) return true;
    const identifiers = words(`${field.getAttribute('name') || ''} ${field.id || ''} ${autocomplete}`);
    if (SENSITIVE.test(identifiers) || /\b(?:cc number|cc csc|cc exp|sin)\b/i.test(identifiers)) return true;
    const label = words(labelFor(field));
    const narrative = /\b(describe|explain|experience|approach|discuss|tell us|how (?:do|would|did)|why)\b/i.test(label);
    if (narrative) return false;
    return SENSITIVE.test(words(`${label} ${field.getAttribute('placeholder') || ''} ${field.getAttribute('title') || ''}`));
  }

  function isSearchField(field) {
    if (field.type === 'search' || nearest(field, '[role="search"]')) return true;
    if (field.tagName !== 'INPUT') return false;
    const label = words(labelFor(field));
    if (/\b(describe|explain|experience|approach|strategy|tell us|why|how)\b/i.test(label)) return false;
    const identifiers = [field.getAttribute('name'), field.id].map(value => words(value).replace(/\s/g, '').toLowerCase());
    if (identifiers.some(value => /^(?:(?:site|global|job|jobs|role|roles|position|positions|opening|openings|posting|postings|candidate)?search(?:query|term|keywords|text|input)?|filter(?:query|term|text)?|q)$/.test(value))) return true;
    return /^(?:(?:keyword|site|job|role|position|opening|posting|candidate) )?search(?: (?:jobs|roles|openings|positions|postings|candidates|results|by .+|for .+))?\s*[.…]*$/i.test(label) || /^filter(?: (?:jobs|roles|openings|positions|postings|results|by .+))?\s*[.…]*$/i.test(label);
  }

  function isConsentField(field) {
    if (!['checkbox', 'radio'].includes(field.type)) return false;
    const description = words(`${labelFor(field)} ${fieldInfo(field).context}`);
    return /\b(consent|agree|accept terms|acknowledg\w*|certif\w*|attest\w*|privacy policy|terms of service|terms and conditions|authorize\b|have read|read and understand|electronic signature)\b/i.test(description);
  }

  globalThis.WCFieldContext = Object.freeze({ labelFor, fieldInfo, radioGroup, radioOptions, radioDetails, isSensitiveField, isSearchField, isConsentField });
})();
