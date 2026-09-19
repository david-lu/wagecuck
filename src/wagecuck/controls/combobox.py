from __future__ import annotations

import random
import re

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from wagecuck.models import Action, ApplicationError, Code, FormField, Option, Snapshot

from .base import Target, normalize


def accessible_name(label: str):
    clean = re.sub(r"[\s*✱]+$", "", label).strip()
    return re.compile(r"^" + re.escape(clean) + r"\s*[✱*]?$", re.IGNORECASE)


async def live_target(page, target: Target, action: Action) -> Target:
    """Rebind an editable widget that its framework replaced while typing."""
    if await target.count():
        return target
    frame = page.frames[action.field.frame]
    candidates = frame.get_by_role(
        "combobox", name=accessible_name(action.field.label), include_hidden=False
    )
    if await candidates.count() == 1:
        rebound = candidates.first
    else:
        controls = frame.locator('[role="combobox"]:visible')
        rows = []
        for index in range(await controls.count()):
            candidate = controls.nth(index)
            label = await candidate.evaluate(r"""element => {
              const text = node => (node?.innerText || node?.textContent || '')
                .trim().replace(/\s+/g, ' ');
              const referenced = (element.getAttribute('aria-labelledby') || '')
                .split(/\s+/).filter(Boolean)
                .map(id => text(element.getRootNode().getElementById?.(id) ||
                  document.getElementById(id))).join(' ').trim();
              return referenced || element.getAttribute('aria-label') ||
                [...(element.labels || [])].map(text).join(' ') ||
                element.getAttribute('placeholder') || '';
            }""")
            if normalize(re.sub(r"[\s*✱]+$", "", label)) == normalize(
                re.sub(r"[\s*✱]+$", "", action.field.label)
            ):
                rows.append(candidate)
        if len(rows) != 1:
            return target
        rebound = rows[0]
    await rebound.evaluate(
        "(element, id) => element.dataset.wagecuckId = id", action.field.id
    )
    return rebound


async def combobox_value(target: Target):
    """Read committed component state instead of transient search text."""
    return await target.evaluate("""e => {
      let parent = e.parentElement;
      for (let depth = 0; parent && depth < 4; depth++, parent = parent.parentElement) {
        if (parent.querySelectorAll('[role=combobox]').length > 1) break;
        const chosen = parent.querySelectorAll(
          '[class*="single-value"], [class*="singleValue"], '
          + '[class*="multi-value"], [class*="multiValue"], '
          + '[data-value]:not([data-value=""]), [aria-selected="true"]'
        );
        const visible = [...chosen].filter(node => node.getClientRects().length &&
          (node.innerText || node.textContent || '').trim());
        const committed = visible.filter(node => !visible.some(parent =>
          parent !== node && parent.contains(node)));
        if (committed.length) return committed.map(node =>
          node.innerText || node.textContent || '').join(String.fromCharCode(10));
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
    """Dismiss an open popup without toggling the control back open."""
    try:
        if await target.count() and await target.get_attribute("aria-expanded") == "true":
            await target.press("Escape", timeout=500)
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
        committed = {normalize(value) for value in actual.splitlines() if value.strip()}
        if action.choice_labels and committed.intersection({
            normalize(label) for label in action.choice_labels
        }):
            return True
        if any(
            selected_value(expected, action) == selected_value(value, action)
            for value in actual.splitlines()
            if value.strip()
        ):
            return True
        if action.source == "facts:country":
            expected_dial = re.search(r"\+\d{1,4}$", expected)
            if expected_dial and normalize(expected_dial.group()) in committed:
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
    try:
        await open_popup(page, target, frame_index=action.field.frame)
    except PlaywrightError:
        target = await live_target(page, target, action)
        await open_popup(page, target, frame_index=action.field.frame)
    try:
        if await target.evaluate("e => e.tagName === 'INPUT' && !e.readOnly"):
            query = "" if action.random_choice else value
            try:
                await target.fill(query)
            except PlaywrightError:
                target = await live_target(page, target, action)
                await target.fill(query)
            target = await live_target(page, target, action)
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
            target = await live_target(page, target, action)
            if not (await combobox_value(target)).strip() and await target.evaluate(
                "e => e.tagName === 'INPUT' && !e.readOnly"
            ):
                await open_popup(page, target, frame_index=action.field.frame)
                await target.fill(label)
                await target.press("ArrowDown")
                await target.press("Enter")
            return
        matching = options.and_(
            page.frames[action.field.frame].get_by_role(
                "option", name=option_name(value, action), exact=True
            )
        )
        await matching.first.wait_for(state="visible")
        option = await unambiguous_option(matching, action.field.label)
        await option.click()
        target = await live_target(page, target, action)
        if not (await combobox_value(target)).strip() and await target.evaluate(
            "e => e.tagName === 'INPUT' && !e.readOnly"
        ):
            # Some virtualized controls expose clickable options but commit only
            # through their keyboard state machine. The typed filter makes the
            # first enabled option the already validated exact match.
            await open_popup(page, target, frame_index=action.field.frame)
            await target.fill(value)
            await target.press("ArrowDown")
            await target.press("Enter")
    finally:
        await close_popup(target)
