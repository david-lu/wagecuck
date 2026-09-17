from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .ats import annotate, detect
from .controls.base import native_value_is_valid, normalize
from .controls.combobox import (
    enrich_dynamic_options,
)
from .controls.combobox import (
    matches_combobox as combobox_matches,
)
from .controls.native import match_option
from .controls.registry import handler_for
from .field_values import FieldValueError, normalize_field_value, render_field_value
from .models import Action, ApplicationError, Code, Control, FormField, Snapshot

__all__ = [
    "combobox_matches",
    "enrich_dynamic_options",
    "match_option",
    "normalize",
]

PARSE = Path(__file__).with_name("parse.js").read_text(encoding="utf-8")
CONTROL_SCRIPT = """els => els.filter(e => e.getClientRects().length && !e.disabled).map(e => {
 if (!e.dataset.wagecuckId) e.dataset.wagecuckId = 'wc' + (window.__wcCounter = (window.__wcCounter || 0) + 1);
 return {id:e.dataset.wagecuckId, label:(e.innerText || e.value || e.getAttribute('aria-label') || '').trim(), kind:e.tagName.toLowerCase(), action:e.getAttribute('data-wagecuck-action') || ''};
})"""


async def snapshot(page: Page) -> Snapshot:
    result = Snapshot(url=page.url, title=await page.title())
    for index, frame in enumerate(page.frames):
        try:
            await annotate(frame)
            fields = await frame.locator(
                "input:not([type=submit]):not([type=button]):not([type=reset]), textarea, select, [role=combobox]:not(input):not(select)"
            ).evaluate_all(PARSE)
            result.fields.extend(FormField(frame=index, **field) for field in fields)
            controls = await frame.locator(
                "button, a, input[type=submit], [role=button], [role=tab]"
            ).evaluate_all(CONTROL_SCRIPT)
            result.controls.extend(Control(frame=index, **control) for control in controls)
            result.text += "\n" + (await frame.locator("body").inner_text(timeout=2000))[:40000]
            errors = frame.locator("[role=alert], .field-error, .error-message, [data-error]")
            for error in await errors.all():
                if await error.is_visible():
                    result.errors.append(await error.inner_text())
        except PlaywrightError:
            if frame == page.main_frame:
                raise
    result.errors = [e.strip()[:400] for e in result.errors if e.strip()]
    return result


async def prepared_snapshot(page: Page) -> Snapshot:
    """Parse controls and open dynamic dropdowns so planning sees real choices."""
    return await enrich_dynamic_options(page, await snapshot(page))


def locator(page: Page, target: FormField | Control):
    if target.frame >= len(page.frames):
        raise ApplicationError(
            Code.NO_PROGRESS, "Application frame changed; restart before submission."
        )
    return page.frames[target.frame].locator(f'[data-wagecuck-id="{target.id}"]')


def entry_controls(snap: Snapshot):
    controls = [
        c
        for c in snap.controls
        if c.action == "start"
        or re.fullmatch(
            r"apply(?: now| for this (?:job|position)| to position)?|application|apply manually|i.m interested",
            c.label,
            re.IGNORECASE,
        )
    ]
    return sorted(controls, key=lambda c: c.label.casefold() != "apply manually")


async def dismiss_optional_cookies(page: Page, snap: Snapshot) -> bool:
    if "cookie" not in snap.text.casefold():
        return False
    reject = [c for c in snap.controls if c.label.casefold() in ("decline all", "reject all")]
    if len(reject) == 1:
        await locator(page, reject[0]).click()
        return True
    return False


def _control_handler(action: Action):
    handler = handler_for(action.field)
    if handler is None:
        raise ApplicationError(
            Code.UNSUPPORTED_CONTROL,
            f"Unsupported {action.field.control_type or action.field.kind}: {action.field.label}",
        )
    return handler


async def _matches_control(page, target, action, value):
    handler = _control_handler(action)
    matches = await handler.matches(page, target, action, value)
    return matches and (
        not handler.validates_natively or await native_value_is_valid(target)
    )


