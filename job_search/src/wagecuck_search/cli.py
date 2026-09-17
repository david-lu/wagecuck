from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .checkpoint import restore_discovery, save_discovery
from .models import LEVELS, SITES, SearchCriteria
from .pipeline import search


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Search jobs independently of application workflows"
    )
    command.add_argument("--job-title", required=True)
    command.add_argument("--location", action="append", default=[], dest="locations")
    command.add_argument("--seniority", choices=LEVELS, nargs="+", default=[])
    command.add_argument("--min-salary", type=float)
    command.add_argument("--salary-currency", default="USD")
    command.add_argument(
        "--salary-period", choices=["year", "month", "week", "day", "hour"], default="year"
    )
    command.add_argument("--internship", action=argparse.BooleanOptionalAction, default=None)
    command.add_argument("--sponsors-visa", action=argparse.BooleanOptionalAction, default=None)
    command.add_argument("--workplace", choices=["remote", "hybrid", "onsite"])
    command.add_argument(
        "--employment-type", choices=["full_time", "part_time", "contract", "temporary"]
    )
    command.add_argument("--keyword", action="append", default=[], dest="keywords")
    command.add_argument("--exclude-keyword", action="append", default=[], dest="exclude_keywords")
    command.add_argument("--exclude-company", action="append", default=[], dest="exclude_companies")
    command.add_argument("--posted-within-days", type=int)
    command.add_argument(
        "--include-unknown",
        action="store_true",
        help="Keep unverified optional filters and label them in each job's note",
    )
    command.add_argument("--sites", choices=SITES, nargs="+", default=list(SITES))
    command.add_argument("--max-pages", type=int, default=200)
    command.add_argument("--max-per-site", type=int, default=10000)
    command.add_argument("--timeout-seconds", type=float, default=30)
    command.add_argument("--site-timeout-seconds", type=float, default=900)
    command.add_argument("--validation-workers", type=int, default=8)
    command.add_argument("--detail-workers", type=int, default=4)
    command.add_argument(
        "--discovery-output",
        type=Path,
        help="Save discovery records before URL validation (not verified results)",
    )
    command.add_argument(
        "--resume-discovery",
        type=Path,
        help="Load a discovery checkpoint and revalidate all matching native URLs",
    )
    command.add_argument(
        "--validation-timeout-seconds",
        type=float,
        default=60,
        help="Maximum time to resolve and validate each employer application URL",
    )
    command.add_argument("--show-browser", action="store_true")
    command.add_argument("--browser-channel", choices=["chrome", "msedge"])
    command.add_argument("--output", type=Path, help="Also save the JSON report to this path")
    return command


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    command = parser()
    args = vars(command.parse_args(argv))
    output = args.pop("output")
    discovery_output = args.pop("discovery_output")
    resume_discovery = args.pop("resume_discovery")
    try:
        criteria = SearchCriteria(**args)
    except ValueError as exc:
        command.error(str(exc))
    providers = None
    if resume_discovery:
        try:
            providers = restore_discovery(
                json.loads(resume_discovery.read_text(encoding="utf-8")), criteria
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            command.error(f"Cannot restore discovery checkpoint: {exc}")

    def progress(event):
        if event["phase"] == "discovery":
            print(
                f"[{event['site']}] {event['records']} records discovered in {event['pages']} batches"
                + (
                    f"; site reports {event['advertised_total']} broad matches"
                    if event.get("advertised_total") is not None
                    else ""
                ),
                file=sys.stderr,
            )
        else:
            print(
                f"[validation] {event['completed']}/{event['total']} matching jobs checked",
                file=sys.stderr,
            )

    checkpoint_error = False

    def checkpoint(results):
        nonlocal checkpoint_error
        if discovery_output:
            try:
                save_discovery(discovery_output, criteria, results)
            except OSError as exc:
                checkpoint_error = True
                print(f"Could not save discovery checkpoint: {exc}", file=sys.stderr)

    options = {"progress": progress, "on_discovery": checkpoint}
    if providers is not None:
        options["providers"] = providers
    report = asyncio.run(search(criteria, **options))
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    print(payload)
    summary = report["summary"]
    for site, stats in summary["sites"].items():
        print(
            f"{site}: {stats['discovered']} discovered, {stats['fetched']} extracted, "
            f"{stats['deduplicated']} deduplicated, "
            f"{stats['returned']} returned, {stats.get('validation_rejected', 0)} URL rejections "
            f"({stats['status']})",
            file=sys.stderr,
        )
    print(
        f"Total: {summary['returned']} jobs; {summary['deduplicated']} duplicates removed; "
        f"{summary['filtered_out']} filtered out; "
        f"{summary.get('validation_rejected', 0)} application URLs rejected",
        file=sys.stderr,
    )
    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Could not save report to {output}: {exc}", file=sys.stderr)
            return 1
    # Usable partial results are still printed; automation can detect degraded runs.
    return (
        1
        if (
            checkpoint_error
            or summary.get("validation_rejected")
            or any(s["status"] != "ok" for s in summary["sites"].values())
        )
        else 0
    )
