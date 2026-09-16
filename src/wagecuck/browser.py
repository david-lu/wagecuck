from __future__ import annotations

import asyncio
import random
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .ats import annotate, detect
from .field_values import FieldValueError, normalize_field_value
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


async def combobox_matches(page, target, action, expected):
    actual = await combobox_value(target)
    if action.choice_labels and normalize(actual) in {
        normalize(label) for label in action.choice_labels
    }:
        return True
    if selected_value(expected, action) == selected_value(actual, action):
        return True
    if action.source != "facts:country":
        return False
    # Telephone country selectors may render only a flag and +1 after selecting
    # Canada. Verify the selected option itself; +1 alone is ambiguous.
    await target.click()
    try:
        selected = (
            page.frames[action.field.frame]
            .get_by_role("option", name=option_name(expected, action), selected=True, exact=True)
            .filter(visible=True)
        )
        await selected.first.wait_for(state="visible")
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


async def fill(page: Page, action: Action):
    field, value = action.field, action.value
    target = locator(page, field)
    try:
        if not (field.kind == "combobox" and action.random_choice):
            value = normalize_field_value(field, value)
            action.value = value
        string = "Yes" if value is True else "No" if value is False else value
        if field.kind == "file":
            await target.set_input_files(string)
            if await target.evaluate("e => e.files.length") < 1 or not await native_value_is_valid(
                target
            ):
                raise ValueError("Upload not retained")
        elif field.kind == "select":
            option = match_option(value, field.options)
            if option is None:
                raise ApplicationError(
                    Code.UNSUPPORTED_CONTROL, f"No unambiguous option for {field.label}"
                )
            await target.select_option(value=option.value)
            if await target.input_value() != option.value or not await native_value_is_valid(
                target
            ):
                raise ValueError("Selection not retained")
        elif field.kind in ("checkbox", "radio"):
            if not isinstance(value, bool):
                raise ApplicationError(
                    Code.UNSUPPORTED_CONTROL, f"Boolean answer required for {field.label}"
                )
            if field.kind != "radio" or value:
                await set_choice(target, value)
            if not await native_value_is_valid(target):
                raise ValueError("Choice violates native constraints")
        elif field.kind == "combobox":
            if action.random_choice:
                frame = page.frames[field.frame]
                await target.click()
                if await target.evaluate("e => e.tagName === 'INPUT' && !e.readOnly"):
                    await target.fill("")
                owned = await target.evaluate(
                    "e => (e.getAttribute('aria-controls') || e.getAttribute('aria-owns') || '').split(/\\s+/).filter(Boolean).map(id => '#' + CSS.escape(id)).join(',')"
                )
                scope = frame.locator(owned) if owned else frame
                options = scope.get_by_role("option", disabled=False).filter(visible=True)
                await options.first.wait_for(state="visible")
                candidates = [
                    (i, label.strip())
                    for i, label in enumerate(await options.all_text_contents())
                    if label.strip()
                    and not re.fullmatch(
                        r"(?:please )?(?:select|choose)(?: an? option)?", normalize(label)
                    )
                ]
                if not candidates:
                    raise ApplicationError(
                        Code.UNSUPPORTED_CONTROL, f"No source choices for {field.label}"
                    )
                index, label = random.choice(candidates)
                await options.nth(index).click()
                action.value = label
                action.choice_labels = [label]
                if not await combobox_matches(page, target, action, label):
                    raise ValueError("Source selection not retained")
                return
            await target.click()
            if await target.evaluate("e => e.tagName === 'INPUT' && !e.readOnly"):
                await target.fill(string)
            frame = page.frames[field.frame]
            options = frame.get_by_role(
                "option", name=option_name(string, action), exact=True
            ).filter(visible=True)
            await options.first.wait_for(state="visible")
            if await options.count() != 1:
                raise ApplicationError(
                    Code.UNSUPPORTED_CONTROL, f"Ambiguous choices for {field.label}"
                )
            await options.click()
            # Input-like widgets must retain a selected label; arbitrary widgets are unsupported.
            if not await combobox_matches(page, target, action, string):
                raise ValueError("Combobox selection not retained")
        elif field.kind in (
            "text",
            "email",
            "tel",
            "url",
            "number",
            "range",
            "date",
            "datetime-local",
            "month",
            "week",
            "time",
            "textarea",
            "search",
        ):
            await target.fill(string)
            await target.blur()
            actual = await target.input_value()
            valid = actual == string or (
                field.kind == "tel" and await phone_matches(target, string)
            )
            if not valid and field.kind == "tel":
                # Some telephone masks mishandle one bulk input event. Retry this
                # field once using normal keystrokes, then verify the whole number.
                await target.press("ControlOrMeta+A")
                await target.press("Backspace")
                await target.press("ControlOrMeta+A")
                await target.press_sequentially(string, delay=20)
                await target.blur()
                valid = await phone_matches(target, string)
            if not valid or not await native_value_is_valid(target):
                raise ValueError("Value not retained")
        else:
            raise ApplicationError(
                Code.UNSUPPORTED_CONTROL, f"Unsupported {field.kind}: {field.label}"
            )
    except ApplicationError:
        raise
    except (FieldValueError, PlaywrightError, ValueError) as exc:
        # Do not serialize Playwright exception bodies: they can contain applicant values.
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


async def verify_actions(page: Page, actions: list[Action]):
    """Verify the complete plan after all change handlers have run."""
    for action in actions:
        field, value = action.field, action.value
        target = locator(page, field)
        if not await target.count():
            continue  # Conditional rerenders are replanned from a fresh snapshot.
        string = "Yes" if value is True else "No" if value is False else value
        if field.kind == "file":
            valid = await target.evaluate("e => e.files.length > 0")
        elif field.kind in ("checkbox", "radio"):
            valid = await target.is_checked() == value
        elif field.kind == "select":
            option = match_option(value, field.options)
            valid = option is not None and await target.input_value() == option.value
        elif field.kind == "combobox":
            valid = await combobox_matches(page, target, action, string)
        else:
            actual = await target.input_value()
            valid = actual == string or (
                field.kind == "tel" and await phone_matches(target, string)
            )
        if field.kind != "combobox":
            valid = valid and await native_value_is_valid(target)
        if not valid:
            raise ApplicationError(
                Code.VALIDATION_FAILED,
                f"A later form update changed the value of: {field.label}",
                [field.label],
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
