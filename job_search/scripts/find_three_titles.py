"""Resumable two-query discovery and one-pass validation for three requested titles."""

import argparse
import asyncio
import csv
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from wagecuck_search.checkpoint import save_discovery
from wagecuck_search.dedupe import canonical_url, deduplicate
from wagecuck_search.export import read_jobs, write_filtered_job_rows, write_jobs
from wagecuck_search.location_agent import OpenAILocationAgent
from wagecuck_search.matching import matches
from wagecuck_search.models import SearchCriteria, utc_now
from wagecuck_search.pipeline import _fetch, build_report
from wagecuck_search.postfilter import filter_jobs
from wagecuck_search.profiles import SearchProfile
from wagecuck_search.providers import provider_for
from wagecuck_search.stages import save_stage, validate_jobs
from wagecuck_search.validation import BrowserValidator, ValidationResult

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parents[1]
argument_parser = argparse.ArgumentParser(description=__doc__)
argument_parser.add_argument(
    "--profile",
    type=Path,
    default=BASE / "profiles" / "los-angeles-or-remote.json",
)
argument_parser.add_argument(
    "--validation-only",
    action="store_true",
    help="Validate the existing 02-filter.csv without rerunning search or filtering",
)
arguments = argument_parser.parse_args()
profile_path = arguments.profile
if not profile_path.is_absolute() and not profile_path.exists():
    profile_path = BASE / profile_path
PROFILE = SearchProfile.load(profile_path)
OUTPUT = BASE / PROFILE.output_directory
ARTIFACTS = OUTPUT / ".artifacts"
OUTPUT.mkdir(parents=True, exist_ok=True)
ARTIFACTS.mkdir(exist_ok=True)

REQUESTED_TITLES = PROFILE.job_titles
SEARCH_QUERIES = PROFILE.search_queries
SEARCH_CSV = OUTPUT / "01-search.csv"
CANDIDATE_CSV = ARTIFACTS / "title-matched.lossless.csv"
FILTER_CSV = OUTPUT / "02-filter.csv"
VALIDATION_CSV = OUTPUT / "03-validation.csv"
PROGRESS = ARTIFACTS / "progress.jsonl"
VALIDATION_JOURNAL = ARTIFACTS / "validation.jsonl"


def emit(event):
    value = {"at": datetime.now(timezone.utc).isoformat(), **event}
    with PROGRESS.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
    phase = value.get("phase")
    if phase != "discovery" or value.get("pages", 0) in (1, 2) or value.get("pages", 0) % 10 == 0:
        print(json.dumps(value, ensure_ascii=False), flush=True)


def title_matches(job):
    matched = []
    for title in REQUESTED_TITLES:
        criteria = SearchCriteria(title, sites=(job.source,))
        if matches(job, criteria)[0]:
            matched.append(title)
    return matched


def aggregate_sources(query_reports, removed):
    combined = {}
    for site in PROFILE.sites:
        entries = [report["summary"]["sites"][site] for report in query_reports.values()]
        statuses = list(dict.fromkeys(entry["status"] for entry in entries))
        combined[site] = {
            "status": ",".join(statuses),
            "discovered": sum(entry["discovered"] for entry in entries),
            "fetched": sum(entry["fetched"] for entry in entries),
            "deduplicated": removed.get(site, 0),
            "pages": sum(entry["pages"] for entry in entries),
            "detail_visits": sum(entry["detail_visits"] for entry in entries),
            "limited": any(entry["limited"] for entry in entries),
            "errors": [error for entry in entries for error in entry["errors"]],
        }
    return combined


def write_source_summary(path, sources, final_jobs=None):
    final_jobs = final_jobs or []
    columns = [
        "site", "status", "discovered", "fetched", "deduplicated", "pages",
        "detail_visits", "limited", "validated_jobs_with_source", "errors",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for site, stats in sources.items():
            writer.writerow(
                {
                    **stats,
                    "site": site,
                    "validated_jobs_with_source": sum(
                        any(source["site"] == site for source in job.get("sources", []))
                        for job in final_jobs
                    ),
                    "errors": " | ".join(stats["errors"]),
                }
            )


class CachedValidator:
    def __init__(self, validator, path):
        self.validator = validator
        self.path = path
        self.cached = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                    self.cached[value["key"]] = ValidationResult(**value["result"])
                except (ValueError, KeyError, TypeError):
                    continue

    def record(self, job, result):
        key = canonical_url(job.url)
        self.cached[key] = result
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({"key": key, "result": asdict(result)}, ensure_ascii=False) + "\n"
            )

    @staticmethod
    def cacheable(result):
        reason = (result.reason or "").lower()
        return not any(
            value in reason
            for value in ("http 429", "timed out", "could not be loaded", "failed (")
        )

    async def prepare(self, job):
        key = canonical_url(job.url)
        if key in self.cached:
            return self.cached[key]
        result = await self.validator.prepare(job)
        if isinstance(result, ValidationResult) and self.cacheable(result):
            self.record(job, result)
        return result

    async def finish(self, plan):
        result = await self.validator.finish(plan)
        if self.cacheable(result):
            self.record(plan.job, result)
        return result

    async def recycle(self):
        await self.validator.recycle()

    async def close(self):
        await self.validator.close()


