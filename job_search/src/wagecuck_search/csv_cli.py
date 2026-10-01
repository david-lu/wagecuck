"""Independent search, validation, field generation, and filtering commands."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .field_filling import OpenAIFieldAgent, fill_fields
from .field_filter import OpenAIFieldFilterAgent
from .job_fields import (
    FIELD_TYPES,
    extraction_definitions,
    filter_definitions,
    normalize_field_types,
)
from .location_agent import OpenAILocationAgent
from .models import SITES, SearchCriteria
from .operations import (
    discover,
    fill_fields_csv,
    filter_csv,
    read_filter_jobs,
    require_distinct,
    save_report,
    validate_csv,
)
from .profiles import SearchProfile, resolve_search_profile_path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path else {}


def parser(action):
    command = argparse.ArgumentParser(
        prog=f"wagecuck-search {action}",
        description="Run one operation using the paths you supply; no workflow or preset filenames.",
    )
    if action == "search":
        command.add_argument("--query", "--job-title", dest="job_title")
        command.add_argument("--sites", choices=SITES, nargs="+")
        command.add_argument("--max-pages", type=int, default=200)
        command.add_argument("--max-per-site", type=int, default=10000)
        command.add_argument("--site-timeout-seconds", type=float, default=900)
        command.add_argument("--detail-workers", type=int, default=4)
    else:
        command.add_argument("input", type=Path, help="Input job CSV")
        command.add_argument("--array-column", "--partial-column", action="append", default=[],
                             help="Treat this custom CSV column as an array_field")
        command.add_argument("--field-type", nargs=2, action="append", default=[],
                             metavar=("FIELD", "TYPE"), help="Declare an imported column: " + ", ".join(FIELD_TYPES))
    command.add_argument("--output", type=Path, required=True, help="Output CSV path")
    if action in ("search", "filter"):
        command.add_argument("--profile", help="Shared profile name or search.json path")
    command.add_argument("--agent-model")
    command.add_argument("--agent-endpoint")
    command.add_argument("--agent-timeout", type=float, default=60)
    if action in ("search", "validate", "fill-fields"):
        command.add_argument("--fields", "--validation-fields", "--array-fields", "--partial-fields", type=Path,
                             required=action == "fill-fields",
                             help="JSON of typed fields with generation prompts")
        command.add_argument("--field-workers", type=int, default=4)
    if action in ("search", "validate"):
        command.add_argument("--timeout-seconds", type=float, default=30)
        command.add_argument("--show-browser", action="store_true")
        command.add_argument("--browser-channel", choices=("chrome", "msedge"))
    if action == "validate":
        command.add_argument("--validation-workers", type=int, default=8)
        command.add_argument("--validation-timeout-seconds", type=float, default=60)
        command.add_argument("--cache", type=Path, help="Optional validation journal for resuming")
    if action == "filter":
        for flag, mode in (("--filter", "any"), ("--filter-all", "all"), ("--filter-none", "none")):
            command.add_argument(flag, nargs=2, action="append", default=[],
                                 metavar=("FIELD", "CONDITION"),
                                 help=f"Keep rows where {mode} values match a prompt; --filter also accepts numeric comparisons")
        # Preserve the existing FIELD=prompt spelling.
        for flag in ("--field", "--all", "--none"):
            command.add_argument(flag, action="append", default=[], help=argparse.SUPPRESS)
        command.add_argument("--filters", type=Path, help="JSON field-to-predicate mapping")
        command.add_argument("--include-unknown", action=argparse.BooleanOptionalAction, default=None,
                             help="Keep rows with missing or unclassified filter values")
        command.add_argument("--location-prompt")
        command.add_argument("--min-salary", type=float)
        command.add_argument("--max-salary", type=float)
        command.add_argument("--salary-basis", choices=("minimum", "maximum"))
        command.add_argument("--salary-currency")
        command.add_argument("--salary-period", choices=("year", "month", "week", "day", "hour"),
                             default=None)
    return command


def predicates_from_args(args, profile_filters=None, array_filters=None, criteria_filters=False):
    supplied = read_json(args.filters)
    if not isinstance(supplied, dict):
        raise ValueError("--filters must be a JSON object")
    predicates = dict(profile_filters or {}) | supplied
    predicates = filter_definitions(predicates)
    for mode, pairs, legacy in (
        ("any", args.filter, args.field),
        ("all", args.filter_all, args.all),
        ("none", args.filter_none, args.none),
    ):
        entries = list(pairs)
        for entry in legacy:
            name, separator, prompt = entry.partition("=")
            if not separator:
                raise ValueError("Use FIELD=prompt, or --filter FIELD \"prompt\"")
            entries.append((name, prompt))
        for name, prompt in entries:
            spec = filter_definitions({name: prompt if mode == "any" else {"prompt": prompt, "mode": mode}})[name]
            if name in predicates:
                if "comparisons" in spec and "comparisons" in predicates[name]:
                    predicates[name]["comparisons"].extend(spec["comparisons"])
                else:
                    raise ValueError(f"Duplicate filter for {name}; combine conditions into one prompt")
            else:
                predicates[name] = spec
    predicates = filter_definitions(predicates)
    if (not predicates and not array_filters and not criteria_filters and not args.location_prompt
            and args.min_salary is None and args.max_salary is None):
        raise ValueError("Provide --filter FIELD PROMPT, --filters FILE, or a salary/location filter")
    if "location" in predicates and args.location_prompt:
        raise ValueError("Use either --filter location or --location-prompt")
    return predicates


def main(action, argv):
    command = parser(action)
    args = command.parse_args(argv)
    profile = None
    if action in ("search", "filter") and args.profile:
        try:
            profile = SearchProfile.load(resolve_search_profile_path(args.profile))
        except (OSError, ValueError, TypeError) as exc:
            command.error(f"Cannot load search profile: {exc}")
    if action == "search":
        args.job_title = args.job_title or (profile.search_queries[0] if profile else None)
        if not args.job_title:
            command.error("--query is required unless --profile is supplied")
        args.sites = args.sites or (profile.sites if profile else SITES)
    if action == "filter":
        preferences = profile.criteria_options if profile else {}
        for key, default in (
            ("include_unknown", False), ("salary_basis", "maximum"),
            ("salary_currency", "USD"), ("salary_period", "year"),
            ("min_salary", None),
        ):
            if getattr(args, key) is None:
                setattr(args, key, preferences.get(key, default))
        if args.location_prompt is None and profile:
            args.location_prompt = profile.location_prompt
        if args.max_salary is None and profile:
            args.max_salary = profile.max_salary
    agent_options = dict(model=args.agent_model, endpoint=args.agent_endpoint, timeout=args.agent_timeout)

    def progress(event):
        print(json.dumps(event), file=sys.stderr, flush=True)

    async def run():
        field_types = {}
        if action != "search":
            for name, kind in args.field_type:
                if name in field_types:
                    raise ValueError(f"Duplicate field type for {name}")
                field_types[name] = kind
            field_types = normalize_field_types(field_types)
            require_distinct(args.input, args.output)
            if not args.input.is_file():
                raise ValueError(f"Input CSV not found: {args.input}")
        if action == "filter":
            preferences = profile.criteria_options if profile else {}
            predicates = predicates_from_args(
                args, preferences.get("field_filters"), preferences.get("array_filters"),
                any(key in preferences for key in (
                    "seniority", "internship", "sponsors_visa", "workplace", "employment_type",
                    "keywords", "exclude_keywords", "exclude_companies", "posted_within_days",
                )),
            )
            other_preferences = {
                key: value for key, value in preferences.items()
                if key not in {"field_filters", "include_unknown", "min_salary", "salary_basis",
                               "salary_currency", "salary_period"}
            }
            criteria = SearchCriteria(
                "generated jobs", field_filters=predicates, include_unknown=args.include_unknown,
                min_salary=args.min_salary, salary_basis=args.salary_basis,
                salary_currency=args.salary_currency, salary_period=args.salary_period,
                **other_preferences,
            )
            # Catch misspelled fields and malformed input before constructing paid API clients.
            read_filter_jobs(args.input, criteria, array_columns=args.array_column, field_types=field_types)
            return await filter_csv(
                args.input, args.output, criteria, array_columns=args.array_column, field_types=field_types,
                progress=progress, location_prompt=args.location_prompt, max_salary=args.max_salary,
                agent=OpenAILocationAgent(**agent_options) if args.location_prompt else None,
                filter_agent=OpenAIFieldFilterAgent(**agent_options) if criteria.array_filters or any("prompt" in spec for spec in predicates.values()) else None,
            )
        definitions = extraction_definitions(read_json(args.fields))
        if definitions and not 1 <= args.field_workers <= 8:
            raise ValueError("Field filling workers must be between 1 and 8")
        field_agent = OpenAIFieldAgent(**agent_options) if definitions else None
        if action == "fill-fields":
            if not definitions:
                raise ValueError("--fields must define at least one field")
            return await fill_fields_csv(
                args.input, args.output, definitions, array_columns=args.array_column, field_types=field_types,
                progress=progress, agent=field_agent, workers=args.field_workers,
            )
        options = dict(
            timeout_seconds=args.timeout_seconds,
            show_browser=args.show_browser, browser_channel=args.browser_channel,
        )
        if action == "search":
            criteria = SearchCriteria(
                args.job_title, **options, sites=args.sites, max_pages=args.max_pages,
                max_per_site=args.max_per_site, site_timeout_seconds=args.site_timeout_seconds,
                detail_workers=args.detail_workers,
            )
            report = await discover(criteria, progress=progress)
            if definitions:
                filled = await fill_fields(
                    report["jobs"], definitions, agent=field_agent, workers=args.field_workers,
                    cache_path=args.output.parent / ".artifacts" / f"{args.output.stem}.fields.jsonl",
                    progress=progress,
                )
                report["jobs"] = filled["jobs"]
                report["field_definitions"] = definitions
                report["summary"]["field_filling"] = filled["summary"]
                report["summary"]["partial"] |= bool(filled["summary"]["failed"])
            save_report(args.output, report)
            return report
        criteria = SearchCriteria(
            "generated jobs", **options, validation_workers=args.validation_workers,
            validation_timeout_seconds=args.validation_timeout_seconds,
        )
        return await validate_csv(
            args.input, args.output, criteria, array_columns=args.array_column, field_types=field_types, progress=progress,
            field_definitions=definitions, field_agent=field_agent, field_workers=args.field_workers,
            cache_path=args.cache,
        )

    try:
        report = asyncio.run(run())
    except (OSError, ValueError, RuntimeError) as exc:
        command.error(str(exc))
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    summary = report["summary"]
    return int(bool(summary.get("partial") or summary.get("failed")))


if __name__ == "__main__":
    raise SystemExit("Use wagecuck-search search, validate, fill-fields, or filter")
