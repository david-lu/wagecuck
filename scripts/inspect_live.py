"""Read-only live smoke tests. Explicitly hardcoded to inspect mode."""

import argparse
import asyncio
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.async_api import async_playwright

from wagecuck import ApplicationRunner, Profile, RunOptions
from wagecuck.agent import WorkflowAgent
from wagecuck.evaluation import (
    BrowserPool,
    add_concurrency_argument,
    default_report_path,
    load_corpus,
    run_cases,
    write_report,
)
from wagecuck.models import Code, Snapshot


def _inspection_row(case):
    return {
        "id": case["id"],
        "url": case["url"],
        "code": Code.INTERNAL_ERROR,
        "message": "Inspection did not complete.",
        "expected_ats": case.get("ats"),
        "ats": "generic",
        "final_url": "",
        "field_count": 0,
        "identity_fields_detected": False,
        "mapped_field_count": 0,
        "required_answers_missing": [],
        "fields": [],
        "run_id": "",
    }


async def inspect_case(
    case, profile, *, aggregate_only=False, ephemeral_artifacts=False, browser=None
):
    """Inspect one URL with isolated state; never enter profile values into the page."""
    row = _inspection_row(case)
    stage = "navigation"
    try:
        case_profile = profile.model_copy(deep=True)
        artifact_context = (
            TemporaryDirectory(prefix="wagecuck-validation-")
            if ephemeral_artifacts
            else nullcontext(None)
        )
        with artifact_context as temporary_artifacts:
            options = RunOptions(mode="inspect", timeout_seconds=45, action_timeout_ms=10000)
            if temporary_artifacts:
                options.artifacts_dir = Path(temporary_artifacts)
            runner = ApplicationRunner()
            result = (
                await runner.run(case["url"], case_profile, options, browser=browser)
                if browser is not None
                else await runner.run(case["url"], case_profile, options)
            )
            row.update(
                code=result.code,
                message=result.message,
                ats=result.ats,
                final_url=result.final_url,
                run_id=result.run_id,
            )
            stage = "snapshot"
            snapshots = sorted(Path(result.artifact_dir).glob("step-*.json"))
            last = json.loads(snapshots[-1].read_text(encoding="utf-8")) if snapshots else {}
            fields = Snapshot.model_validate(last).fields if last else []
        stage = "planning"
        actions, unresolved = await WorkflowAgent().plan(fields, case_profile)
        sources = {action.source for action in actions}
        row.update(
            field_count=len(fields),
            identity_fields_detected="facts:email" in sources
            and bool(sources & {"facts:first_name", "facts:full_name", "document:resume"}),
            mapped_field_count=len(actions),
            required_answers_missing=list(
                dict.fromkeys(
                    (field.group or field.label) if field.kind == "radio" else field.label
                    for field in unresolved
                    if field.required
                )
            ),
            fields=[
                {"label": field.label, "kind": field.kind, "required": field.required}
                for field in fields
            ],
        )
    except Exception as exc:  # noqa: BLE001 -- isolate unexpected per-case diagnostic failures
        # A malformed artifact or diagnostics failure must not cancel other URLs.
        # Keep known navigation failures and avoid leaking artifact contents in holdouts.
        if row["code"] in (Code.INTERNAL_ERROR, Code.INSPECTED):
            row["code"] = Code.INTERNAL_ERROR
            row["message"] = f"Inspection {stage} failed ({type(exc).__name__})."
        row["diagnostic_error"] = {"stage": stage, "type": type(exc).__name__}
    return {
        key: value
        for key, value in row.items()
        if not aggregate_only or key not in ("fields", "required_answers_missing")
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("examples/jobs.json"))
    parser.add_argument("--expected-split", choices=("training", "validation"), default="training")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--ephemeral-artifacts", action="store_true")
    parser.add_argument("--profile", type=Path, default=Path("profiles/demo/profile.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int)
    add_concurrency_argument(parser)
    args = parser.parse_args()
    args.output = args.output or default_report_path("live-report")
    corpus = load_corpus(args.manifest, expected_split=args.expected_split)
    cases = corpus.cases[: args.limit]
    profile = Profile.load(args.profile)
    completed = {}

    def report():
        return {
            "checked_at": datetime.now(UTC).isoformat(),
            "dataset_split": corpus.split,
            "aggregate_only": args.aggregate_only,
            "mode": "inspect",
            "concurrency": args.concurrency,
            "pool_enabled": args.pool,
            "applicant_data_entered": False,
            "applications_submitted": 0,
            "results": [completed[index] for index in sorted(completed)],
        }

    def collect(index, row):
        completed[index] = row
        print(json.dumps({key: value for key, value in row.items() if key != "fields"}), flush=True)
        write_report(args.output, report())

    async def worker(case, browser=None):
        return await inspect_case(
            case,
            profile,
            aggregate_only=args.aggregate_only,
            ephemeral_artifacts=args.ephemeral_artifacts,
            browser=browser,
        )

    write_report(args.output, report())
    if args.pool and cases:
        async with (
            async_playwright() as playwright,
            BrowserPool(playwright.chromium, args.concurrency) as pool,
        ):

            async def pooled_worker(case):
                try:
                    async with pool.lease() as browser:
                        return await worker(case, browser)
                except Exception as exc:  # noqa: BLE001 -- one failed browser slot is one case failure
                    row = _inspection_row(case)
                    row.update(
                        code=Code.BROWSER_ERROR,
                        message=f"Browser pool lease failed ({type(exc).__name__}).",
                    )
                    return {
                        key: value
                        for key, value in row.items()
                        if not args.aggregate_only
                        or key not in ("fields", "required_answers_missing")
                    }

            await run_cases(cases, pooled_worker, concurrency=args.concurrency, on_result=collect)
    else:
        await run_cases(cases, worker, concurrency=args.concurrency, on_result=collect)
    print(f"Report: {args.output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
