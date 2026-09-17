"""Durable execution harness for the requested full search and CSV export."""

import asyncio
import csv
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from wagecuck_search.application_links import BOARD_DOMAINS, on_domain, web_url
from wagecuck_search.checkpoint import save_discovery
from wagecuck_search.dedupe import canonical_url
from wagecuck_search.models import SearchCriteria
from wagecuck_search.pipeline import search
from wagecuck_search.providers import BrowserProvider
from wagecuck_search.simplify import SimplifyProvider
from wagecuck_search.validation import BrowserValidator

BASE = Path(__file__).resolve().parents[1]
RUN_ROOT = BASE / ".artifacts" / "runs"
RUN = RUN_ROOT / ("full-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
RUN.mkdir(parents=True)
ARTIFACTS = RUN / ".artifacts"
ARTIFACTS.mkdir(exist_ok=True)
(RUN_ROOT / ".artifacts").mkdir(exist_ok=True)
(RUN_ROOT / ".artifacts" / "latest-full-run.txt").write_text(str(RUN), encoding="utf-8")
FIELDS = ["url", "title", "company", "location", "salary_minimum", "salary_maximum",
          "salary_currency", "salary_period", "salary_text", "last_updated", "posted_at",
          "internship", "sponsors_visa", "workplace", "employment_type", "experience_levels",
          "note", "url_validated_at", "application_url_type", "source_sites", "source_urls"]


def row(job):
    result = {k: job.get(k, "") for k in FIELDS}
    salary = job.get("salary") or {}
    for key in ("minimum", "maximum", "currency", "period", "text"):
        result["salary_" + key] = salary.get(key, "")
    result["experience_levels"] = " | ".join(job.get("experience_levels", []))
    result["source_sites"] = " | ".join(dict.fromkeys(s["site"] for s in job.get("sources", [])))
    result["source_urls"] = " | ".join(s["url"] for s in job.get("sources", []))
    for key, value in result.items():
        if value is None:
            result[key] = ""
        elif isinstance(value, bool):
            result[key] = "true" if value else "false"
    return result


def write_csv(path, jobs):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(row(j) for j in jobs)


async def main():
    sys.stdout.reconfigure(encoding="utf-8")
    criteria = SearchCriteria("software engineer", seniority=("senior", "staff"),
                              browser_channel="msedge", validation_workers=12)
    (ARTIFACTS / "criteria.json").write_text(json.dumps(asdict(criteria), indent=2), encoding="utf-8")
    print("RUN_DIRECTORY=" + str(RUN), flush=True)
    progress_stream = (ARTIFACTS / "progress.jsonl").open("a", encoding="utf-8", buffering=1)
    journal = (ARTIFACTS / "validation.jsonl").open("a", encoding="utf-8", buffering=1)
    csv_stream = (RUN / "jobs.csv").open("w", newline="", encoding="utf-8-sig", buffering=1)
    writer = csv.DictWriter(csv_stream, fieldnames=FIELDS)
    writer.writeheader()
    seen, counts = set(), {"checked": 0, "verified": 0, "rejected": 0}

    def progress(event):
        event = {"at": datetime.now(timezone.utc).isoformat(), **event}
        progress_stream.write(json.dumps(event) + "\n")
        print(json.dumps(event), flush=True)

    def discovery(results):
        save_discovery(ARTIFACTS / "01-search.checkpoint.json", criteria, results)
        progress({"phase": "discovery_complete", "sites": [
            {"site": r.site, "records": len(r.jobs), "status": r.status, "errors": r.errors}
            for r in results]})

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="msedge")
            native = BrowserValidator(browser, criteria)

            class JournalValidator:
                async def validate(self, job):
                    check = await native.validate(job)
                    counts["checked"] += 1
                    journal.write(json.dumps({"job": asdict(job), "validation": asdict(check)},
                                             ensure_ascii=False) + "\n")
                    valid = (not check.reason and check.url and web_url(check.url)
                             and not on_domain(check.url, BOARD_DOMAINS)
                             and check.kind in ("employer", "ats"))
                    if valid:
                        counts["verified"] += 1
                        key = canonical_url(check.url)
                        if key not in seen:
                            seen.add(key)
                            posting = job.output() | {"url": check.url,
                                "url_validated_at": check.checked_at,
                                "application_url_type": check.kind}
                            writer.writerow(row(posting))
                            csv_stream.flush()
                    else:
                        counts["rejected"] += 1
                    if counts["checked"] % 25 == 0:
                        progress({"phase": "validation_counts", **counts,
                                  "unique_csv_rows": len(seen)})
                    return check

            class SavedProvider:
                def __init__(self, provider):
                    self.provider, self.site = provider, provider.site
                    self.progress = None

                async def fetch(self, query, options):
                    self.provider.progress = self.progress
                    result = await self.provider.fetch(query, options)
                    save_discovery(ARTIFACTS / ("01-search." + self.site + ".checkpoint.json"), criteria, [result])
                    progress({"phase": "site_complete", "site": self.site,
                              "records": len(result.jobs), "status": result.status,
                              "errors": result.errors})
                    return result

            providers = [SavedProvider((SimplifyProvider if site == "simplify" else BrowserProvider)(site, browser))
                         for site in criteria.sites]
            report = await search(criteria, providers, JournalValidator(), progress=progress,
                                  on_discovery=discovery)
            await browser.close()
        csv_stream.close()
        # Final export includes merged source provenance and filter notes from the pipeline.
        write_csv(RUN / "jobs.csv", report["jobs"])
        (RUN / "03-validation.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        summary = report["summary"]
        columns = ["site", "discovered", "fetched", "deduplicated", "validation_attempted",
                   "validation_rejected", "returned", "status", "limited"]
        with (RUN / "summary.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            out = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            out.writeheader()
            for site, stats in summary["sites"].items():
                out.writerow({"site": site, **stats})
            out.writerow({"site": "TOTAL", **summary})
        (RUN / "03-validation.summary.json").write_text(json.dumps({
            "csv": str(RUN / "jobs.csv"), "count": len(report["jobs"]), "summary": summary
        }, indent=2), encoding="utf-8")
        print("COMPLETE=" + json.dumps({"csv": str(RUN / "jobs.csv"), "count": len(report["jobs"])}), flush=True)
    finally:
        csv_stream.close()
        journal.close()
        progress_stream.close()

asyncio.run(main())
