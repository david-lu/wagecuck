"""Run every application in job-search CSV/JSON outputs."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright
from pydantic import ValidationError

from wagecuck.agent_config import add_agent_arguments, create_agent
from wagecuck.cli import profiles_directory
from wagecuck.evaluation import (
    BrowserPool,
    add_concurrency_argument,
    default_report_path,
    run_cases,
    write_report,
)
from wagecuck.job_inputs import JobInput, load_job_inputs
from wagecuck.models import ApplicationResult, Code, Profile, RunOptions
from wagecuck.runner import ApplicationRunner

FAILURE_EXPLANATIONS = {
    "ACCESS_DENIED": "The job site denied browser access.",
    "AGENT_FAILED": "The configured model could not produce a valid answer plan.",
    "AUTH_REQUIRED": "The application requires an account or authenticated session.",
    "BROWSER_ERROR": "The browser failed while loading or processing the application.",
    "CAPTCHA_KEY_MISSING": "A CAPTCHA was reached but CapSolver is not configured.",
    "CAPTCHA_REQUIRED": "A CAPTCHA was reached before the application became ready.",
    "FIELD_FILL_FAILED": "One or more required controls rejected or did not retain a value.",
    "FORM_NOT_FOUND": "No application form or supported entry control was found.",
    "FORM_NOT_RELOADED": "The application form could not be reconstructed for the fill probe.",
    "JOB_CLOSED": "The posting was closed or no longer accepted applications.",
    "REQUIRED_ANSWER_MISSING": "No usable profile or inferred answer was available for a required control.",
    "TIMEOUT": "The application exceeded its time budget.",
    "UPLOAD_TIMEOUT": "A required document upload did not finish processing in time.",
    "UPLOAD_UNVERIFIED": "A required document upload could not be verified in the offline probe.",
    "VALIDATION_FAILED": "The required controls did not satisfy the page's validation state.",
}


def _compact(value, limit=180):
    text = " ".join(str(value).split()).replace("|", "\\|")
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _top_values(rows, key, limit=3):
    values = Counter(_compact(value) for row in rows for value in (row.get(key) or []) if value)
    return ", ".join(f"{value} ({count})" for value, count in values.most_common(limit))


def _failure_groups(rows):
    grouped = []
    for code, count in Counter(row.get("code", "UNKNOWN") for row in rows).most_common():
        matching = [row for row in rows if row.get("code", "UNKNOWN") == code]
        detail = FAILURE_EXPLANATIONS.get(code, f"The run ended with {code}.")
        messages = Counter(_compact(row["message"]) for row in matching if row.get("message"))
        if messages:
            detail += " Messages: " + "; ".join(
                f"{message} ({message_count})" for message, message_count in messages.most_common(2)
            )
        missing = _top_values(matching, "required_answers_missing")
        if missing:
            detail += f" Missing answers: {missing}."
        controls = _top_values(matching, "required_fill_failures")
        if controls:
            detail += f" Failed controls: {controls}."
        if code == "TIMEOUT":
            stages = Counter(_compact(row.get("stage", "unknown")) for row in matching)
            detail += (
                " Stages: "
                + ", ".join(
                    f"{stage} ({stage_count})" for stage, stage_count in stages.most_common()
                )
                + "."
            )
        grouped.append({"code": code, "count": count, "detail": detail})
    return grouped


def outcome_summary(report):
    """Summarize recorded results without changing their pass classification."""
    rows = report["results"]
    if report.get("mode") in ("dry-run", "fill", "submit"):
        expected_status = "succeeded" if report["mode"] == "submit" else "ready"
        passed = [row for row in rows if row.get("status") == expected_status]
        failed = [row for row in rows if row.get("status") != expected_status]
        return {
            "total": len(rows),
            "passed": len(passed),
            "failed": len(failed),
            "required_satisfied": None,
            "required_total": None,
            "failures": _failure_groups(failed),
        }
    passed = [row for row in rows if row.get("required_fill_pass") is True]
    failed = [row for row in rows if row.get("required_fill_pass") is not True]
    return {
        "total": len(rows),
        "passed": len(passed),
        "failed": len(failed),
        "required_satisfied": sum(row.get("required_question_satisfied_count", 0) for row in rows),
        "required_total": sum(row.get("required_question_count", 0) for row in rows),
        "failures": _failure_groups(failed),
    }


def render_terminal_summary(report):
    summary = outcome_summary(report)
    action = "Submitted" if report.get("mode") == "submit" else "Ready without submission"
    if report.get("mode") not in ("dry-run", "fill", "submit"):
        action = "Passed required fields"
    lines = [
        "",
        "Final results",
        f"  {action}: {summary['passed']} / {summary['total']}",
        f"  Failed or incomplete: {summary['failed']} / {summary['total']}",
    ]
    if summary["required_total"] is not None:
        lines.append(
            "  Required questions satisfied: "
            f"{summary['required_satisfied']} / {summary['required_total']}"
        )
    if report.get("duplicate_urls_removed"):
        lines.append(f"  Duplicate URLs removed: {report['duplicate_urls_removed']}")
    if summary["failures"]:
        lines.append("  Failure reasons:")
        lines.extend(
            f"    {failure['code']}: {failure['count']} - {failure['detail']}"
            for failure in summary["failures"]
        )
    return "\n".join(lines)


def _render_offline_probe_summary(report, report_name):
    rows = report["results"]
    totals = Counter(row["code"] for row in rows)
    agent = report.get("agent", {})
    lines = [
        "# Legacy offline DOM probe coverage",
        "",
        (
            f"Checked: {report['checked_at']}. Dataset: {report.get('dataset_split', 'run')}. "
            f"URLs: {len(rows)}. Concurrency: {report.get('concurrency', 1)}. "
            f"Browser pool: {report.get('pool_enabled', False)}. Employer submissions: 0."
        ),
        (
            f"Model provider: {agent.get('provider', 'not recorded')}. "
            f"Model: {agent.get('model') or 'none'}. "
            f"Model calls attempted: {agent.get('calls_attempted', 'not recorded')}. "
            f"Profile inference and invented-answer fallback: {agent.get('agent_fill', False)}."
        ),
        "",
        (
            "Every URL was attempted in a fresh browser context. For reachable forms, the real "
            "planner and field executor ran on the loaded DOM after network access was disabled."
        ),
        "",
        "Outcome counts: " + ", ".join(f"{code}: {count}" for code, count in totals.items()) + ".",
        "",
        "| Case | Outcome | Questions satisfied / total | Required satisfied / total | Required pass | Made-up answers | CAPTCHA |",
        "|---|---|---:|---:|---|---:|---|",
    ]
    for row in rows:
        satisfied = (
            f"{row.get('satisfied_question_count', row.get('filled_question_count', row['filled_count']))} / "
            f"{row.get('question_count', row.get('mapped_question_count', row['mapped_count']))}"
            if "field_count" in row
            else "—"
        )
        required = (
            f"{row['required_question_satisfied_count']} / {row['required_question_count']}"
            if "required_question_count" in row
            else "—"
        )
        made_up = str(row.get("made_up_answer_count", "not recorded"))
        lines.append(
            f"| [{row['id']}]({row['url']}) | {row['code']} | {satisfied} | {required} | "
            f"{'yes' if row.get('required_fill_pass') else 'no' if 'required_fill_pass' in row else '—'} | "
            f"{made_up} | {row.get('captcha') or 'not observed'} |"
        )
    lines.extend(["", f"Full per-case data: [{report_name}]({report_name}).", ""])
    return lines


def _render_live_summary(report, report_name):
    lines = [
        f"# Application batch: {report['mode']}",
        "",
        (
            f"Started: {report['started_at']}. Inputs: {len(report['input_files'])}. "
            f"Unique jobs: {report['total']}. Concurrency: {report['concurrency']}. "
            f"Browser pool: {report['pool_enabled']}."
        ),
        "",
        "| Company | Role | Outcome | Submitted | URL |",
        "|---|---|---|---|---|",
    ]
    for row in report["results"]:
        source = row.get("input", {})
        lines.append(
            f"| {_compact(source.get('company', ''))} | {_compact(source.get('title', ''))} | "
            f"{row['code']} | {'yes' if row.get('submitted') else 'no'} | "
            f"[application]({row['job_url']}) |"
        )
    lines.extend(["", f"Full per-application data: [{report_name}]({report_name}).", ""])
    return lines


def render_summary(report, report_name):
    lines = (
        _render_live_summary(report, report_name)
        if report.get("mode") in ("dry-run", "fill", "submit")
        else _render_offline_probe_summary(report, report_name)
    )
    summary = outcome_summary(report)
    action = "Submitted" if report.get("mode") == "submit" else "Ready without submission"
    if report.get("mode") not in ("dry-run", "fill", "submit"):
        action = "Passed required fields"
    lines.extend(
        [
            "## Final results",
            "",
            f"- {action}: **{summary['passed']} / {summary['total']}**",
            f"- Failed or incomplete: **{summary['failed']} / {summary['total']}**",
        ]
    )
    if summary["required_total"] is not None:
        lines.append(
            "- Required questions satisfied on reached forms: "
            f"**{summary['required_satisfied']} / {summary['required_total']}**"
        )
    lines.extend(
        [
            "",
            "### Failure reasons",
            "",
            "| Cases | Code | Why |",
            "|---:|---|---|",
            *(
                [
                    f"| {failure['count']} | {failure['code']} | {failure['detail']} |"
                    for failure in summary["failures"]
                ]
                or ["| 0 | — | All cases passed. |"]
            ),
            "",
        ]
    )
    return "\n".join(lines)


def summarize(report_path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.with_suffix(".md").write_text(
        render_summary(report, report_path.name), encoding="utf-8"
    )


def _failed_result(job: JobInput, profile: Profile, mode: str, message: str):
    timestamp = datetime.now(UTC).isoformat()
    return ApplicationResult(
        run_id=uuid4().hex,
        profile_id=profile.id,
        job_url=job.url,
        mode=mode,
        code=Code.BROWSER_ERROR,
        message=message,
        started_at=timestamp,
        finished_at=timestamp,
    )


async def run_live(jobs, profile, args, output, duplicates, dataset_split="run"):
    execution_mode = args.mode
    options = RunOptions(
        mode=execution_mode,
        headless=not args.headed,
        agent_fill=args.agent_fill,
        slow_mo_ms=args.slow_mo,
        timeout_seconds=args.timeout,
        artifacts_dir=args.artifacts,
        database=args.database,
        storage_state=args.storage_state,
        capture_sensitive_artifacts=args.sensitive_artifacts,
    )
    completed = {}
    started_at = datetime.now(UTC).isoformat()

    def payload():
        return {
            "schema_version": 1,
            "mode": args.mode,
            "execution_mode": execution_mode,
            "dataset_split": dataset_split,
            "network_disabled_before_filling": False,
            "final_submission_enabled": args.mode == "submit",
            "profile_id": profile.id,
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat() if len(completed) == len(jobs) else None,
            "input_files": [str(path.resolve()) for path in args.inputs],
            "duplicate_urls_removed": duplicates,
            "total": len(jobs),
            "completed": len(completed),
            "concurrency": args.concurrency,
            "pool_enabled": args.pool,
            "results": [completed[index] for index in sorted(completed)],
        }

    def record(index, result):
        row = result.model_dump(mode="json")
        row["execution_mode"] = row["mode"]
        row["mode"] = args.mode
        row["input"] = jobs[index].as_dict()
        completed[index] = row
        write_report(output, payload())
        expected = "succeeded" if args.mode == "submit" else "ready"
        status = "PASS" if row["status"] == expected else "FAIL"
        print(
            f"[{args.mode} {len(completed)}/{len(jobs)}] {status}: {jobs[index].id} "
            f"({row['code']})",
            flush=True,
        )

    async def execute(job, browser=None):
        try:
            runner = ApplicationRunner(create_agent(args))
            return await runner.run(
                job.url,
                profile.model_copy(deep=True),
                options.model_copy(deep=True),
                browser=browser,
            )
        except Exception as exc:  # noqa: BLE001 - preserve the rest of the batch
            return _failed_result(
                job, profile, args.mode, f"Batch worker failed ({type(exc).__name__})."
            )

    write_report(output, payload())
    if args.pool:
        launch_options = {"headless": not args.headed, "slow_mo": args.slow_mo}
        async with (
            async_playwright() as playwright,
            BrowserPool(
                playwright.chromium, args.concurrency, launch_options=launch_options
            ) as pool,
        ):

            async def pooled(job):
                try:
                    async with pool.lease() as browser:
                        return await execute(job, browser)
                except Exception as exc:  # noqa: BLE001 - one pool slot is one failed job
                    return _failed_result(
                        job, profile, args.mode, f"Browser pool failed ({type(exc).__name__})."
                    )

            await run_cases(jobs, pooled, concurrency=args.concurrency, on_result=record)
    else:
        await run_cases(jobs, execute, concurrency=args.concurrency, on_result=record)
    return payload()


def positive_limit(value):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("limit must be a positive integer")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="*")
    parser.add_argument(
        "--mode",
        choices=("dry-run", "fill", "submit"),
        default="dry-run",
        help="dry-run uses live fill behavior with a synthetic profile and never submits",
    )
    parser.add_argument("--split", choices=("training", "validation"), default=None)
    parser.add_argument("--profile", type=Path, default=profiles_directory() / "dummy/profile.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize-only", action="store_true")
    parser.add_argument("--limit", type=positive_limit)
    parser.add_argument("--slow-mo", type=int, default=0, metavar="MS")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--artifacts", type=Path, default=Path(".artifacts/application/runs"))
    parser.add_argument(
        "--database", type=Path, default=Path(".artifacts/application/applications.sqlite3")
    )
    parser.add_argument("--storage-state", type=Path)
    parser.add_argument("--sensitive-artifacts", action="store_true")
    visibility = parser.add_mutually_exclusive_group()
    visibility.add_argument("--show-browser", "--headed", dest="headed", action="store_true")
    visibility.add_argument("--headless", dest="headed", action="store_false")
    parser.set_defaults(headed=None)
    add_agent_arguments(parser)
    add_concurrency_argument(parser)
    args = parser.parse_args(argv)
    if args.summarize_only:
        if args.output is None or not args.output.is_file():
            parser.error("--summarize-only requires --output pointing to an existing JSON report")
        summarize(args.output)
        report = json.loads(args.output.read_text(encoding="utf-8"))
        print(f"Report: {args.output}")
        print(render_terminal_summary(report))
        return 0
    if not args.inputs:
        parser.error("at least one job-search CSV or JSON input is required")
    try:
        jobs, dataset_split, duplicates = load_job_inputs(
            args.inputs, expected_split=args.split, limit=args.limit
        )
        create_agent(args)
        RunOptions(
            mode=args.mode,
            slow_mo_ms=args.slow_mo,
            timeout_seconds=args.timeout,
        )
        profile = Profile.load(args.profile)
    except (OSError, TypeError, ValidationError, ValueError) as exc:
        parser.error(str(exc))
    if args.mode == "dry-run" and not profile.synthetic:
        parser.error("dry-run requires a synthetic profile; use --mode fill for real data")
    if args.mode == "submit" and profile.synthetic:
        parser.error("submit mode requires a non-synthetic profile")
    if args.output is None:
        args.output = default_report_path(f"{args.mode}-all", directory=Path("runs") / "reports")
    if any(args.output.resolve() == path.resolve() for path in args.inputs):
        parser.error("--output must differ from every input file")
    report = asyncio.run(
        run_live(jobs, profile, args, args.output, duplicates, dataset_split=dataset_split)
    )
    summarize(args.output)
    print(f"Report: {args.output.resolve()}", flush=True)
    print(render_terminal_summary(report), flush=True)
    summary = outcome_summary(report)
    if summary["failed"] == 0:
        return 0
    if any(row.get("status") == "unknown" for row in report["results"]):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
