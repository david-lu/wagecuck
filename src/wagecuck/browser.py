from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .ats import annotate, detect
from .field_values import FieldValueError, normalize_field_value, render_field_value
from .models import Action, ApplicationError, Code, Control, FormField, Snapshot

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


def normalize(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


def match_option(value: str | bool, options):
    target = normalize("Yes" if value is True else "No" if value is False else value)
    exact = [o for o in options if target in (normalize(o.label), normalize(o.value))]
    if len(exact) == 1:
        return exact[0]
    # Declining disclosure is equivalent only to other explicit decline options.
    if target in ("prefer not to say", "decline to self identify", "i do not wish to answer"):
        matches = [
            o
            for o in options
            if re.search(r"decline|prefer not|do not wish", o.label, re.IGNORECASE)
        ]
        if len(matches) == 1:
            return matches[0]
    return None


async def combobox_value(target):
    # React Select clears its search input after selection and renders the selected
    # value alongside it. Never mistake the transient search text for a selection.
    return await target.evaluate("""e => {
      let parent = e.parentElement;
      for (let depth = 0; parent && depth < 3; depth++, parent = parent.parentElement) {
        if (parent.querySelectorAll('[role=combobox]').length > 1) break;
        const chosen = parent.querySelectorAll('[class*="single-value"], [class*="singleValue"]');
        if (chosen.length === 1) return chosen[0].innerText;
      }
      return e.value || e.getAttribute('aria-valuetext') || e.innerText || '';
    }""")


async def phone_matches(target, expected):
    actual = await target.evaluate("""e => {
      const value = e.value || '';
      const separate = e.closest('.iti--separate-dial-code');
      const dial = separate?.querySelector('.iti__selected-dial-code')?.textContent || '';
      return !value.trim().startsWith('+') && dial ? dial + value : value;
    }""")
    return re.sub(r"\D", "", actual) == re.sub(r"\D", "", expected)


def selected_value(value, action):
    if action.source == "facts:country":
        value = re.sub(r"\s*\+\d{1,4}$", "", value)
    return normalize(value)


def option_name(value, action):
    if action.choice_labels:
        return re.compile(
            r"^(?:"
            + "|".join(re.escape(label).replace("/", r"\/") for label in action.choice_labels)
            + r")$",
            re.IGNORECASE,
        )
    return (
        re.compile(r"^" + re.escape(value) + r"(?:\s*\+\d{1,4})?$", re.IGNORECASE)
        if action.source == "facts:country"
        else value
    )


async def combobox_options(page: Page, target, action: Action):
    """Use the widget's owned popup for every selection and verification path."""
    frame = page.frames[action.field.frame]
    owned = await target.evaluate(
        """e => [...new Set(['aria-controls', 'aria-owns'].flatMap(attribute =>
          (e.getAttribute(attribute) || '').split(/\\s+/).filter(Boolean)))]
          .map(id => '#' + CSS.escape(id)).join(',')"""
    )
    scope = frame.locator(owned) if owned else frame
    return scope.get_by_role("option", disabled=False).filter(visible=True)


async def combobox_matches(page, target, action, expected):
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
    # A dial code by itself is ambiguous: inspect the widget's selected option.
    await target.click()
    try:
        options = await combobox_options(page, target, action)
        selected = options.and_(
            page.frames[action.field.frame].get_by_role(
                "option", name=option_name(expected, action), selected=True, exact=True
            )
        )
        await options.first.wait_for(state="visible")
        return await selected.count() == 1
    finally:
        await target.press("Escape")


async def set_choice(target, value: bool) -> None:
    """Set native and visually hidden custom choice inputs, then verify state."""
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


async def native_value_is_valid(target) -> bool:
    """Use the browser's own type/pattern/range/length validation when available."""
    return await target.evaluate("e => !e.willValidate || e.validity.valid")


async def _write_file(page, target, action, value):
    await target.set_input_files(value)


async def _matches_file(page, target, action, value):
    return await target.evaluate("e => [...e.files].map(file => file.name)") == [Path(value).name]


async def _write_select(page, target, action, value):
    option = match_option(value, action.field.options)
    if option is None:
        raise ApplicationError(
            Code.UNSUPPORTED_CONTROL, f"No unambiguous option for {action.field.label}"
        )
    await target.select_option(value=option.value)


async def _matches_select(page, target, action, value):
    option = match_option(value, action.field.options)
    return option is not None and await target.input_value() == option.value


async def _write_choice(page, target, action, value):
    if not isinstance(value, bool):
        raise ApplicationError(
            Code.UNSUPPORTED_CONTROL, f"Boolean answer required for {action.field.label}"
        )
    if action.field.kind != "radio" or value:
        await set_choice(target, value)


async def _matches_choice(page, target, action, value):
    return await target.is_checked() == value


async def _write_combobox(page, target, action, value):
    await target.click()
    if await target.evaluate("e => e.tagName === 'INPUT' && !e.readOnly"):
        await target.fill("" if action.random_choice else value)
    options = await combobox_options(page, target, action)
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


async def _write_text(page, target, action, value):
    await target.fill(value)
    await target.blur()
    if action.field.kind == "tel" and not await _matches_text(page, target, action, value):
        # Some phone masks mishandle a bulk input event. Retry with keystrokes.
        await target.press("ControlOrMeta+A")
        await target.press("Backspace")
        await target.press("ControlOrMeta+A")
        await target.press_sequentially(value, delay=20)
        await target.blur()


async def _matches_text(page, target, action, value):
    return await target.input_value() == value or (
        action.field.kind == "tel" and await phone_matches(target, value)
    )


async def _write_range(page, target, action, value):
    # Playwright fill() does not accept range inputs. Use the native setter and
    # standard events, then read back the value to catch clamping or step rounding.
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


async def _matches_number(page, target, action, value):
    actual = await target.input_value()
    return normalize_field_value(action.field, actual) == normalize_field_value(action.field, value)


# Small function pairs keep writing and readback consistent without widget classes.
_CONTROL_HANDLERS = {
    "file": (_write_file, _matches_file),
    "select": (_write_select, _matches_select),
    "checkbox": (_write_choice, _matches_choice),
    "radio": (_write_choice, _matches_choice),
    "combobox": (_write_combobox, combobox_matches),
    "number": (_write_text, _matches_number),
    "range": (_write_range, _matches_number),
    **dict.fromkeys(
        (
            "text",
            "email",
            "tel",
            "url",
            "date",
            "datetime-local",
            "month",
            "week",
            "time",
            "textarea",
            "search",
        ),
        (_write_text, _matches_text),
    ),
}


def _control_handler(action: Action):
    handler = _CONTROL_HANDLERS.get(action.field.kind)
    if handler is None:
        raise ApplicationError(
            Code.UNSUPPORTED_CONTROL, f"Unsupported {action.field.kind}: {action.field.label}"
        )
    return handler


async def _matches_control(page, target, action, value):
    _, matches = _control_handler(action)
    return await matches(page, target, action, value) and await native_value_is_valid(target)


async def fill(page: Page, action: Action):
    field = action.field
    target = locator(page, field)
    try:
        write, _ = _control_handler(action)
        if field.kind == "combobox" and action.random_choice:
            value = ""  # The concrete canonical answer is assigned after selecting an option.
        else:
            action.value = normalize_field_value(field, action.value)
            value = render_field_value(field, action.value)
        await write(page, target, action, value)
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
