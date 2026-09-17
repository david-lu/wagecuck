"""Exercise the real field executor on loaded forms with all networking disabled.

This is a DOM compatibility probe, not an application or an upload acceptance test.
No next/submit controls are clicked. Network-dependent widgets may fail offline.
"""

import argparse
import asyncio
import copy
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from wagecuck.agent import WorkflowAgent
from wagecuck.agent_config import add_agent_arguments, agent_metadata, create_agent
from wagecuck.browser import (
    click_and_settle,
    dismiss_optional_cookies,
    entry_controls,
    prepared_snapshot,
    snapshot,
)
from wagecuck.captcha import detect_challenge
from wagecuck.evaluation import (
    BrowserPool,
    add_concurrency_argument,
    default_report_path,
    run_cases,
    write_report,
)
from wagecuck.execution import execute_actions, field_id, form_changed, form_signature
from wagecuck.models import ApplicationError, Profile
from wagecuck.reporting import execution_report


async def probe_fields(page, profile, agent=None, *, agent_fill=False):
    # Register the block before any profile values enter page JavaScript. The
    # caller also blocks service workers and WebSockets from context creation.
    await page.context.route("**/*", lambda route: route.abort())
    await page.context.set_offline(True)
    snap = await prepared_snapshot(page)
    challenge = await detect_challenge(page)
    planner = WorkflowAgent(agent)
    previous_actions, analysis, warnings = {}, {}, []
    seen = {}
    for _ in range(4):
        signature = form_signature(snap)
        seen[signature] = seen.get(signature, 0) + 1
        actions, unresolved = await planner.plan(snap.fields, profile, agent_fill=agent_fill)
        warnings.extend(planner.warnings)
        execution = await execute_actions(
            page,
            actions,
            previous_actions=list(previous_actions.values()),
            assessed_fields=snap.fields,
        )
        analysis.update(
            {row["field_id"]: row for row in planner.describe(snap.fields, actions, unresolved)}
        )
        previous_actions = {
            field_id(outcome.action.field): outcome.action for outcome in execution.fields
        }
        after = execution.snapshot
        if (not execution.needs_replan and not form_changed(snap, after)) or seen[signature] >= 2:
            break
        snap = after
    report = execution_report(execution, list(analysis.values()))
    if not report["field_count"]:
        report["required_fill_pass"] = False
    report.update(captcha=challenge.kind if challenge else None, agent_warnings=warnings)
    return report


def probe_code(report):
    """Classify the recorded verification result without promoting a failed pass."""
    if not report["field_count"]:
        return "FORM_NOT_RELOADED"
    upload_codes = {
        row["code"] for row in report.get("control_outcomes", []) if row.get("kind") == "file"
    }
    upload_codes.update(report.get("upload_error_codes", []))
    for code in ("UPLOAD_TIMEOUT", "UPLOAD_UNVERIFIED"):
        if code in upload_codes:
            return code
    if report["filled_count"] < report["mapped_count"] or not report["completed_values_retained"]:
        return "FIELD_FILL_FAILED"
    if report["required_answers_missing"]:
        return "REQUIRED_ANSWER_MISSING"
    if report["page_validation_error_count"] or not report["required_fill_pass"]:
        return "VALIDATION_FAILED"
    if not report["mapped_count"]:
        return "NO_MAPPED_FIELDS"
    return "MAPPED_FIELDS_VERIFIED"


async def reload_form(page, case):
    await page.goto(case["final_url"], wait_until="domcontentloaded")
    previous_shape, stable = None, 0
    for attempt in range(40):
        snap = await snapshot(page)
        if await dismiss_optional_cookies(page, snap):
            snap = await snapshot(page)
        shape = tuple((field.name, field.label, field.kind) for field in snap.fields)
        stable = stable + 1 if shape == previous_shape else 0
        previous_shape = shape
        if attempt >= 12 and stable >= 2 and len(snap.fields) >= case["field_count"]:
            break
        if not snap.fields and attempt in (8, 16) and entry_controls(snap):
            page = await click_and_settle(page, entry_controls(snap)[0])
        await asyncio.sleep(0.25)
    return page


def exception_code(exc):
    return "TIMEOUT" if isinstance(exc, (TimeoutError, PlaywrightTimeout)) else "BROWSER_ERROR"


