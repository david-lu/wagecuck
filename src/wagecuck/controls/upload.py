"""Upload documents before filling; observe bounded upload/autofill completion."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

from wagecuck.models import Action, ApplicationError, Code

from .base import Target

UPLOAD_TIMEOUT_SECONDS = 15.0
UPLOAD_QUIET_SECONDS = 1.0
UPLOAD_MIN_WAIT_SECONDS = 1.5

# Stay inside one document widget. A filename elsewhere on the page is not evidence.
UPLOAD_STATE = r"""e => {
  const visible = n => !!n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden';
  let scope = e.parentElement;
  for (let depth = 0; depth < 5 && scope?.parentElement; depth++) {
    const parent = scope.parentElement;
    if (parent.matches('form, body, html') ||
        parent.querySelectorAll('input[type=file]').length !== 1 ||
        parent.querySelector('input:not([type=file]):not([type=hidden]), textarea, select')) break;
    scope = parent;
  }
  const nodes = [scope, ...scope.querySelectorAll('*')].filter(visible);
  const text = scope.innerText || '';
  const busy = nodes.some(n => {
    if (n.getAttribute('aria-busy') === 'true') return true;
    if (n.getAttribute('role') === 'progressbar') {
      const current = n.getAttribute('aria-valuenow');
      const maximum = n.getAttribute('aria-valuemax') || '100';
      return current === null || Number(current) < Number(maximum);
    }
    // Inspect status text, not instructional paragraphs or completed indicators.
    const status = n.childElementCount ? '' : n.textContent.trim();
    return !n.matches('button, a, label, [role=button]') &&
      /^(uploading|processing|parsing|extracting)\b/i.test(status) &&
      !/\b(complete|completed|finished|done)\b/i.test(status);
  });
  const error = nodes.some(n => n.matches('[role=alert], .field-error, .error-message, [data-error]') &&
    n.textContent.trim()) || /\b(upload failed|failed to upload|something went wrong)\b/i.test(text);
  const replace = nodes.some(n => n.matches('button, a, label, [role=button]') &&
    /\b(remove|delete|replace)\b/i.test(n.innerText || n.getAttribute('aria-label') || ''));
  const names = nodes.flatMap(n => [n.childElementCount ? '' : n.textContent.trim(),
    n.getAttribute('title') || '', n.getAttribute('download') || ''])
    .filter(t => /^[^\r\n]{1,250}\.(pdf|docx?|rtf|txt|odt)$/i.test(t));
  return {selected: [...(e.files || [])].map(f => f.name),
    attached: replace && !busy && !error ? [...new Set(names)] : [],
    busy, error, invalid: e.getAttribute('aria-invalid') === 'true',
    scopeId: scope.getAttribute('data-wagecuck-upload')};
}"""


async def upload_state(target: Target):
    return await target.evaluate(UPLOAD_STATE)


async def matches_file(page, target: Target, action: Action, value):
    if action.upload_error:
        return False
    state = await upload_state(target)
    return not (state["error"] or state["busy"] or state["invalid"]) and (
        state["selected"] == [Path(value).name]
        or (not state["selected"] and Path(value).name in state["attached"])
    )


async def _autofill_state(page):
    """Compare values in memory only, including already-filled values that change."""
    states = []
    for frame in page.frames:
        states.append(
            await frame.locator("input, textarea, select").evaluate_all(
                "els => els.map(e => [e.name, e.type, e.value, e.checked, e.disabled])"
            )
        )
    return states


async def write_file(page, target: Target, action: Action, value):
    # Mark a narrowly scoped upload container so a replacement input can be followed.
    await target.evaluate(r"""e => {
      let scope = e.parentElement;
      for (let depth = 0; depth < 5 && scope?.parentElement; depth++) {
        const p = scope.parentElement;
        if (p.matches('form, body, html') || p.querySelectorAll('input[type=file]').length !== 1 ||
            p.querySelector('input:not([type=file]):not([type=hidden]), textarea, select')) break;
        scope = p;
      }
      scope.setAttribute('data-wagecuck-upload', e.dataset.wagecuckId);
    }""")
    scope = page.frames[action.field.frame].locator(f'[data-wagecuck-upload="{action.field.id}"]')
    pending = set()
    failed_request = False
    loop = asyncio.get_running_loop()
    last_activity = loop.time()

    def started(request):
        nonlocal last_activity
        if request.resource_type in ("fetch", "xhr"):
            pending.add(request)
            last_activity = loop.time()

    def finished(request):
        nonlocal last_activity, failed_request
        if request in pending:
            pending.discard(request)
            content_type = request.headers.get("content-type", "").lower()
            document_request = (
                bool(
                    re.search(
                        r"upload|attachment|resume|document|parse|autofill",
                        urlsplit(request.url).path,
                        re.IGNORECASE,
                    )
                )
                or "multipart/form-data" in content_type
                or content_type
                in ("application/pdf", "application/octet-stream", "application/msword")
                or "officedocument" in content_type
            )
            failed_request |= bool(request.failure) and document_request
            last_activity = loop.time()

    page.on("request", started)
    page.on("requestfinished", finished)
    page.on("requestfailed", finished)
    action.upload_attempted = True
    try:
        await target.set_input_files(value)
        start = loop.time()
        previous = None
        while loop.time() - start < UPLOAD_TIMEOUT_SECONDS:
            # Some ATSs clear/replace the file input once the attachment is stored.
            replacements = scope.locator("input[type=file]")
            if await replacements.count() == 1:
                await replacements.evaluate("(e, id) => e.dataset.wagecuckId = id", action.field.id)
            if not await target.count():
                break  # The caller will parse the new form and rebind the document.
            state = await upload_state(target)
            current = (state, await _autofill_state(page))
            if current != previous:
                last_activity = loop.time()
                previous = current
            offline = not await page.evaluate("navigator.onLine")
            if state["error"] or (offline and failed_request):
                action.upload_error = Code.UPLOAD_UNVERIFIED if offline else Code.FIELD_FILL_FAILED
                raise ApplicationError(
                    action.upload_error,
                    "Document upload could not complete with networking disabled."
                    if offline
                    else "The page reported a document upload failure.",
                )
            if (
                not pending
                and not state["busy"]
                and loop.time() - start >= UPLOAD_MIN_WAIT_SECONDS
                and loop.time() - last_activity >= UPLOAD_QUIET_SECONDS
            ):
                return
            await asyncio.sleep(0.1)
        else:
            action.upload_error = Code.UPLOAD_TIMEOUT
            raise ApplicationError(
                Code.UPLOAD_TIMEOUT, "Document processing did not settle in time."
            )
    finally:
        page.remove_listener("request", started)
        page.remove_listener("requestfinished", finished)
        page.remove_listener("requestfailed", finished)
