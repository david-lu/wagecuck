"""Run every corpus URL through live navigation, then isolated DOM filling."""

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from wagecuck.agent_config import add_agent_arguments, agent_arguments, create_agent
from wagecuck.evaluation import implementation_fingerprint, load_corpus


def summarize(report_path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = report["results"]
    for row in rows:
        if row.get("fields"):
            control_analysis = {
                field["field_id"]: field for field in row.get("control_analysis", [])
            }
            acted_ids = {outcome["field_id"] for outcome in row.get("control_outcomes", [])}
            for field in row["fields"]:
                if field["code"] == "ALREADY_FILLED_OR_NOT_SELECTED" and not field["kind"].endswith(
                    "_group"
                ):
                    field["code"] = "ALREADY_FILLED"
                acted_routes = list(
                    dict.fromkeys(
                        control_analysis[field_id]["route"]
                        for field_id in field["control_ids"]
                        if field_id in acted_ids and field_id in control_analysis
                    )
                )
                if acted_routes:
                    field["route"] = acted_routes[0] if len(acted_routes) == 1 else "mixed"
            satisfied_codes = {"FILLED", "ALREADY_FILLED"}
            required = [field for field in row["fields"] if field["required"]]
            failures = [field for field in required if field["code"] not in satisfied_codes]
            row["question_count"] = len(row["fields"])
            row["mapped_question_count"] = sum(
                field["code"] not in ("UNRESOLVED", "ALREADY_FILLED", "NOT_SELECTED_GROUP_OPTION")
                for field in row["fields"]
            )
            row["filled_question_count"] = sum(field["code"] == "FILLED" for field in row["fields"])
            row["satisfied_question_count"] = sum(
                field["code"] in satisfied_codes for field in row["fields"]
            )
            row["required_question_count"] = len(required)
            row["required_question_satisfied_count"] = len(required) - len(failures)
            row["required_fill_pass"] = not failures
            row["required_fill_failures"] = [field["question"] for field in failures]
        if "unmapped_fields" in row:
            row["required_answers_missing"] = list(
                dict.fromkeys(
                    (f["group"] or f["label"]) if f["kind"] == "radio" else f["label"]
                    for f in row["unmapped_fields"]
                    if f["required"]
                )
            )
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    totals = Counter(row["code"] for row in rows)
    agent = report.get("agent", {})
    lines = [
        "# Application dry-run coverage",
        "",
        (
            f"Checked: {report['checked_at']}. Dataset: {report.get('dataset_split', 'legacy')}. "
            f"URLs: {len(rows)}. Employer submissions: 0."
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
            "planner and field executor ran on the loaded DOM after network access was disabled. "
            "Service workers and WebSockets were blocked. Closed, inaccessible and login-gated "
            "URLs remain in this report as failures; they are not skipped from the totals."
        ),
        "",
        (
            "**These results do not establish complete application compatibility.** "
            "They cover navigation and the loaded form step. Server validation, upload acceptance, "
            "remote autocomplete, later pages and real CAPTCHA acceptance remain unverified. "
            "MAPPED_FIELDS_VERIFIED means only the profile mappings were retained locally."
        ),
        *(
            [
                "",
                (
                    "This is an aggregate-only held-out report. Field labels, options, mappings "
                    "and failure details were not persisted and must not be used to tune field "
                    "behavior."
                ),
            ]
            if report.get("aggregate_only")
            else []
        ),
        "",
        "Outcome counts: " + ", ".join(f"{code}: {count}" for code, count in totals.items()) + ".",
        "",
        "| Case | Outcome | Questions satisfied / total | Required satisfied / total | Required pass | Made-up answers filled | CAPTCHA detected |",
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
        if "made_up_answer_count" in row:
            made_up = str(row["made_up_answer_count"])
        elif row.get("fields") and all("made_up" in field for field in row["fields"]):
            made_up = str(
                sum(
                    field.get("made_up", False) and field["code"] == "FILLED"
                    for field in row["fields"]
                )
            )
        else:
            made_up = "not recorded"
        lines.append(
            f"| [{row['id']}]({row['url']}) | {row['code']} | {satisfied} | {required} | "
            f"{'yes' if row.get('required_fill_pass') else 'no' if 'required_fill_pass' in row else '—'} | "
            f"{made_up} | {row.get('captcha') or 'not observed'} |"
        )
    lines.extend(
        [
            "",
            (
                f"Aggregate per-case results are in [the JSON report]({report_path.name})."
                if report.get("aggregate_only")
                else "Field-level failures and exact unanswered questions are in "
                f"[the JSON report]({report_path.name})."
            ),
            "",
        ]
    )
    report_path.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifests",
        type=Path,
        nargs="+",
        default=None,
    )
    parser.add_argument("--split", choices=("training", "validation"), default="training")
    parser.add_argument("--profile", type=Path, default=Path("profiles/demo/profile.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize-only", action="store_true")
    add_agent_arguments(parser)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.manifests is None:
        args.manifests = [root / "examples" / f"{args.split}-jobs.json"]
    if args.output is None:
        args.output = root / "docs" / f"dry-run-{args.split}.json"
    corpora = [load_corpus(manifest, expected_split=args.split) for manifest in args.manifests]
    aggregate_only = args.split == "validation"
    fingerprint = implementation_fingerprint(root)
    if not args.summarize_only:
        try:
            create_agent(args)  # Fail before navigation if the model configuration is incomplete.
        except ValueError as exc:
            parser.error(str(exc))
        scripts = Path(__file__).parent
        reports = []
        expected = []
        for index, (manifest, corpus) in enumerate(zip(args.manifests, corpora, strict=True)):
            expected.extend(case["id"] for case in corpus.cases)
            report = args.output.parent / f"navigation-{args.split}-{index + 1}.json"
            command = [
                sys.executable,
                str(scripts / "inspect_live.py"),
                "--manifest",
                str(manifest),
                "--profile",
                str(args.profile),
                "--output",
                str(report),
                "--expected-split",
                args.split,
            ]
            if aggregate_only:
                command.extend(("--aggregate-only", "--ephemeral-artifacts"))
            subprocess.run(command, check=True)
            reports.append(str(report))
        probe_command = [
            sys.executable,
            str(scripts / "probe_live.py"),
            "--reports",
            *reports,
            "--profile",
            str(args.profile),
            "--output",
            str(args.output),
            "--dataset-split",
            args.split,
            "--implementation-fingerprint",
            fingerprint,
            *agent_arguments(args),
        ]
        if aggregate_only:
            probe_command.append("--aggregate-only")
        subprocess.run(probe_command, check=True)
        actual = [row["id"] for row in json.loads(args.output.read_text())["results"]]
        if Counter(expected) != Counter(actual):
            raise RuntimeError("Coverage report is incomplete: corpus/result IDs differ")
    summarize(args.output)


if __name__ == "__main__":
    main()
