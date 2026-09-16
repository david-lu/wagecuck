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
    fill,
    snapshot,
    verify_actions,
)
from wagecuck.captcha import detect_challenge
from wagecuck.logical_fields import logical_field_results
from wagecuck.models import ApplicationError, Profile


async def probe_fields(page, profile, agent=None, *, agent_fill=False):
    # Register the block before any profile values enter page JavaScript. The
    # caller also blocks service workers and WebSockets from context creation.
    await page.context.route("**/*", lambda route: route.abort())
    await page.context.set_offline(True)
    snap = await snapshot(page)
    challenge = await detect_challenge(page)
    planner = WorkflowAgent(agent)
    actions, unresolved = await planner.plan(snap.fields, profile, agent_fill=agent_fill)
    outcomes, completed = [], []
    for action in actions:
        outcome = {
            "field_id": f"{action.field.frame}:{action.field.id}",
            "label": action.field.label,
            "group": action.field.group,
            "kind": action.field.kind,
            "code": "FILLED",
            "required": action.field.required,
            "source": action.source,
            "answer_basis": action.answer_basis,
            "made_up": action.made_up,
            "source_keys": action.source_keys,
            "inference_reason": action.inference_reason,
            "selected": action.value is True
            if action.field.kind in ("radio", "checkbox")
            else None,
        }
        try:
            await fill(page, action)
            completed.append(action)
        except ApplicationError as exc:
            outcome["code"] = exc.code
        outcomes.append(outcome)
    try:
        await verify_actions(page, completed)
        retained = True
    except ApplicationError:
        retained = False
    analysis = planner.describe(snap.fields, actions, unresolved)
    logical_fields = logical_field_results(snap.fields, outcomes, analysis)
    unresolved_questions = [field for field in logical_fields if field["code"] == "UNRESOLVED"]
    satisfied_codes = {"FILLED", "ALREADY_FILLED"}
    required_questions = [field for field in logical_fields if field["required"]]
    required_failures = [
        field for field in required_questions if field["code"] not in satisfied_codes
    ]
    return {
        "field_count": len(snap.fields),
        "question_count": len(logical_fields),
        "mapped_count": len(actions),
        "filled_count": len(completed),
        "mapped_question_count": sum(
            field["code"] not in ("UNRESOLVED", "ALREADY_FILLED", "NOT_SELECTED_GROUP_OPTION")
            for field in logical_fields
        ),
        "filled_question_count": sum(field["code"] == "FILLED" for field in logical_fields),
        "satisfied_question_count": sum(
            field["code"] in satisfied_codes for field in logical_fields
        ),
        "required_question_count": len(required_questions),
        "required_question_satisfied_count": len(required_questions) - len(required_failures),
        "required_fill_pass": not required_failures,
        "required_fill_failure_count": len(required_failures),
        "required_fill_failures": [field["question"] for field in required_failures],
        "made_up_answer_count": sum(
            field.get("made_up", False) and field["code"] == "FILLED" for field in logical_fields
        ),
        "completed_values_retained": retained,
        "captcha": challenge.kind if challenge else None,
        "required_answers_missing": [
            field["question"] for field in unresolved_questions if field["required"]
        ],
        "unmapped_fields": [
            {
                "label": field["question"],
                "group": field["question"],
                "kind": field["kind"],
                "required": field["required"],
                "options": field.get("options", []),
            }
            for field in unresolved_questions
        ],
        "unmapped_controls": [
            {"label": f.label, "group": f.group, "kind": f.kind, "required": f.required}
            for f in unresolved
        ],
        "fields": logical_fields,
        "control_outcomes": outcomes,
        "field_analysis": logical_fields,
        "control_analysis": analysis,
        "agent_warnings": planner.warnings,
    }


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
                        row["code"] = (
                            "FORM_NOT_RELOADED"
                            if not row["field_count"]
                            else "FIELD_FILL_FAILED"
                            if row["filled_count"] < row["mapped_count"]
                            or not row["completed_values_retained"]
                            else "REQUIRED_ANSWER_MISSING"
                            if row["required_answers_missing"]
                            else "NO_MAPPED_FIELDS"
                            if not row["mapped_count"]
                            else "MAPPED_FIELDS_VERIFIED"
                        )
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
