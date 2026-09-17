from __future__ import annotations

import random
import re

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from wagecuck.models import Action, ApplicationError, Code, FormField, Option, Snapshot

from .base import Target, normalize


async def combobox_value(target: Target):
    """Read committed component state instead of transient search text."""
    return await target.evaluate("""e => {
      let parent = e.parentElement;
      for (let depth = 0; parent && depth < 4; depth++, parent = parent.parentElement) {
        if (parent.querySelectorAll('[role=combobox]').length > 1) break;
        const chosen = parent.querySelectorAll(
          '[class*="single-value"], [class*="singleValue"], [data-value], [aria-selected="true"]'
        );
        const visible = [...chosen].filter(node => node.getClientRects().length);
        if (visible.length === 1) return visible[0].innerText || visible[0].textContent || '';
      }
      return e.value || e.getAttribute('aria-valuetext') || e.innerText || '';
    }""")


def selected_value(value, action):
    if action.source == "facts:country":
        value = re.sub(r"\s*\+\d{1,4}$", "", value)
    return normalize(value)


def option_name(value, action):
    if action.choice_labels:
        return re.compile(
            r"^(?:"
            + "|".join(re.escape(label).replace("/", r"/") for label in action.choice_labels)
            + r")$",
            re.IGNORECASE,
        )
    return (
        re.compile(r"^" + re.escape(value) + r"(?:\s*\+\d{1,4})?$", re.IGNORECASE)
        if action.source == "facts:country"
        else value
    )


async def option_locator(page, target: Target, action: Action | None = None, *, frame_index=None):
    index = action.field.frame if action is not None else frame_index
    frame = page.frames[index]
    owned = await target.evaluate(
        r"""e => [...new Set(['aria-controls', 'aria-owns'].flatMap(attribute =>
          (e.getAttribute(attribute) || '').split(/\s+/).filter(Boolean)))]
          .map(id => '#' + CSS.escape(id)).join(',')"""
    )
    scope = frame.locator(owned) if owned else frame
    return scope.get_by_role("option", disabled=False).filter(visible=True)


async def discover_options(page, field: FormField, *, timeout_ms: int = 1800) -> list[Option]:
    """Open one dynamic widget and capture the options it actually renders."""
    target = page.frames[field.frame].locator(f'[data-wagecuck-id="{field.id}"]')
    if not await target.count():
        return field.options
    try:
        await target.click()
        options = await option_locator(page, target, frame_index=field.frame)
        await options.first.wait_for(state="visible", timeout=timeout_ms)
        rows = await options.evaluate_all(r"""elements => elements.map(element => ({
          label: (element.innerText || element.textContent || '').trim().replace(/\s+/g, ' '),
          value: element.getAttribute('data-value') || element.getAttribute('value') ||
            element.id || (element.innerText || element.textContent || '').trim()
        })).filter(option => option.label)""")
        unique = {}
        for row in rows:
            unique.setdefault(normalize(row["label"]), Option(**row))
        return list(unique.values())
    except (PlaywrightError, PlaywrightTimeout):
        return field.options
    finally:
        try:
            await target.press("Escape", timeout=500)
        except PlaywrightError:
            pass


async def enrich_dynamic_options(page, snap: Snapshot) -> Snapshot:
    """Attach runtime choices before deterministic or model planning."""
    for field in snap.fields:
        if field.control_type == "dynamic_combobox" and not field.options:
            field.options = await discover_options(page, field)
    return snap


async def matches_combobox(page, target: Target, action: Action, expected):
    actual = await combobox_value(target)
    if not actual.strip():
        return False
    if action.choice_labels and normalize(actual) in {
        normalize(label) for label in action.choice_labels
    }:
        return True
    if selected_value(expected, action) == selected_value(actual, action):
        return True
    if action.source != "facts:country":
        return False
    await target.click()
    try:
        options = await option_locator(page, target, action)
        selected = options.and_(
            page.frames[action.field.frame].get_by_role(
                "option", name=option_name(expected, action), selected=True, exact=True
            )
        )
        await options.first.wait_for(state="visible")
        return await selected.count() == 1
    finally:
        await target.press("Escape")


async def write_combobox(page, target: Target, action: Action, value):
    await target.click()
    if await target.evaluate("e => e.tagName === 'INPUT' && !e.readOnly"):
        await target.fill("" if action.random_choice else value)
    options = await option_locator(page, target, action)
    if action.random_choice:
        await options.first.wait_for(state="visible")
        candidates = [
            (index, label.strip())
            for index, label in enumerate(await options.all_text_contents())
            if label.strip()
            and not re.fullmatch(r"(?:please )?(?:select|choose)(?: an? option)?", normalize(label))
        ]
        if not candidates:
            raise ApplicationError(
                Code.UNSUPPORTED_CONTROL, f"No source choices for {action.field.label}"
            )
        index, label = random.choice(candidates)
        await options.nth(index).click()
        action.value = label
        action.choice_labels = [label]
        return
    matching = options.and_(
        page.frames[action.field.frame].get_by_role(
            "option", name=option_name(value, action), exact=True
        )
    )
    await matching.first.wait_for(state="visible")
    if await matching.count() != 1:
        raise ApplicationError(
            Code.UNSUPPORTED_CONTROL, f"Ambiguous choices for {action.field.label}"
        )
    await matching.click()