async def fill(page: Page, action: Action):
    field = action.field
    target = locator(page, field)
    try:
        handler = _control_handler(action)
        if field.kind == "combobox" and action.random_choice:
            value = ""  # The concrete canonical answer is assigned after selecting an option.
        else:
            action.value = normalize_field_value(field, action.value)
            value = render_field_value(field, action.value)
        await handler.write(page, target, action, value)
        expected = render_field_value(field, action.value) if action.random_choice else value
        if not await _matches_control(page, target, action, expected):
            raise ValueError("Value not retained")
    except ApplicationError:
        raise
    except (FieldValueError, PlaywrightError, ValueError) as exc:
        # Playwright exception bodies can contain applicant values.
        raise ApplicationError(
            Code.FIELD_FILL_FAILED, f"Could not verify field: {field.label}"
        ) from exc


async def click_and_settle(page: Page, control: Control):
    previous_pages = set(page.context.pages)
    await locator(page, control).click()
    # Accommodate an application link opening a new tab without an arbitrary long sleep.
    for _ in range(5):
        added = [p for p in page.context.pages if p not in previous_pages]
        if added:
            page = added[-1]
            await page.wait_for_load_state("domcontentloaded")
            break
        await asyncio.sleep(0.1)
    return page


@dataclass(frozen=True)
class FieldVerification:
    action: Action
    present: bool
    valid: bool
    code: Code | None = None
    message: str = ""


async def verify_action_results(page: Page, actions: list[Action]) -> list[FieldVerification]:
    """Read every action with the same control handlers used during filling."""
    results = []
    for action in actions:
        try:
            target = locator(page, action.field)
            if not await target.count():
                results.append(
                    FieldVerification(
                        action,
                        False,
                        False,
                        Code.NO_PROGRESS,
                        f"Field changed or disappeared: {action.field.label}",
                    )
                )
                continue
            value = render_field_value(action.field, action.value)
            valid = await _matches_control(page, target, action, value)
            results.append(
                FieldVerification(
                    action,
                    True,
                    valid,
                    None if valid else Code.VALIDATION_FAILED,
                    ""
                    if valid
                    else f"A later form update changed the value of: {action.field.label}",
                )
            )
        except ApplicationError as exc:
            results.append(
                FieldVerification(action, exc.code != Code.NO_PROGRESS, False, exc.code, str(exc))
            )
        except (FieldValueError, PlaywrightError, ValueError):
            results.append(
                FieldVerification(
                    action,
                    True,
                    False,
                    Code.VALIDATION_FAILED,
                    f"Could not verify field: {action.field.label}",
                )
            )
    return results


async def verify_actions(page: Page, actions: list[Action]):
    """Compatibility wrapper; disappearing conditional fields are replanned."""
    for result in await verify_action_results(page, actions):
        if result.present and not result.valid:
            raise ApplicationError(
                result.code or Code.VALIDATION_FAILED, result.message, [result.action.field.label]
            )


def detect_ats(url: str) -> str:
    return detect(url)


def blocker(snap: Snapshot) -> Code | None:
    text = snap.text.casefold()
    if detect(snap.url) == "greenhouse" and "error=true" in snap.url:
        return Code.JOB_CLOSED
    if detect(snap.url) == "jobvite" and parse_qs(urlsplit(snap.url).query).get("error") == ["404"]:
        return Code.JOB_CLOSED
    if re.search(
        r"job (?:is )?no longer available|position has been filled|job (?:has been|is) closed|job has expired|no longer accepting applications",
        text,
    ):
        return Code.JOB_CLOSED
    if re.search(
        r"verify you are human|complete the captcha|i.m not a robot|security verification|unusual traffic",
        text,
    ):
        return Code.CAPTCHA_REQUIRED
    if re.search(r"access denied|you have been blocked|request blocked", text):
        return Code.ACCESS_DENIED
    if any(f.kind == "password" for f in snap.fields):
        return Code.AUTH_REQUIRED
    return None


def confirmation(snap: Snapshot) -> str | None:
    # Neither a successful click nor a 2xx response proves an application was accepted.
    patterns = [
        r"your application has been (?:successfully )?(?:submitted|received)",
        r"application submitted successfully",
        r"thank you for applying",
        r"thanks for applying",
        r"we have received your application",
    ]
    for pattern in patterns:
        match = re.search(pattern, snap.text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None
