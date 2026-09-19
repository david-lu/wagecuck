from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .checkpoint import restore_discovery, save_discovery
from .field_filter import OpenAIFieldFilterAgent
from .location_agent import OpenAILocationAgent
from .models import LEVELS, SITES, SearchCriteria
from .operations import filter_csv
from .pipeline import search


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Commands: search, validate, fill-fields, filter. Use COMMAND --help. Legacy search flags are also accepted."
    )
    command.add_argument("--job-title")
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
    command.add_argument("--post-filter-input", type=Path, help="Filter an existing job CSV")
    command.add_argument("--post-filter-output", type=Path, help="Write the filtered job CSV")
    command.add_argument(
        "--location-prompt",
        help="Natural-language location request evaluated once over unique locations by an agent",
    )
    command.add_argument("--max-salary", type=float)
    command.add_argument(
        "--array-fields", "--partial-fields", type=Path,
        help="Use the CSV validate/search commands with --fields for prompt-based extraction",
    )
    command.add_argument(
        "--array-filter", "--partial-filter", action="append", default=[],
        help='Filter unique items once, then join to jobs: FIELD=natural-language request',
    )
    command.add_argument("--agent-model")
    command.add_argument("--agent-endpoint")
    command.add_argument(
        "--agent-timeout",
        type=float,
        default=60,
        help="Seconds to wait for one location-agent response before keeping that batch",
    )
    return command


def main(argv=None) -> int:
    values = list(argv) if argv is not None else sys.argv[1:]
    if values and values[0] in ("search", "validate", "fill-fields", "filter"):
        from .csv_cli import main as csv_main
        return csv_main(values[0], values[1:])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    command = parser()
    args = vars(command.parse_args(argv))
    output = args.pop("output")
    discovery_output = args.pop("discovery_output")
    resume_discovery = args.pop("resume_discovery")
    filter_input = args.pop("post_filter_input")
    filter_output = args.pop("post_filter_output")
    location_prompt = args.pop("location_prompt")
    max_salary = args.pop("max_salary")
    agent_model = args.pop("agent_model")
    agent_endpoint = args.pop("agent_endpoint")
    agent_timeout = args.pop("agent_timeout")
    definitions_path = args.pop("array_fields")
    array_filters = args.pop("array_filter")
    args["array_fields"] = {}
    args["array_filters"] = {}
    try:
        if definitions_path:
            raise ValueError(
                "Use validate INPUT --output OUTPUT --fields FILE "
                "with type and prompt definitions to add fields through the agent"
            )
        for entry in array_filters:
            name, separator, prompt = entry.partition("=")
            if not separator or name in args["array_filters"]:
                raise ValueError("Use one --array-filter FIELD=prompt per field")
            args["array_filters"][name] = prompt
    except (OSError, ValueError) as exc:
        command.error(str(exc))
    if array_filters and not filter_input:
        command.error("--array-filter requires --post-filter-input; or use the filter command")
    if bool(filter_input) != bool(filter_output):
        command.error("--post-filter-input and --post-filter-output must be used together")
    if (location_prompt or max_salary is not None) and not filter_input:
        command.error("--location-prompt and --max-salary require --post-filter-input; or use the filter command")
    if not args["job_title"] and not filter_input:
        command.error("--job-title is required unless --post-filter-input is used")
    if not args["job_title"]:
        args["job_title"] = "generated jobs"
    try:
        criteria = SearchCriteria(**args)
    except ValueError as exc:
        command.error(str(exc))
    agent = None
    effective_location_prompt = location_prompt or (
        " or ".join(criteria.locations) if criteria.locations else None
    )
    if effective_location_prompt and filter_input:
        try:
            agent = OpenAILocationAgent(
                model=agent_model, endpoint=agent_endpoint, timeout=agent_timeout
            )
        except ValueError as exc:
            command.error(str(exc))

    filter_agent = None
    if criteria.array_filters:
        try:
            filter_agent = OpenAIFieldFilterAgent(
                model=agent_model, endpoint=agent_endpoint, timeout=agent_timeout
            )
        except ValueError as exc:
            command.error(str(exc))

    def filter_progress(event):
        phase = event["phase"]
        if phase == "filter_started":
            message = f"[filter] loaded {event['input']} rows"
        elif phase == "filter_field":
            message = (
                f"[filter:{event['field']}] {event['unique_values']} unique items; "
                f"{event['cached']} cached, {event['pending']} pending"
            )
        elif phase == "filter_locations":
            message = (
                f"[filter] {event['unique_locations']} unique locations; "
                f"{event['cached']} cached, {event['pending']} pending"
            )
        elif phase == "filter_location_progress":
            message = (
                f"[filter] classified {event['completed']}/{event['total']} locations "
                f"({event['requests']} agent requests; {event['skipped']} skipped)"
            )
        elif phase == "filter_row_progress":
            message = (
                f"[filter] processed {event['processed']}/{event['total']} rows; "
                f"kept {event['kept']}, removed {event['removed']}"
            )
        elif phase == "filter_complete":
            message = (
                f"[filter] kept {event['returned']}/{event['input']} rows; "
                f"removed {event['filtered_out']} in {event['elapsed_seconds']:.2f}s"
            )
        else:
            return
        print(message, file=sys.stderr, flush=True)

    if filter_input:
        try:
            report = asyncio.run(filter_csv(
                filter_input,
                filter_output,
                criteria,
                location_prompt=effective_location_prompt,
                agent=agent,
                filter_agent=filter_agent,
                max_salary=max_salary,
                progress=filter_progress,
            ))
        except (OSError, ValueError, RuntimeError) as exc:
            command.error(str(exc))
        print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
        return 0
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
        elif event["phase"] == "validation":
            print(
                f"[validation] {event['completed']}/{event['total']} matching jobs checked",
                file=sys.stderr,
            )
        elif event["phase"] == "browser_validation":
            print(
                f"[validation] {event['remaining']} jobs require browser rendering",
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
