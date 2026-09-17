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


async def owned_selector(target: Target):
    return await target.evaluate(
        r"""e => [...new Set(['aria-controls', 'aria-owns'].flatMap(attribute =>
          (e.getAttribute(attribute) || '').split(/\s+/).filter(Boolean)))]
          .map(id => '#' + CSS.escape(id)).join(',')"""
    )


async def popup_scope(page, target, frame_index):
    owned = await owned_selector(target)
    if owned:
        return page.frames[frame_index].locator(owned)
    # Some components omit ARIA ownership but keep one popup beside one control.
    parent = target.locator("..")
    for _ in range(4):
        if await parent.count() != 1 or await parent.evaluate("e => e.matches('form, body, html')"):
            break
        if await parent.locator("[role=combobox]").count() > 1:
            break
        boxes = parent.get_by_role("listbox", include_hidden=True)
        if await boxes.count() == 1:
            return boxes
        parent = parent.locator("..")
    return None


async def option_locator(page, target: Target, action: Action | None = None, *, frame_index=None):
    index = action.field.frame if action is not None else frame_index
    scope = await popup_scope(page, target, index)
    if scope is None:
        scope = page.frames[index]
    return scope.get_by_role("option", disabled=False).filter(visible=True)


async def open_popup(page, target, *, frame_index):
    scope = await popup_scope(page, target, frame_index)
    options = await option_locator(page, target, frame_index=frame_index)
    if await target.get_attribute("aria-expanded") != "true" and (
        scope is None or not await options.count()
    ):
        await target.click()


async def close_popup(target):
    try:
        await target.press("Escape", timeout=500)
        if await target.get_attribute("aria-expanded") == "true":
            await target.click(timeout=500)
    except PlaywrightError:
        pass


async def unambiguous_option(matching, label):
    count = await matching.count()
    if count == 1:
        return matching
    rows = await matching.evaluate_all("""els => els.map(e => ({
      label: (e.textContent || '').trim().replace(/\\s+/g, ' '),
      value: e.getAttribute('data-value') || e.getAttribute('value') ||
        (e.hasAttribute('data-country-code') ?
          e.getAttribute('data-country-code') + ':' + (e.getAttribute('data-dial-code') || '') : '')
    }))""")
    # Repeated preferred/all-options entries are safe only with identical explicit values.
    if rows and rows[0]["value"] and len({(normalize(r["label"]), r["value"]) for r in rows}) == 1:
        return matching.first
    raise ApplicationError(Code.UNSUPPORTED_CONTROL, f"Ambiguous choices for {label}")


async def discover_options(page, field: FormField, *, timeout_ms: int = 1800) -> list[Option]:
    """Open one dynamic widget and capture the options it actually renders."""
    target = page.frames[field.frame].locator(f'[data-wagecuck-id="{field.id}"]')
    if not await target.count():
        return field.options
    try:
        await open_popup(page, target, frame_index=field.frame)
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
        await close_popup(target)


async def enrich_dynamic_options(page, snap: Snapshot) -> Snapshot:
    """Attach runtime choices before deterministic or model planning."""
    for field in snap.fields:
        if field.control_type == "dynamic_combobox" and not field.options:
            field.options = await discover_options(page, field)
    return snap


async def matches_combobox(page, target: Target, action: Action, expected):
    actual = await combobox_value(target)
    if actual.strip():
        if action.choice_labels and normalize(actual) in {
            normalize(label) for label in action.choice_labels
        }:
            return True
        if selected_value(expected, action) == selected_value(actual, action):
            return True
    scope = await popup_scope(page, target, action.field.frame)
    if scope is None:
        return False
    selected = scope.get_by_role(
        "option", name=option_name(expected, action), selected=True, exact=True, include_hidden=True
    )
    if not await selected.count():
        return False
    await unambiguous_option(selected, action.field.label)
    return True


async def write_combobox(page, target: Target, action: Action, value):
    await open_popup(page, target, frame_index=action.field.frame)
    try:
        if await target.evaluate("e => e.tagName === 'INPUT' && !e.readOnly"):
            await target.fill("" if action.random_choice else value)
        options = await option_locator(page, target, action)
        if action.random_choice:
            await options.first.wait_for(state="visible")
            candidates = [
                (index, label.strip())
                for index, label in enumerate(await options.all_text_contents())
                if label.strip()
                and not re.fullmatch(
                    r"(?:please )?(?:select|choose)(?: an? option)?", normalize(label)
                )
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
        option = await unambiguous_option(matching, action.field.label)
        await option.click()
    finally:
        await close_popup(target)