async def probe_case(case, browser, profile, args):
    """Own all mutable browser, profile, and model state for exactly one URL."""
    row = {
        "id": case["id"],
        "url": case["final_url"],
        "ats": case["ats"],
        "agent_calls_attempted": 0,
    }
    context, agent = None, None
    try:
        agent = create_agent(args)
        profile = profile.model_copy(deep=True)
        if case["code"] != "INSPECTED_NOT_SUBMITTED":
            row.update(
                code=case["code"],
                stage="live_navigation",
                filled_count=0,
                message=case.get("message", "Could not reach an application form."),
            )
        else:
            row["stage"] = "form_reload"
            async with asyncio.timeout(75 + (3 * args.agent_timeout if agent else 0)):
                context = await browser.new_context(service_workers="block")
                await context.route_web_socket("**/*", lambda ws: ws.close())
                context.set_default_timeout(2500)
                context.set_default_navigation_timeout(30000)
                page = await context.new_page()
                page = await reload_form(page, case)
                row["stage"] = "offline_fill"
                row.update(await probe_fields(page, profile, agent, agent_fill=args.agent_fill))
                row["code"] = probe_code(row)
    except ApplicationError as exc:
        row.update(code=exc.code, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - one diagnostic case cannot abort its peers
        row["code"] = exception_code(exc)
    finally:
        row["agent_calls_attempted"] = agent.calls if agent else 0
        if context is not None:
            try:
                async with asyncio.timeout(5):
                    await context.close()
            except Exception:  # noqa: BLE001 - continue independent cases after cleanup failure
                row["code"] = "BROWSER_ERROR"
    return row, agent_metadata(agent, agent_fill=args.agent_fill)


def aggregate_agent_metadata(template, metadata):
    """Merge per-case counters in input order without sharing mutable agents."""
    result = copy.deepcopy(template)
    result["calls_attempted"] = sum(case["calls_attempted"] for case in metadata)
    if "metrics" not in template and not any("metrics" in case for case in metadata):
        return result
    metrics = [case["metrics"] for case in metadata if "metrics" in case]
    merged = {}
    for key in ("operation_calls", "operation_attempts", "failures"):
        total = Counter()
        for metric in metrics:
            total.update(metric.get(key, {}))
        merged[key] = dict(total)
    for key in ("retries", "input_tokens", "output_tokens"):
        merged[key] = sum(metric.get(key, 0) for metric in metrics)
    merged["request_ids"] = list(
        dict.fromkeys(
            request_id for metric in metrics for request_id in metric.get("request_ids", [])
        )
    )[-20:]
    merged["last_error"] = next(
        (metric["last_error"] for metric in reversed(metrics) if metric.get("last_error")), None
    )
    result["metrics"] = merged
    return result


async def probe_cases(cases, browser_type, profile, args, metadata_template, *, pool=None):
    completed = {}

    def persist():
        ordered = [completed[index] for index in sorted(completed)]
        write_report(
            args.output,
            {
                "checked_at": datetime.now(UTC).isoformat(),
                "dataset_split": args.dataset_split,
                "aggregate_only": args.aggregate_only,
                "implementation_fingerprint": args.implementation_fingerprint,
                "mode": "offline_dom_probe",
                "network_disabled_before_filling": True,
                "applications_submitted": 0,
                "concurrency": args.concurrency,
                "pool_enabled": pool is not None,
                "agent": aggregate_agent_metadata(metadata_template, [item[1] for item in ordered]),
                "limitations": "Only the loaded step; no server validation, accepted upload, dependent remote options, next-step navigation, CAPTCHA solving or submission verified.",
                "results": [item[0] for item in ordered],
            },
        )

    def record(index, outcome):
        row, metadata = outcome
        hidden = {
            "fields",
            "control_outcomes",
            "unmapped_fields",
            "unmapped_controls",
            "field_analysis",
            "control_analysis",
            "required_fill_failures",
            "required_answers_missing",
            "agent_warnings",
        }
        stored_row = (
            {key: value for key, value in row.items() if key not in hidden}
            if args.aggregate_only
            else row
        )
        completed[index] = stored_row, metadata
        printable_row = (
            stored_row
            if args.aggregate_only
            else {
                k: v
                for k, v in row.items()
                if k
                not in (
                    "fields",
                    "control_outcomes",
                    "unmapped_fields",
                    "unmapped_controls",
                    "field_analysis",
                    "control_analysis",
                    "required_fill_failures",
                )
            }
        )
        print(json.dumps(printable_row), flush=True)
        persist()

    async def worker(case):
        if case["code"] != "INSPECTED_NOT_SUBMITTED":
            return await probe_case(case, None, profile, args)
        browser, outcome = None, None
        try:
            if pool is not None:
                async with pool.lease() as leased_browser:
                    return await probe_case(case, leased_browser, profile, args)
            async with asyncio.timeout(30):
                browser = await browser_type.launch()
            outcome = await probe_case(case, browser, profile, args)
        except Exception as exc:  # noqa: BLE001 - browser startup failure affects one case
            outcome = (
                {
                    "id": case["id"],
                    "url": case["final_url"],
                    "ats": case["ats"],
                    "stage": "form_reload",
                    "code": exception_code(exc),
                    "agent_calls_attempted": 0,
                },
                copy.deepcopy(metadata_template),
            )
        finally:
            if browser is not None:
                try:
                    async with asyncio.timeout(5):
                        await browser.close()
                except Exception:  # noqa: BLE001 - retain the completed case and counters
                    if outcome is not None:
                        outcome[0]["code"] = "BROWSER_ERROR"
        return outcome

    persist()
    await run_cases(cases, worker, concurrency=args.concurrency, on_result=record)
    return [completed[index][0] for index in sorted(completed)]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--profile", type=Path, default=Path("profiles/demo/profile.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dataset-split", choices=("training", "validation"), default="training")
    parser.add_argument("--implementation-fingerprint", default="")
    parser.add_argument("--aggregate-only", action="store_true")
    add_concurrency_argument(parser)
    add_agent_arguments(parser)
    args = parser.parse_args()
    if args.output is None:
        args.output = default_report_path("offline-fill-report")
    try:
        metadata_template = agent_metadata(create_agent(args), agent_fill=args.agent_fill)
    except ValueError as exc:
        parser.error(str(exc))
    profile = Profile.load(args.profile)
    if not profile.synthetic:
        parser.error("Use a synthetic profile for this diagnostic.")
    cases = [
        case
        for report in args.reports
        for case in json.loads(report.read_text(encoding="utf-8"))["results"]
    ]
    async with async_playwright() as playwright:
        if args.pool:
            async with BrowserPool(playwright.chromium, args.concurrency) as pool:
                await probe_cases(
                    cases, playwright.chromium, profile, args, metadata_template, pool=pool
                )
        else:
            await probe_cases(cases, playwright.chromium, profile, args, metadata_template)
    print(f"Report: {args.output.resolve()}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
