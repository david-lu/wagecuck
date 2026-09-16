"""Exercise the real field executor on loaded forms with all networking disabled.

This is a DOM compatibility probe, not an application or an upload acceptance test.
No next/submit controls are clicked. Network-dependent widgets may fail offline.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import async_playwright

from wagecuck.agent import WorkflowAgent
from wagecuck.agent_config import add_agent_arguments, agent_metadata, create_agent
from wagecuck.browser import (
    click_and_settle,
    dismiss_optional_cookies,
    entry_controls,
    snapshot,
)
from wagecuck.captcha import detect_challenge
from wagecuck.execution import execute_actions, field_id, form_changed, form_signature
from wagecuck.models import ApplicationError, Profile
from wagecuck.reporting import execution_report


async def probe_fields(page, profile, agent=None, *, agent_fill=False):
    # Register the block before any profile values enter page JavaScript. The
    # caller also blocks service workers and WebSockets from context creation.
    await page.context.route("**/*", lambda route: route.abort())
    await page.context.set_offline(True)
    snap = await snapshot(page)
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
            page, actions, previous_actions=list(previous_actions.values()),
            assessed_fields=snap.fields,
        )
        analysis.update({row["field_id"]: row for row in planner.describe(snap.fields, actions, unresolved)})
        previous_actions = {field_id(outcome.action.field): outcome.action for outcome in execution.fields}
        after = execution.snapshot
        if not form_changed(snap, after) or seen[signature] >= 2:
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
    if report["filled_count"] < report["mapped_count"] or not report["completed_values_retained"]:
        return "FIELD_FILL_FAILED"
    if report["required_answers_missing"]:
        return "REQUIRED_ANSWER_MISSING"
    if report["page_validation_error_count"] or not report["required_fill_pass"]:
        return "VALIDATION_FAILED"
    if not report["mapped_count"]:
        return "NO_MAPPED_FIELDS"
    return "MAPPED_FIELDS_VERIFIED"


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--profile", type=Path, default=Path("profiles/demo/profile.json"))
    parser.add_argument("--output", type=Path, default=Path("runs/reports/offline-fill-report.json"))
    parser.add_argument("--dataset-split", choices=("training", "validation"), default="training")
    parser.add_argument("--implementation-fingerprint", default="")
    parser.add_argument("--aggregate-only", action="store_true")
    add_agent_arguments(parser)
    args = parser.parse_args()
    try:
        agent = create_agent(args)
    except ValueError as exc:
        parser.error(str(exc))
    profile = Profile.load(args.profile)
    if not profile.synthetic:
        parser.error("Use a synthetic profile for this diagnostic.")
    rows = []

    def record(row):
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
        rows.append(stored_row)
        printable_row = stored_row if args.aggregate_only else {
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
        print(json.dumps(printable_row), flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "checked_at": datetime.now(UTC).isoformat(),
                    "dataset_split": args.dataset_split,
                    "aggregate_only": args.aggregate_only,
                    "implementation_fingerprint": args.implementation_fingerprint,
                    "mode": "offline_dom_probe",
                    "network_disabled_before_filling": True,
                    "applications_submitted": 0,
                    "agent": agent_metadata(agent, agent_fill=args.agent_fill),
                    "limitations": "Only the loaded step; no server validation, accepted upload, dependent remote options, next-step navigation, CAPTCHA solving or submission verified.",
                    "results": rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        for report in args.reports:
            for case in json.loads(report.read_text())["results"]:
                row = {"id": case["id"], "url": case["final_url"], "ats": case["ats"]}
                calls_before = agent.calls if agent else 0
                row["agent_calls_attempted"] = 0
                if case["code"] != "INSPECTED_NOT_SUBMITTED":
                    row.update(
                        {
                            "code": case["code"],
                            "stage": "live_navigation",
                            "message": case.get("message", "Could not reach an application form."),
                            "filled_count": 0,
                        }
                    )
                    record(row)
                    continue
                context = await browser.new_context(service_workers="block")
                await context.route_web_socket("**/*", lambda ws: ws.close())
                context.set_default_timeout(2500)
                context.set_default_navigation_timeout(30000)
                row["stage"] = "form_reload"
                try:
                    async with asyncio.timeout(75 + (3 * args.agent_timeout if agent else 0)):
                        page = await context.new_page()
                        await page.goto(case["final_url"], wait_until="domcontentloaded")
                        previous_shape, stable = None, 0
                        for _ in range(40):
                            snap = await snapshot(page)
                            if await dismiss_optional_cookies(page, snap):
                                snap = await snapshot(page)
                            shape = tuple((f.name, f.label, f.kind) for f in snap.fields)
                            stable = stable + 1 if shape == previous_shape else 0
                            previous_shape = shape
                            if _ >= 12 and stable >= 2 and len(snap.fields) >= case["field_count"]:
                                break
                            if not snap.fields and _ in (8, 16) and entry_controls(snap):
                                page = await click_and_settle(page, entry_controls(snap)[0])
                            await asyncio.sleep(0.25)
                        row["stage"] = "offline_fill"
                        row.update(
                            await probe_fields(page, profile, agent, agent_fill=args.agent_fill)
                        )
                        row["code"] = probe_code(row)
                except ApplicationError as exc:
                    row["code"] = exc.code
                    row["message"] = str(exc)
                except Exception as exc:  # noqa: BLE001 - continue independent diagnostic cases
                    row["code"] = (
                        "TIMEOUT" if type(exc).__name__ == "TimeoutError" else "BROWSER_ERROR"
                    )
                finally:
                    row["agent_calls_attempted"] = agent.calls - calls_before if agent else 0
                    await context.close()
                record(row)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
