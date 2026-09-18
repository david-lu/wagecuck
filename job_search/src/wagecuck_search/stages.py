"""Independent search -> filtering -> validation stages with portable CSV boundaries."""

import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from .checkpoint import SavedProvider
from .export import posting, read_jobs, write_filtered_job_rows, write_jobs, write_table
from .location_agent import LOCATION_COLUMNS
from .matching import broad_query
from .models import SiteResult, utc_now
from .partial_filter import PARTIAL_COLUMNS
from .pipeline import _fetch, build_report, search
from .postfilter import filter_jobs


def save_stage(path, report):
    path = Path(path)
    write_jobs(path, report["jobs"])
    public_report = {key: value for key, value in report.items()
                     if key not in ("locations", "rejections", "partial_field_values")}
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
    for name, rows in report.get("partial_field_values", {}).items():
        write_table(
            artifacts / f"{path.stem}.partial-fields" / f"{name}.csv", PARTIAL_COLUMNS, rows
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


async def filter_csv(source, output, criteria, **kwargs):
    require_distinct(source, output)
    if kwargs.get("location_prompt") and kwargs.get("location_map_path") is None:
        output = Path(output)
        kwargs["location_map_path"] = output.parent / ".artifacts" / f"{output.stem}.locations.csv"
    output = Path(output)
    kwargs.setdefault(
        "partial_map_dir", output.parent / ".artifacts" / f"{output.stem}.partial-fields"
    )
    report = await filter_jobs(read_jobs(source), criteria, **kwargs)
    save_stage(output, report)
    write_filtered_job_rows(source, output, (job["url"] for job in report["jobs"]))
    return report


def require_distinct(source, output):
    if Path(source).resolve() == Path(output).resolve():
        raise ValueError("Input and output CSV paths must differ to preserve the previous stage")


async def run_stages(criteria, directory, *, providers=None, validator=None, progress=None,
                     location_prompt=None, agent=None, max_salary=None, partial_agent=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = [directory / name for name in ("01-search.csv", "02-filter.csv", "03-validation.csv")]
    if any(path.exists() for path in outputs):
        raise ValueError("Stage outputs already exist; choose a new output directory")
    discovered = await discover(criteria, providers, progress)
    save_stage(outputs[0], discovered)
    filtered = await filter_jobs(
        discovered["jobs"], criteria,
        location_prompt=location_prompt,
        location_map_path=directory / ".artifacts" / "02-filter.locations.csv",
        agent=agent,
        partial_agent=partial_agent,
        partial_map_dir=directory / ".artifacts" / "02-filter.partial-fields",
        max_salary=max_salary,
        progress=progress,
    )
    save_stage(outputs[1], filtered)
    write_filtered_job_rows(
        outputs[0], outputs[1], (job["url"] for job in filtered["jobs"])
    )
    validated = await validate_jobs(filtered["jobs"], criteria, validator, progress)
    save_stage(outputs[2], validated)
    return {"search": discovered["summary"], "filter": filtered["summary"],
            "validation": validated["summary"]}