async def discover_titles():
    if CANDIDATE_CSV.exists() and SEARCH_CSV.exists():
        candidates = read_jobs(CANDIDATE_CSV)
        for candidate in candidates:
            if candidate.get("note"):
                parts = [part.strip() for part in candidate["note"].split(";")]
                parts = [part for part in parts if not part.startswith("Matched requested title:")]
                candidate["note"] = "; ".join(parts) or None
        emit({"phase": "resume_discovery", "candidates": len(candidates)})
        return candidates, json.loads(
            (OUTPUT / "01-search.summary.json").read_text(encoding="utf-8")
        )

    raw_jobs = []
    query_reports = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for query in SEARCH_QUERIES:
                criteria = SearchCriteria(
                    query,
                    sites=PROFILE.sites,
                    max_pages=1000,
                    max_per_site=100000,
                    timeout_seconds=30,
                    site_timeout_seconds=1800,
                    detail_workers=8,
                )
                emit({"phase": "query_started", "query": query})
                results = await _fetch(
                    criteria,
                    query,
                    [provider_for(site, browser) for site in criteria.sites],
                    emit,
                )
                save_discovery(
                    ARTIFACTS / f"01-search-{query.replace(' ', '-')}.checkpoint.json",
                    criteria,
                    results,
                )
                report = build_report(criteria, query, results, utc_now(), apply_filters=False)
                query_reports[query] = report
                raw_jobs.extend(job for result in results for job in result.jobs)
                emit(
                    {
                        "phase": "query_complete",
                        "query": query,
                        "fetched": report["summary"]["fetched"],
                        "unique": report["summary"]["returned"],
                    }
                )
        finally:
            await browser.close()

    unique, removed = deduplicate(raw_jobs)
    write_jobs(SEARCH_CSV, [job.output() for job in unique])
    candidates = []
    title_counts = defaultdict(int)
    for job in unique:
        requested = title_matches(job)
        if not requested:
            continue
        for title in requested:
            title_counts[title] += 1
        candidates.append(job)
    write_jobs(CANDIDATE_CSV, [asdict(job) for job in candidates])
    sources = aggregate_sources(query_reports, removed)
    summary = {
        "stage": "search",
        "requested_titles": REQUESTED_TITLES,
        "broad_queries": SEARCH_QUERIES,
        "discovered": sum(v["summary"]["discovered"] for v in query_reports.values()),
        "fetched": len(raw_jobs),
        "deduplicated": len(raw_jobs) - len(unique),
        "unique_search_results": len(unique),
        "title_matched_candidates": len(candidates),
        "title_matches": dict(title_counts),
        "sites": sources,
        "queries": {key: value["summary"] for key, value in query_reports.items()},
    }
    (OUTPUT / "01-search.summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_source_summary(OUTPUT / "source-summary.csv", sources)
    return [asdict(job) for job in candidates], summary


async def filter_then_validate(candidates, search_summary):
    location_agent = OpenAILocationAgent() if PROFILE.location_prompt else None
    filtered = await filter_jobs(
        candidates,
        PROFILE.filter_criteria(),
        location_prompt=PROFILE.location_prompt,
        location_map_path=ARTIFACTS / "02-filter.locations.csv",
        agent=location_agent,
        max_salary=PROFILE.max_salary,
        progress=emit,
    )
    filter_title_counts = {
        title: sum(matches_from_output(job, title) for job in filtered["jobs"])
        for title in REQUESTED_TITLES
    }
    filter_summary = filtered["summary"] | {
        "requested_titles": REQUESTED_TITLES,
        "title_matches": filter_title_counts,
        "search_profile": PROFILE.name,
    }
    filtered["summary"] = filter_summary
    save_stage(FILTER_CSV, filtered)
    write_filtered_job_rows(
        SEARCH_CSV, FILTER_CSV, (job["url"] for job in filtered["jobs"])
    )
    await validate_filtered(filtered["jobs"], search_summary, filter_summary)


async def validate_filtered(filtered_jobs, search_summary, filter_summary):
    criteria = SearchCriteria(
        "software engineer",
        sites=PROFILE.sites,
        timeout_seconds=30,
        validation_timeout_seconds=45,
        validation_workers=64,
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        validator = CachedValidator(
            BrowserValidator(
                browser,
                criteria,
                browser_factory=lambda: playwright.chromium.launch(headless=True),
            ),
            VALIDATION_JOURNAL,
        )
        try:
            report = await validate_jobs(filtered_jobs, criteria, validator, emit)
        finally:
            await validator.close()
    save_stage(VALIDATION_CSV, report)
    write_source_summary(OUTPUT / "source-summary.csv", search_summary["sites"], report["jobs"])
    combined = {
        "output_directory": str(OUTPUT),
        "search": {key: value for key, value in search_summary.items() if key not in ("sites", "queries")},
        "filter": filter_summary,
        "validation": report["summary"],
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    emit(
        {
            "phase": "complete",
            "search_unique": search_summary["unique_search_results"],
            "title_candidates": search_summary["title_matched_candidates"],
            "profile_matched": len(filtered_jobs),
            "validated": len(report["jobs"]),
            "validation_rejected": report["summary"]["validation_rejected"],
            "deduplicated": search_summary["deduplicated"]
            + report["summary"]["application_url_deduplicated"],
        }
    )


def matches_from_output(job, title):
    from wagecuck_search.export import posting

    return int(matches(posting(job), SearchCriteria(title, sites=("simplify",)))[0])


async def main():
    if arguments.validation_only:
        required = [
            FILTER_CSV,
            OUTPUT / "01-search.summary.json",
            OUTPUT / "02-filter.summary.json",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError("Validation inputs are missing: " + ", ".join(missing))
        filtered_jobs = read_jobs(FILTER_CSV)
        search_summary = json.loads(
            (OUTPUT / "01-search.summary.json").read_text(encoding="utf-8")
        )
        filter_summary = json.loads(
            (OUTPUT / "02-filter.summary.json").read_text(encoding="utf-8")
        )
        emit({"phase": "validation_started", "input": len(filtered_jobs)})
        await validate_filtered(filtered_jobs, search_summary, filter_summary)
        return
    candidates, search_summary = await discover_titles()
    await filter_then_validate(candidates, search_summary)


asyncio.run(main())
