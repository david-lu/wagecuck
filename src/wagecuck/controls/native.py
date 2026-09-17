from __future__ import annotations

import re

from wagecuck.field_values import normalize_field_value
from wagecuck.models import Action, ApplicationError, Code

from .base import Target, normalize


def match_option(value: str | bool, options):
    target = normalize("Yes" if value is True else "No" if value is False else value)
    exact = [
        option for option in options if target in (normalize(option.label), normalize(option.value))
    ]
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


async def write_text(page, target: Target, action: Action, value):
    await target.fill(value)
    await target.blur()


async def matches_text(page, target: Target, action: Action, value):
    return await target.input_value() == value


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
