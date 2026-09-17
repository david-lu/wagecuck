from __future__ import annotations

import re
from pathlib import Path

from wagecuck.field_values import normalize_field_value
from wagecuck.models import Action, ApplicationError, Code

from .base import Target, normalize


def match_option(value: str | bool, options):
    target = normalize("Yes" if value is True else "No" if value is False else value)
    exact = [option for option in options if target in (normalize(option.label), normalize(option.value))]
    if len(exact) == 1:
        return exact[0]
    if target in ("prefer not to say", "decline to self identify", "i do not wish to answer"):
        matches = [
            option
            for option in options
            if re.search(r"decline|prefer not|do not wish", option.label, re.IGNORECASE)
        ]
        if len(matches) == 1:
            return matches[0]
    return None


async def write_file(page, target: Target, action: Action, value):
    await target.set_input_files(value)


async def matches_file(page, target: Target, action: Action, value):
    return await target.evaluate("e => [...e.files].map(file => file.name)") == [Path(value).name]


async def write_select(page, target: Target, action: Action, value):
    option = match_option(value, action.field.options)
    if option is None:
        raise ApplicationError(
            Code.UNSUPPORTED_CONTROL, f"No unambiguous option for {action.field.label}"
        )
    await target.select_option(value=option.value)


async def matches_select(page, target: Target, action: Action, value):
    option = match_option(value, action.field.options)
    return option is not None and await target.input_value() == option.value


async def phone_matches(target: Target, expected):
    actual = await target.evaluate("""e => {
      const value = e.value || '';
      const separate = e.closest('.iti--separate-dial-code');
      const dial = separate?.querySelector('.iti__selected-dial-code')?.textContent || '';
      return !value.trim().startsWith('+') && dial ? dial + value : value;
    }""")
    return re.sub(r"\D", "", actual) == re.sub(r"\D", "", expected)


async def write_text(page, target: Target, action: Action, value):
    await target.fill(value)
    await target.blur()
    if action.field.kind == "tel" and not await matches_text(page, target, action, value):
        await target.press("ControlOrMeta+A")
        await target.press("Backspace")
        await target.press("ControlOrMeta+A")
        await target.press_sequentially(value, delay=20)
        await target.blur()


async def matches_text(page, target: Target, action: Action, value):
    return await target.input_value() == value or (
        action.field.kind == "tel" and await phone_matches(target, value)
    )


async def write_range(page, target: Target, action: Action, value):
    await target.evaluate(
        """(element, value) => {
        const setter = Object.getOwnPropertyDescriptor(
            element.ownerDocument.defaultView.HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(element, value);
        element.dispatchEvent(new Event('input', {bubbles: true}));
        element.dispatchEvent(new Event('change', {bubbles: true}));
    }""",
        value,
    )
    await target.blur()


async def matches_number(page, target: Target, action: Action, value):
    actual = await target.input_value()
    return normalize_field_value(action.field, actual) == normalize_field_value(action.field, value)