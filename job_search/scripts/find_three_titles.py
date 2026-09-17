"""Resumable two-query discovery and one-pass validation for three requested titles."""

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
from wagecuck_search.export import read_jobs, write_jobs
from wagecuck_search.matching import matches
from wagecuck_search.models import SITES, SearchCriteria, utc_now
from wagecuck_search.pipeline import _fetch, build_report
from wagecuck_search.providers import provider_for
from wagecuck_search.stages import save_stage, validate_jobs
from wagecuck_search.validation import BrowserValidator, ValidationResult

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parents[1]
OUTPUT = BASE / "results" / "software-frontend-creative-technologist-2026-09-17"
ARTIFACTS = OUTPUT / ".artifacts"
OUTPUT.mkdir(parents=True, exist_ok=True)
ARTIFACTS.mkdir(exist_ok=True)

REQUESTED_TITLES = ("software engineer", "frontend engineer", "creative technologist")
SEARCH_QUERIES = ("software engineer", "creative technologist")
SEARCH_CSV = OUTPUT / "01-search.csv"
CANDIDATE_CSV = ARTIFACTS / "title-matched.lossless.csv"
VALIDATION_CSV = OUTPUT / "02-validation.csv"
FINAL_CSV = OUTPUT / "03-filter.csv"
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
    for site in SITES:
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
        emit({"phase": "resume_discovery", "candidates": len(read_jobs(CANDIDATE_CSV))})
        return read_jobs(CANDIDATE_CSV), json.loads(
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
                    sites=SITES,
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
        label = "Matched requested title: " + ", ".join(requested)
        job.note = "; ".join(filter(None, (job.note, label)))
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


async def validate_candidates(candidates, search_summary):
    criteria = SearchCriteria(
        "software engineer",
        sites=SITES,
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
            report = await validate_jobs(candidates, criteria, validator, emit)
        finally:
            await validator.close()
    save_stage(VALIDATION_CSV, report)
    write_jobs(FINAL_CSV, report["jobs"])
    final_title_counts = {
        title: sum(matches_from_output(job, title) for job in report["jobs"])
        for title in REQUESTED_TITLES
    }
    final_summary = {
        "stage": "filter",
        "input": len(report["jobs"]),
        "returned": len(report["jobs"]),
        "deduplicated": 0,
        "filter_reasons": {},
        "requested_titles": REQUESTED_TITLES,
        "title_matches": final_title_counts,
        "note": "No location, salary, or other post-generation filters were requested.",
    }
    (OUTPUT / "03-filter.summary.json").write_text(
        json.dumps(final_summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_source_summary(OUTPUT / "source-summary.csv", search_summary["sites"], report["jobs"])
    combined = {
        "output_directory": str(OUTPUT),
        "search": {key: value for key, value in search_summary.items() if key not in ("sites", "queries")},
        "validation": report["summary"],
        "filter": final_summary,
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    emit(
        {
            "phase": "complete",
            "search_unique": search_summary["unique_search_results"],
            "title_candidates": len(candidates),
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
    candidates, search_summary = await discover_titles()
    await validate_candidates(candidates, search_summary)


asyncio.run(main())
