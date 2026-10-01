from __future__ import annotations

from playwright.async_api import Error as PlaywrightError

from wagecuck.models import Action, ApplicationError, Code

from .base import Target


async def set_choice(target: Target, value: bool) -> None:
    """Set a native or visually hidden choice input and verify the checked state."""
    role = await target.get_attribute("role")
    if role in ("checkbox", "radio", "switch"):
        checked = await target.get_attribute("aria-checked") == "true"
        if checked != value:
            await target.click()
        if (await target.get_attribute("aria-checked") == "true") != value:
            raise ValueError("ARIA check state not retained")
        return
    try:
        await target.set_checked(value)
    except PlaywrightError:
        if await target.is_checked() != value:
            label = target.locator("xpath=ancestor::label[1]")
            if await label.count():
                await label.click()
            else:
                await target.evaluate("e => e.click()")
    if await target.is_checked() != value:
        raise ValueError("Check state not retained")


async def write_choice(page, target: Target, action: Action, value):
    if not isinstance(value, bool):
        raise ApplicationError(
            Code.UNSUPPORTED_CONTROL, f"Boolean answer required for {action.field.label}"
        )
    if action.field.kind != "radio" or value:
        await set_choice(target, value)


async def matches_choice(page, target: Target, action: Action, value):
    if await target.get_attribute("role") in ("checkbox", "radio", "switch"):
        return (await target.get_attribute("aria-checked") == "true") == value
    return await target.is_checked() == value
