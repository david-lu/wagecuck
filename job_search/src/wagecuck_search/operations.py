"""Independent operations with explicit input and output CSV paths."""

import csv
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from .checkpoint import SavedProvider
from .export import posting, public_job, read_jobs, write_filtered_job_rows, write_jobs, write_table
from .field_filling import fill_fields
from .field_filter import FIELD_VALUE_COLUMNS
from .job_fields import extraction_definitions, normalize_field_types, validate_filter_types
from .location_agent import LOCATION_COLUMNS
from .matching import broad_query
from .models import SiteResult, utc_now
from .pipeline import _fetch, build_report, search
from .postfilter import filter_jobs


def save_report(path, report):
    path = Path(path)
    write_jobs(path, report["jobs"], extra_columns=report.get("field_definitions", {}))
    public_report = {key: value for key, value in report.items()
                     if key not in ("locations", "rejections", "field_values")}
    public_report["jobs"] = [public_job(job) for job in report.get("jobs", [])]
    report_path = path.with_suffix(".json")
    report_temporary = report_path.with_name(report_path.name + ".tmp")
    report_temporary.write_text(
        json.dumps(public_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report_temporary.replace(report_path)
    summary_path = path.with_suffix(".summary.json")
    temporary = summary_path.with_name(summary_path.name + ".tmp")
    temporary.write_text(json.dumps(report["summary"], indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(summary_path)
    artifacts = path.parent / ".artifacts"
    if "locations" in report:
        write_table(artifacts / f"{path.stem}.locations.csv", LOCATION_COLUMNS, report["locations"])
    for name, rows in report.get("field_values", {}).items():
        write_table(
            artifacts / f"{path.stem}.field-values" / f"{name}.csv", FIELD_VALUE_COLUMNS, rows
        )
    if "rejections" in report:
        write_table(artifacts / f"{path.stem}.rejections.csv",
                    ["url", "title", "reason"], report["rejections"])


async def discover(criteria, providers=None, progress=None):
    started = utc_now()
    query = broad_query(criteria.job_title)
    if providers is None:
        from playwright.async_api import async_playwright

        from .providers import provider_for

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=not criteria.show_browser, channel=criteria.browser_channel)
            try:
                providers = [provider_for(site, browser) for site in criteria.sites]
                results = await _fetch(criteria, query, providers, progress)
            finally:
                await browser.close()
    else:
        results = await _fetch(criteria, query, providers, progress)
    report = build_report(criteria, query, results, started, apply_filters=False)
    report["summary"]["stage"] = "search"
    return report


async def validate_jobs(jobs, criteria, validator=None, progress=None):
    groups = defaultdict(list)
    for value in jobs:
        job = posting(value)
        groups[job.source].append(job)
    # SearchCriteria's public site list is for discovery; saved/custom CSV sources
    # are valid here and are never passed to a live board provider.
    options = replace(criteria)
    options.sites = tuple(groups)
    providers = [SavedProvider(SiteResult(site, records, discovered=len(records)))
                 for site, records in groups.items()]
    report = await search(
        options,
        providers,
        validator,
        progress=progress,
        filter_candidates=False,
        deduplicate_candidates=False,
    )
    summary = report["summary"]
    summary["stage"] = "validation"
    summary["input"] = len(jobs)
    summary.pop("unique_before_filtering", None)
    return report


async def fill_fields_csv(source, output, definitions, *, array_columns=(), field_types=None, **kwargs):
    """Fill prompt-defined fields from CSV evidence without navigation or row filtering."""
    require_distinct(source, output)
    output = Path(output)
    kwargs.setdefault("cache_path", output.parent / ".artifacts" / f"{output.stem}.fields.jsonl")
    report = await fill_fields(read_jobs(source, array_columns=array_columns, field_types=field_types), definitions, **kwargs)
    save_report(output, report)
    return report


async def validate_csv(source, output, criteria=None, *, validator=None, progress=None,
                       field_definitions=None, field_agent=None, field_workers=4,
                       array_columns=(), field_types=None, cache_path=None):
    """Validate any job CSV, retain native-page evidence, and optionally fill fields."""
    from .models import SearchCriteria

    require_distinct(source, output)
    definitions = extraction_definitions(field_definitions or {})
    if definitions and not 1 <= field_workers <= 8:
        raise ValueError("Field filling workers must be between 1 and 8")
    criteria = criteria or SearchCriteria("software engineer")
    jobs = read_jobs(source, array_columns=array_columns, field_types=field_types)
    if validator is None and jobs:
        from playwright.async_api import async_playwright

        from .validation import BrowserValidator
        from .validation_cache import CachedValidator

        async with async_playwright() as playwright:
            async def launch():
                return await playwright.chromium.launch(
                    headless=not criteria.show_browser, channel=criteria.browser_channel)
            browser = await launch()
            native = BrowserValidator(browser, criteria, browser_factory=launch)
            if cache_path:
                Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
                native = CachedValidator(native, Path(cache_path))
            try:
                report = await validate_jobs(jobs, criteria, native, progress)
            finally:
                await native.close()
    elif jobs:
        report = await validate_jobs(jobs, criteria, validator, progress)
    else:
        report = {"jobs": [], "summary": {"stage": "validation", "input": 0, "returned": 0,
                                        "validation_rejected": 0, "partial": False}}
    if definitions:
        output = Path(output)
        filled = await fill_fields(
            report["jobs"], definitions, agent=field_agent, workers=field_workers,
            cache_path=output.parent / ".artifacts" / f"{output.stem}.fields.jsonl",
            progress=progress,
        )
        report["jobs"] = filled["jobs"]
        report["field_definitions"] = definitions
        report["summary"]["field_filling"] = filled["summary"]
        report["summary"]["partial"] |= bool(filled["summary"]["failed"])
    save_report(output, report)
    return report


async def filter_csv(source, output, criteria, **kwargs):
    require_distinct(source, output)
    if kwargs.get("location_prompt") and kwargs.get("location_map_path") is None:
        output = Path(output)
        kwargs["location_map_path"] = output.parent / ".artifacts" / f"{output.stem}.locations.csv"
    output = Path(output)
    kwargs.setdefault(
        "field_map_dir", output.parent / ".artifacts" / f"{output.stem}.field-values"
    )
    array_columns = kwargs.pop("array_columns", ())
    jobs = read_filter_jobs(source, criteria, array_columns=array_columns,
                            field_types=kwargs.get("field_types"))
    report = await filter_jobs(jobs, criteria, **kwargs)
    save_report(output, report)
    # URL is not a row ID: two rows for one URL may have different field values.
    accepted = {id(job) for job in report["jobs"]}
    write_filtered_job_rows(source, output, (index for index, job in enumerate(jobs)
                                           if id(job) in accepted))
    return report


def require_distinct(source, output):
    if Path(source).resolve() == Path(output).resolve():
        raise ValueError("Input and output CSV paths must differ to preserve the source")


def read_filter_jobs(source, criteria, *, array_columns=(), field_types=None):
    jobs = read_jobs(source, array_columns=array_columns, field_types=field_types)
    with Path(source).open(encoding="utf-8-sig", newline="") as stream:
        columns = set(csv.DictReader(stream).fieldnames or [])
    available = set(columns)
    for name in ("minimum", "maximum", "currency", "period", "text"):
        if "salary_" + name in columns:
            available.add("salary." + name)
    for job in jobs:
        available.update(job.get("fields") or {})
        available.update(job.get("array_fields") or {})
    requested = set(criteria.field_filters) | set(criteria.array_filters) | set(array_columns) | set(field_types or {})
    missing = requested - available
    if missing:
        raise ValueError("Unknown filter field(s): " + ", ".join(sorted(missing))
                         + ". Available fields: " + ", ".join(sorted(available)))
    predicates = {name: {"prompt": prompt, "mode": "any"}
                  for name, prompt in criteria.array_filters.items()} | criteria.field_filters
    validate_filter_types(jobs, predicates, normalize_field_types(field_types or {}))
    return jobs
