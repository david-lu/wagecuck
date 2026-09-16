"""Run every corpus URL through live navigation, then isolated DOM filling."""

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def summarize(report_path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = report["results"]
    for row in rows:
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
    lines = [
        "# Application dry-run coverage",
        "",
        f"Checked: {report['checked_at']}. URLs: {len(rows)}. Employer submissions: 0.",
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
        "",
        "Outcome counts: " + ", ".join(f"{code}: {count}" for code, count in totals.items()) + ".",
        "",
        "| Case | Outcome | Fields filled / mapped | Missing required answers | CAPTCHA detected |",
        "|---|---|---:|---:|---|",
    ]
    for row in rows:
        mapped = f"{row['filled_count']} / {row['mapped_count']}" if "mapped_count" in row else "—"
        missing = (
            str(len(row["required_answers_missing"])) if "required_answers_missing" in row else "—"
        )
        lines.append(
            f"| [{row['id']}]({row['url']}) | {row['code']} | {mapped} | {missing} | {row.get('captcha') or 'not observed'} |"
        )
    lines += [
        "",
        "Field-level failures and exact unanswered questions are in [the JSON report](dry-run-all.json).",
        "",
    ]
    report_path.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifests",
        type=Path,
        nargs="+",
        default=[Path("examples/live-jobs.json"), Path("examples/expanded-jobs.json")],
    )
    parser.add_argument("--profile", type=Path, default=Path("profiles/demo/profile.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/dry-run-all.json"))
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    if not args.summarize_only:
        scripts = Path(__file__).parent
        reports = []
        expected = []
        for index, manifest in enumerate(args.manifests):
            expected.extend(case["id"] for case in json.loads(manifest.read_text())["cases"])
            report = args.output.parent / f"navigation-{index + 1}.json"
            subprocess.run(
                [
                    sys.executable,
                    str(scripts / "inspect_live.py"),
                    "--manifest",
                    str(manifest),
                    "--profile",
                    str(args.profile),
                    "--output",
                    str(report),
                ],
                check=True,
            )
            reports.append(str(report))
        subprocess.run(
            [
                sys.executable,
                str(scripts / "probe_live.py"),
                "--reports",
                *reports,
                "--profile",
                str(args.profile),
                "--output",
                str(args.output),
            ],
            check=True,
        )
        actual = [row["id"] for row in json.loads(args.output.read_text())["results"]]
        if Counter(expected) != Counter(actual):
            raise RuntimeError("Coverage report is incomplete: corpus/result IDs differ")
    summarize(args.output)


if __name__ == "__main__":
    main()
