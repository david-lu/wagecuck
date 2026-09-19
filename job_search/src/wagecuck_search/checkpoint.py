"""Persist public discovery records so URL validation can be rerun without rediscovery."""

import json
from dataclasses import asdict
from pathlib import Path

from .models import JobPosting, Salary, SiteResult

OPERATIONAL = {
    "max_pages",
    "max_per_site",
    "timeout_seconds",
    "site_timeout_seconds",
    "validation_timeout_seconds",
    "validation_workers",
    "detail_workers",
    "show_browser",
    "browser_channel",
}


def filters(criteria):
    return json.loads(
        json.dumps({k: v for k, v in asdict(criteria).items() if k not in OPERATIONAL})
    )


def save_discovery(path, criteria, results):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "filters": filters(criteria), "sites": [asdict(r) for r in results]}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def restore_discovery(payload, criteria):
    saved_filters = dict(payload.get("filters") or {})
    for old, new in (("partial_fields", "array_fields"), ("partial_filters", "array_filters")):
        if old in saved_filters:
            saved_filters.setdefault(new, saved_filters.pop(old))
    saved_filters.setdefault("array_fields", {})
    saved_filters.setdefault("array_filters", {})
    saved_filters.setdefault("field_filters", {})
    saved_filters.setdefault("salary_basis", "maximum")
    if payload.get("version") != 1 or saved_filters != filters(criteria):
        raise ValueError(
            "Discovery checkpoint must use the same title, filters, and selected sites"
        )
    providers = []
    for value in payload["sites"]:
        jobs = []
        for entry in value["jobs"]:
            entry = dict(entry)
            if "partial_fields" in entry:
                entry.setdefault("array_fields", entry.pop("partial_fields"))
            if entry.get("salary"):
                entry["salary"] = Salary(**entry["salary"])
            jobs.append(JobPosting(**entry))
        result = SiteResult(**(value | {"jobs": jobs}))
        providers.append(SavedProvider(result))
    return providers


class SavedProvider:
    def __init__(self, result):
        self.site, self.result = result.site, result

    async def fetch(self, query, criteria):
        return self.result
