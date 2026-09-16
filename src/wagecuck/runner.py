from __future__ import annotations

import asyncio
import json
import re
import sys
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.async_api import Browser, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from .agent import MappingAgent, WorkflowAgent
from .browser import (
    blocker,
    click_and_settle,
    confirmation,
    detect_ats,
    dismiss_optional_cookies,
    entry_controls,
    locator,
    snapshot,
)
from .captcha import CapSolver, deliver_token, detect_challenge
from .execution import execute_actions, field_id, form_changed, form_signature
from .models import ApplicationError, ApplicationResult, Code, Profile, RunOptions
from .reporting import execution_report
from .store import Store, application_key


def now():
    return datetime.now(UTC).isoformat()


class ApplicationRunner:
    """Reusable service; each invocation owns its context and immutable result identity."""

    def __init__(self, agent: MappingAgent | None = None, captcha: CapSolver | None = None):
        self.agent = WorkflowAgent(agent)
        self.captcha = captcha or CapSolver()

    async def run(
        self,
        url: str,
        profile: Profile,
        options: RunOptions | None = None,
        *,
        browser: Browser | None = None,
    ) -> ApplicationResult:
        """Run once in a fresh context; an optional borrowed browser stays open.

        The caller controls a borrowed browser's launch settings. This invocation
        always owns its context, including storage state, timeouts, and tracing.
        """
        options = options or RunOptions()
        result = ApplicationResult(
            run_id=uuid4().hex,
            profile_id=profile.id,
            job_url=url,
            mode=options.mode,
            started_at=now(),
        )
        directory = options.artifacts_dir / result.run_id
        directory.mkdir(parents=True, exist_ok=True)
        result.artifact_dir = str(directory.resolve())
        store, key, claimed = None, "", False

        def event(state, **data):
            with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"at": now(), "state": state, **data}) + "\n")

        try:
            if options.agent_fill and self.agent.fallback is None:
                raise ApplicationError(
                    Code.INVALID_INPUT, "agent_fill requires a configured agent."
                )
            parts = urlsplit(url)
            if (
                parts.scheme not in ("http", "https")
                or not parts.hostname
                or parts.username
                or parts.password
            ):
                raise ApplicationError(
                    Code.INVALID_INPUT, "A public HTTP(S) application URL is required."
                )
            if (
                (options.mode == "submit" or options.wait_for_user)
                and profile.synthetic
                and parts.hostname not in ("localhost", "127.0.0.1", "::1")
            ):
                raise ApplicationError(
                    Code.SYNTHETIC_PROFILE_BLOCKED,
                    "Synthetic profiles can submit only to local fixtures.",
                )
            if options.mode != "inspect":
                for document in (profile.resume, profile.cover_letter):
                    if document and (not document.is_file() or document.stat().st_size == 0):
                        raise ApplicationError(
                            Code.DOCUMENT_MISSING, "A configured document is missing or empty."
                        )
            if options.mode == "submit" or options.wait_for_user:
                store = Store(options.database)
                key = application_key(url, profile.id)
                store.claim(key, result.run_id)
                claimed = True
            event("starting", mode=options.mode)
            async with asyncio.timeout(options.timeout_seconds) as workflow_budget:
                async with AsyncExitStack() as resources:
                    if browser is None:
                        playwright = await resources.enter_async_context(async_playwright())
                        browser = await playwright.chromium.launch(
                            headless=options.headless, slow_mo=options.slow_mo_ms
                        )
                        resources.push_async_callback(browser.close)
                    context = await browser.new_context(
                        storage_state=str(options.storage_state) if options.storage_state else None
                    )
                    resources.push_async_callback(context.close)
                    context.set_default_timeout(options.action_timeout_ms)
                    context.set_default_navigation_timeout(options.navigation_timeout_ms)
                    if options.capture_sensitive_artifacts:
                        await context.tracing.start(screenshots=True, snapshots=True)
                    page = await context.new_page()
                    response = None
                    for attempt in range(2):
                        try:
                            response = await page.goto(url, wait_until="domcontentloaded")
                            break
                        except PlaywrightError as exc:
                            reason = re.search(r"net::ERR_[A-Z_]+", str(exc))
                            event(
                                "navigation_failed",
                                attempt=attempt + 1,
                                reason=reason.group(0) if reason else type(exc).__name__,
                            )
                            if attempt == 1:
                                raise ApplicationError(
                                    Code.NAVIGATION_FAILED,
                                    "Could not load the application URL after two attempts.",
                                ) from exc
                    if response and response.status >= 400:
                        result.final_url, result.ats = page.url, detect_ats(page.url)
                        code = (
                            Code.JOB_CLOSED
                            if response.status in (404, 410)
                            else Code.ACCESS_DENIED
                            if response.status in (401, 403, 429)
                            else Code.NAVIGATION_FAILED
                        )
                        raise ApplicationError(code, f"Job page returned HTTP {response.status}.")
                    try:
                        await self._workflow(
                            page,
                            profile,
                            options,
                            result,
                            directory,
                            event,
                            store,
                            key,
                            workflow_budget,
                        )
                    finally:
                        if options.capture_sensitive_artifacts:
                            try:
                                await page.screenshot(
                                    path=str(directory / "final.png"), full_page=True
                                )
                                await context.tracing.stop(path=str(directory / "trace.zip"))
                            except PlaywrightError:
                                event("artifact_capture_failed")
        except ApplicationError as exc:
            result.code, result.message, result.unresolved = exc.code, str(exc), exc.unresolved
        except (TimeoutError, PlaywrightTimeout):
            result.code, result.message = Code.TIMEOUT, "The bounded workflow timed out."
        except PlaywrightError:
            result.code, result.message = (
                Code.BROWSER_ERROR,
                "Browser failed; check browser installation and session state.",
            )
        except Exception as exc:  # noqa: BLE001 - public runner always returns a typed failure
            result.code, result.message = (
                Code.INTERNAL_ERROR,
                f"Unexpected {type(exc).__name__}; see local diagnostics.",
            )
        finally:
            if result.success:
                # Browser/trace cleanup failure must not overwrite confirmed receipt.
                result.code, result.status = Code.OK, "succeeded"
                result.message = "The application page confirmed receipt."
            elif result.status in ("ready", "inspected"):
                result.code = Code.READY if result.status == "ready" else Code.INSPECTED
            # Cancellation/crashes after the submission fence leave the durable state uncertain.
            if result.submission_attempted and not result.success:
                result.status, result.code = "unknown", Code.SUBMISSION_UNCONFIRMED
                result.message = "Submission was attempted but acceptance could not be confirmed. Do not retry without checking the employer portal."
            if result.user_handoff and not result.success:
                result.status, result.code = "unknown", Code.USER_SUBMISSION_UNCONFIRMED
                result.message = "Manual review ended without an observed application receipt. Check the employer portal before retrying."
            result.retryable = not result.submission_attempted and result.code in (
                Code.NAVIGATION_FAILED,
                Code.TIMEOUT,
                Code.BROWSER_ERROR,
                Code.AGENT_FAILED,
            )
            if store and claimed:
                store.transition(
                    key,
                    result.run_id,
                    "succeeded"
                    if result.success
                    else "uncertain"
                    if result.submission_attempted or result.user_handoff
                    else "failed",
                )
            result.finished_at = now()
            event("finished", code=result.code, status=result.status)
            (directory / "result.json").write_text(
                result.model_dump_json(indent=2), encoding="utf-8"
            )
        return result

    async def _workflow(
        self, page, profile, options, result, directory, event, store, key, workflow_budget
    ):
        seen = {}
        entry_attempts = {}
        previous_actions = {}
        identity_sources = set()
        captcha_attempts = 0
        for step in range(options.max_steps):
            result.steps = step + 1
            snap = await snapshot(page)
            # Initial hydration can expose a shell before the form or Apply control appears.
            if not snap.fields and (
                step > 0
                or not any(
                    re.search(r"apply|application", c.label, re.IGNORECASE) for c in snap.controls
                )
            ):
                for _ in range(12):
                    if blocker(snap):
                        break
                    await asyncio.sleep(0.25)
                    snap = await snapshot(page)
                    if snap.fields or (
                        step == 0
                        and any(
                            re.search(r"apply|application", c.label, re.IGNORECASE)
                            for c in snap.controls
                        )
                    ):
                        break
            result.final_url, result.ats = page.url, detect_ats(page.url)
            if await dismiss_optional_cookies(page, snap):
                snap = await snapshot(page)
            event("parsed", step=step + 1, ats=result.ats, fields=len(snap.fields))
            # No entered values or full page text in default artifacts.
            if options.mode == "inspect" and snap.fields:
                inspected_actions, inspected_unresolved = await self.agent.plan(
                    snap.fields, profile
                )
                (directory / f"analysis-{step + 1:02d}.json").write_text(
                    json.dumps(
                        self.agent.describe(snap.fields, inspected_actions, inspected_unresolved),
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            public = snap.model_dump(
                exclude={"text": True, "errors": True, "fields": {"__all__": {"context"}}}
            )
            (directory / f"step-{step + 1:02d}.json").write_text(
                json.dumps(public, indent=2), encoding="utf-8"
            )
            challenge = await detect_challenge(page)
            code = blocker(snap)
            if code == Code.CAPTCHA_REQUIRED and challenge and snap.fields:
                code = None  # Solve immediately before submission so the token stays fresh.
            if (
                code == Code.CAPTCHA_REQUIRED
                and challenge
                and not snap.fields
                and options.mode == "submit"
            ):
                if captcha_attempts >= 2:
                    raise ApplicationError(
                        Code.CAPTCHA_SOLVE_FAILED, "CAPTCHA persisted after two solves."
                    )
                token = await self.captcha.solve(challenge)
                await deliver_token(page, challenge, token)
                captcha_attempts += 1
                event("captcha_token_delivered", provider="capsolver")
                continue
            if code:
                raise ApplicationError(code, f"Application stopped: {code.value}.")
            if options.mode == "inspect" and snap.fields:
                result.status, result.code = "inspected", Code.INSPECTED
                result.message = f"Detected {len(snap.fields)} fields; no applicant data entered."
                return
            if not snap.fields and not identity_sources:
                entry = entry_controls(snap)
                if not entry:
                    raise ApplicationError(
                        Code.FORM_NOT_FOUND,
                        "No application form or recognized entry control found.",
                    )
                # Duplicated top/bottom Apply links are common; choose the first.
                signature = (page.url, tuple((c.label, c.action) for c in entry))
                # SSR buttons can be visible before hydration attaches handlers. Retry
                # only entry navigation, with no fields/identity and never final submit.
                if entry_attempts.get(signature, 0) >= 2:
                    raise ApplicationError(
                        Code.NO_PROGRESS, "Apply navigation did not reveal a form."
                    )
                entry_attempts[signature] = entry_attempts.get(signature, 0) + 1
                page = await click_and_settle(page, entry[0])
                continue
            signature = form_signature(snap)
            seen[signature] = seen.get(signature, 0) + 1
            if seen[signature] > 2:
                raise ApplicationError(
                    Code.NO_PROGRESS, "The form repeated the same state after bounded replanning."
                )
            actions, unresolved = await self.agent.plan(
                snap.fields, profile, agent_fill=options.agent_fill
            )
            for warning in self.agent.warnings:
                event("agent_warning", **warning)
            for field in snap.fields:
                if field.required_evidence.startswith("agent:"):
                    event("agent_required_field", field=field.id, evidence=field.required_evidence)
            (directory / f"step-{step + 1:02d}.json").write_text(
                snap.model_dump_json(
                    indent=2,
                    exclude={"text": True, "errors": True, "fields": {"__all__": {"context"}}},
                ),
                encoding="utf-8",
            )
            execution = await execute_actions(
                page,
                actions,
                previous_actions=list(previous_actions.values()),
                assessed_fields=snap.fields,
            )
            previous_actions = {
                field_id(outcome.action.field): outcome.action for outcome in execution.fields
            }
            (directory / f"analysis-{step + 1:02d}.json").write_text(
                json.dumps(self.agent.describe(snap.fields, actions, unresolved), indent=2),
                encoding="utf-8",
            )
            current_outcomes = {field_id(item.action.field): item for item in execution.fields}
            for action in actions:
                outcome = current_outcomes[field_id(action.field)]
                record = {
                    "step": step + 1,
                    "field_id": f"{action.field.frame}:{action.field.id}",
                    "label": action.field.label,
                    "source": action.source,
                    "answer_basis": action.answer_basis,
                    "made_up": action.made_up,
                    "source_keys": action.source_keys,
                    "inference_reason": action.inference_reason,
                    "status": "filled" if outcome.verified else "failed",
                    "code": outcome.code,
                }
                result.answer_log.append(record)
                if not outcome.verified:
                    continue
                identity_sources.add(action.source)
                # Validated semantic mappings count toward the same identity check
                # as deterministic aliases; drafting never contributes identity.
                if action.source.startswith("agent:"):
                    mapped_keys = action.source.removeprefix("agent:").split("+")
                    identity_sources.update(f"facts:{key}" for key in mapped_keys)
                    if "documents.resume" in mapped_keys:
                        identity_sources.add("document:resume")
                result.fields_filled += 1
                event(
                    "filled",
                    field=action.field.id,
                    source=action.source,
                    answer_basis=action.answer_basis,
                    made_up=action.made_up,
                    source_keys=action.source_keys,
                    inference_reason=action.inference_reason,
                )
            after = execution.snapshot
            code = blocker(after)
            if code and not (code == Code.CAPTCHA_REQUIRED and challenge):
                raise ApplicationError(code, f"Application stopped: {code.value}.")
            # Replaced nodes and changed options/requirements need a fresh plan;
            # normal successful filling does not count as a structural change.
            if form_changed(snap, after):
                continue
            report = execution_report(
                execution, self.agent.describe(snap.fields, actions, unresolved)
            )
            (directory / f"execution-{step + 1:02d}.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            active_ids = {field_id(field) for field in after.fields}
            failed = [
                outcome
                for outcome in execution.fields
                if field_id(outcome.action.field) in active_ids and not outcome.verified
            ]
            if failed:
                raise ApplicationError(
                    Code(failed[0].code),
                    failed[0].message,
                    [outcome.action.field.label for outcome in failed],
                )
            missing = report["required_answers_missing"]
            if missing:
                raise ApplicationError(
                    Code.REQUIRED_ANSWER_MISSING,
                    "Required questions have no explicit profile answer.",
                    missing,
                )
            invalid = [f.label for f in after.fields if f.invalid]
            if invalid or after.errors:
                raise ApplicationError(
                    Code.VALIDATION_FAILED, "The page reported form validation errors.", invalid
                )
            if not report["required_fill_pass"]:
                raise ApplicationError(
                    Code.VALIDATION_FAILED,
                    "Required values were not retained.",
                    report["required_fill_failures"],
                )
            next_controls = [
                c
                for c in after.controls
                if c.action == "next"
                or re.fullmatch(
                    r"next(?: step)?|continue|save and continue|review(?: application)?",
                    c.label,
                    re.IGNORECASE,
                )
            ]
            submit_controls = [
                c
                for c in after.controls
                if c.action == "submit"
                or (
                    re.fullmatch(
                        r"submit(?: (?:your )?application)?|send(?: application)?|apply|apply now",
                        c.label,
                        re.IGNORECASE,
                    )
                    and c.kind != "a"
                )
            ]
            if next_controls and not submit_controls:
                if len(next_controls) != 1:
                    raise ApplicationError(
                        Code.UNSUPPORTED_CONTROL, "Multiple possible next-step controls."
                    )
                page = await click_and_settle(page, next_controls[0])
                continue
            if not submit_controls:
                raise ApplicationError(
                    Code.SUBMIT_NOT_FOUND, "No recognized final application submit control."
                )
            if len(submit_controls) != 1:
                raise ApplicationError(
                    Code.UNSUPPORTED_CONTROL, "Multiple possible final submission controls."
                )
            if "facts:email" not in identity_sources or not identity_sources.intersection(
                {"facts:full_name", "facts:first_name", "document:resume"}
            ):
                raise ApplicationError(
                    Code.FORM_NOT_FOUND, "Cannot establish that this is an applicant identity form."
                )
            if options.wait_for_user:
                if profile.synthetic and urlsplit(page.url).hostname not in (
                    "localhost",
                    "127.0.0.1",
                    "::1",
                ):
                    raise ApplicationError(
                        Code.SYNTHETIC_PROFILE_BLOCKED,
                        "Synthetic profiles can hand off only on local fixtures.",
                    )
                # A person may submit by mouse, keyboard, or site-specific UI. Keep the durable
                # claim uncertain until we observe receipt, including after an interrupted run.
                store.transition(key, result.run_id, "awaiting_user")
                result.user_handoff = True
                workflow_budget.reschedule(None)
                await self._wait_for_user(page, result, event, confirmation(after))
                return
            if options.mode == "fill":
                if challenge:
                    raise ApplicationError(
                        Code.CAPTCHA_REQUIRED,
                        "Fields filled; CAPTCHA solving is reserved for submit mode.",
                    )
                result.status, result.code = "ready", Code.READY
                result.message = "Application filled and ready; final submit was not clicked."
                return
            if profile.synthetic and urlsplit(page.url).hostname not in (
                "localhost",
                "127.0.0.1",
                "::1",
            ):
                raise ApplicationError(
                    Code.SYNTHETIC_PROFILE_BLOCKED,
                    "Synthetic profile was redirected to a live application.",
                )
            baseline = confirmation(after)
            challenge = await detect_challenge(page)
            if challenge:
                self.captcha.validate(challenge)
                token = await self.captcha.solve(challenge)
            store.transition(key, result.run_id, "submitting")
            result.submission_attempted = True
            if challenge:
                await deliver_token(page, challenge, token)
                event("captcha_token_delivered", provider="capsolver")
                # Some named widget callbacks submit; never click again if they already did.
                current = await snapshot(page)
                evidence = confirmation(current)
                if evidence and evidence != baseline:
                    result.success, result.submitted = True, True
                    result.status, result.code = "succeeded", Code.OK
                    result.message = "The application page confirmed receipt."
                    result.evidence.append(evidence)
                    return
            event("submitting")
            await locator(page, submit_controls[0]).click()
            deadline = asyncio.get_running_loop().time() + options.confirmation_timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                try:
                    current = await snapshot(page)
                except PlaywrightError:
                    await asyncio.sleep(0.25)
                    continue  # Reobserve during redirects; never resubmit.
                result.final_url = current.url
                evidence = confirmation(current)
                if evidence and evidence != baseline:
                    result.success, result.submitted = True, True
                    result.status, result.code = "succeeded", Code.OK
                    result.message = "The application page confirmed receipt."
                    result.evidence.append(evidence)
                    return
                await asyncio.sleep(0.25)
            raise ApplicationError(
                Code.SUBMISSION_UNCONFIRMED, "No new application confirmation was observed."
            )
        raise ApplicationError(Code.STEP_LIMIT, "Maximum workflow steps reached.")

    async def _wait_for_user(self, page, result, event, baseline):
        event("waiting_for_user")
        print(
            "Application filled. Review it in the browser and click Submit when ready. "
            "Waiting for confirmation; close the browser to end the run.",
            file=sys.stderr,
            flush=True,
        )
        # No filling, clicking, or CAPTCHA callbacks after handoff. User edits are preserved.
        context = page.context
        while context.browser.is_connected() and context.pages:
            pages = [page] if not page.is_closed() else []
            pages += [candidate for candidate in context.pages if candidate != page]
            for candidate in pages:
                try:
                    current = await snapshot(candidate)
                except PlaywrightError:
                    continue  # Includes a navigation replacing the execution context.
                evidence = confirmation(current)
                if evidence and evidence != baseline:
                    result.final_url = current.url
                    result.success = result.submitted = result.submission_attempted = True
                    result.status, result.code = "succeeded", Code.OK
                    result.message = (
                        "The application page confirmed receipt after manual submission."
                    )
                    result.evidence.append(evidence)
                    event("manual_submission_confirmed")
                    return
            await asyncio.sleep(0.5)
