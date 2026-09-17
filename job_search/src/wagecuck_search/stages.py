"""Independent search -> validation -> filtering stages with portable CSV boundaries."""

import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from .checkpoint import SavedProvider
from .export import posting, read_jobs, write_jobs, write_table
from .location_agent import LOCATION_COLUMNS
from .matching import broad_query
from .models import SiteResult, utc_now
from .pipeline import _fetch, build_report, search
from .postfilter import filter_jobs


def save_stage(path, report):
    path = Path(path)
    write_jobs(path, report["jobs"])
    report_path = path.with_suffix(".json")
    report_temporary = report_path.with_name(report_path.name + ".tmp")
    report_temporary.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report_temporary.replace(report_path)
    summary_path = path.with_suffix(".summary.json")
    temporary = summary_path.with_name(summary_path.name + ".tmp")
    temporary.write_text(json.dumps(report["summary"], indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(summary_path)
    if "locations" in report:
        write_table(path.with_suffix(".locations.csv"), LOCATION_COLUMNS, report["locations"])
    if "rejections" in report:
        write_table(path.with_suffix(".rejections.csv"), ["url", "title", "reason"], report["rejections"])


async def discover(criteria, providers=None, progress=None):
    started = utc_now()
    query = broad_query(criteria.job_title)
    if providers is None:
        from playwright.async_api import async_playwright

        from .providers import BrowserProvider
        from .simplify import SimplifyProvider

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=not criteria.show_browser, channel=criteria.browser_channel)
            try:
                providers = [(SimplifyProvider if site == "simplify" else BrowserProvider)(site, browser)
                             for site in criteria.sites]
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
    report = await search(options, providers, validator, progress=progress, filter_candidates=False)
    report["summary"]["stage"] = "validation"
    return report


async def filter_csv(source, output, criteria, **kwargs):
    require_distinct(source, output)
    if kwargs.get("location_prompt") and kwargs.get("location_map_path") is None:
        kwargs["location_map_path"] = Path(output).with_suffix(".locations.csv")
    report = await filter_jobs(read_jobs(source), criteria, **kwargs)
    save_stage(output, report)
    return report


def require_distinct(source, output):
    if Path(source).resolve() == Path(output).resolve():
        raise ValueError("Input and output CSV paths must differ to preserve the previous stage")


async def run_stages(criteria, directory, *, providers=None, validator=None, progress=None,
                     location_prompt=None, agent=None, max_salary=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = [directory / name for name in ("01-search.csv", "02-validation.csv", "03-filter.csv")]
    if any(path.exists() for path in outputs):
        raise ValueError("Stage outputs already exist; choose a new output directory")
    discovered = await discover(criteria, providers, progress)
    save_stage(outputs[0], discovered)
    validated = await validate_jobs(discovered["jobs"], criteria, validator, progress)
    save_stage(outputs[1], validated)
    filtered = await filter_jobs(
        validated["jobs"], criteria,
        location_prompt=location_prompt,
        location_map_path=outputs[2].with_suffix(".locations.csv"),
        agent=agent,
        max_salary=max_salary,
    )
    save_stage(outputs[2], filtered)
    return {"search": discovered["summary"], "validation": validated["summary"],
            "filter": filtered["summary"]}
