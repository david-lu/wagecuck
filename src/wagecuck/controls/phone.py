"""Bounded phone entry: national digits, international form, original formatting."""

import asyncio

from wagecuck.models import ApplicationError, Code
from wagecuck.phone_numbers import phone_candidates, phone_equivalent

PHONE_SETTLE_SECONDS = 0.35

PHONE_STATE = r"""e => {
  const visible = n => !!n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden';
  let scope = e;
  for (let parent = e.parentElement, depth = 0; parent && depth < 5; parent = parent.parentElement, depth++) {
    if (parent.matches('form, body, html') ||
        [...parent.querySelectorAll('input:not([type=hidden]), textarea')].some(n => n !== e &&
          n.autocomplete !== 'tel-country-code' && n.getAttribute('role') !== 'combobox') ||
        parent.querySelectorAll('[role=combobox], select').length > 1) break;
    scope = parent;
  }
  const selected = scope.querySelector(
    '[data-country-code][aria-selected=true], [data-dial-code][aria-selected=true], select option:checked');
  const countryInput = scope.querySelector('[autocomplete="tel-country-code"]');
  const explicitDial = countryInput?.value || selected?.value || '';
  const dial = selected?.getAttribute('data-dial-code') ||
    scope.querySelector('.iti__selected-dial-code')?.textContent ||
    (/^\+?\d{1,4}$/.test(explicitDial) ? explicitDial : '') ||
    selected?.textContent.match(/\+(\d{1,4})\s*$/)?.[1] || '';
  const region = selected?.getAttribute('data-country-code') ||
    (/^[A-Z]{2}$/i.test(selected?.value || '') ? selected.value.toUpperCase() : '');
  const referenced = (e.getAttribute('aria-errormessage') || '').split(/\s+/)
    .map(id => e.ownerDocument.getElementById(id)).filter(Boolean);
  const errors = [...scope.querySelectorAll('[role=alert], .field-error, .error-message, [data-error]'), ...referenced];
  return {value: e.value, region: region.toUpperCase(), dial,
    valid: (!e.willValidate || e.validity.valid) && e.getAttribute('aria-invalid') !== 'true' &&
      !errors.some(n => visible(n) && n.textContent.trim())};
}"""


async def matches_phone(page, target, action, value):
    state = await target.evaluate(PHONE_STATE)
    return state["valid"] and phone_equivalent(
        state["value"], value, state["region"] or None, state["dial"]
    )


async def write_phone(page, target, action, value):
    state = await target.evaluate(PHONE_STATE)
    for candidate in phone_candidates(value, state["region"] or None):
        await target.fill(candidate)
        await target.blur()
        await asyncio.sleep(PHONE_SETTLE_SECONDS)
        if await matches_phone(page, target, action, value):
            return
        # Some masks require individual keyboard events; do not repeat an ordinary
        # format rejection when the entered number was already retained.
        state = await target.evaluate(PHONE_STATE)
        if not phone_equivalent(state["value"], value, state["region"] or None, state["dial"]):
            await target.press("ControlOrMeta+A")
            await target.press("Backspace")
            await target.press_sequentially(candidate, delay=20)
            await target.blur()
            await asyncio.sleep(PHONE_SETTLE_SECONDS)
            if await matches_phone(page, target, action, value):
                return
    raise ApplicationError(
        Code.FIELD_FILL_FAILED, f"No accepted phone format for: {action.field.label}"
    )
