"""Read-only live smoke tests. Explicitly hardcoded to inspect mode."""

import argparse
import asyncio
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from wagecuck import ApplicationRunner, Profile, RunOptions
from wagecuck.agent import WorkflowAgent
from wagecuck.evaluation import load_corpus
from wagecuck.models import Snapshot


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("examples/training-jobs.json"))
    parser.add_argument("--expected-split", choices=("training", "validation"), default="training")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--ephemeral-artifacts", action="store_true")
    parser.add_argument("--profile", type=Path, default=Path("profiles/demo/profile.json"))
    parser.add_argument("--output", type=Path, default=Path("runs/reports/live-report.json"))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    corpus = load_corpus(args.manifest, expected_split=args.expected_split)
    cases = corpus.cases
    profile = Profile.load(args.profile)
    rows = []
    for case in cases[: args.limit]:
        artifact_context = (
            TemporaryDirectory(prefix="wagecuck-validation-")
            if args.ephemeral_artifacts
            else nullcontext(None)
        )
        with artifact_context as temporary_artifacts:
            options = RunOptions(mode="inspect", timeout_seconds=45, action_timeout_ms=10000)
            if temporary_artifacts:
                options.artifacts_dir = Path(temporary_artifacts)
            result = await ApplicationRunner().run(case["url"], profile, options)
            snapshots = sorted(Path(result.artifact_dir).glob("step-*.json"))
            last = json.loads(snapshots[-1].read_text()) if snapshots else {}
        actions, unresolved = await WorkflowAgent().plan(
            Snapshot.model_validate(last).fields if last else [], profile
        )
        sources = {a.source for a in actions}
        identity = "facts:email" in sources and bool(
            sources & {"facts:first_name", "facts:full_name", "document:resume"}
        )
        detailed_row = {
            "id": case["id"],
            "url": case["url"],
            "code": result.code,
            "message": result.message,
            "expected_ats": case.get("ats"),
            "ats": result.ats,
            "final_url": result.final_url,
            "field_count": len(last.get("fields", [])),
            "identity_fields_detected": identity,
            "mapped_field_count": len(actions),
            "required_answers_missing": list(
                dict.fromkeys(
                    (f.group or f.label) if f.kind == "radio" else f.label
                    for f in unresolved
                    if f.required
                )
            ),
            "fields": [
                {"label": f["label"], "kind": f["kind"], "required": f["required"]}
                for f in last.get("fields", [])
            ],
            "run_id": result.run_id,
        }
        row = (
            {
                key: value
                for key, value in detailed_row.items()
                if key not in ("fields", "required_answers_missing")
            }
            if args.aggregate_only
            else detailed_row
        )
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "fields"}), flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "checked_at": datetime.now(UTC).isoformat(),
                    "dataset_split": corpus.split,
                    "aggregate_only": args.aggregate_only,
                    "mode": "inspect",
                    "applicant_data_entered": False,
                    "applications_submitted": 0,
                    "results": rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    asyncio.run(main())
