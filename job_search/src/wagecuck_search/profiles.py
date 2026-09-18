"""Load user-owned search profiles for repeatable broad-search/filter runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path

from .models import SITES, SearchCriteria

FILTER_KEYS = {
    "seniority",
    "min_salary",
    "salary_currency",
    "salary_period",
    "internship",
    "sponsors_visa",
    "workplace",
    "employment_type",
    "keywords",
    "exclude_keywords",
    "exclude_companies",
    "posted_within_days",
    "include_unknown",
    "partial_filters",
}


def _strings(value, name):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a nonempty list")
    result = tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    if not result:
        raise ValueError(f"{name} must contain text values")
    return result


@dataclass(frozen=True)
class SearchProfile:
    name: str
    job_titles: tuple[str, ...]
    search_queries: tuple[str, ...]
    output_directory: Path
    sites: tuple[str, ...]
    location_prompt: str | None
    criteria_options: dict
    max_salary: float | None
    partial_fields: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path):
        path = Path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("search profile must be a JSON object")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("search profile name is required")
        job_titles = _strings(payload.get("job_titles"), "job_titles")
        search_queries = _strings(payload.get("search_queries"), "search_queries")
        raw_output_directory = str(payload.get("output_directory", "")).strip()
        output_directory = Path(raw_output_directory)
        if (not raw_output_directory or output_directory.is_absolute()
                or ".." in output_directory.parts):
            raise ValueError("output_directory must be a relative path inside job_search")
        sites = tuple(payload.get("sites") or SITES)
        if not sites or set(sites) - set(SITES):
            raise ValueError("search profile contains an unsupported site")
        filters = payload.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError("filters must be a JSON object")
        filters = dict(filters)
        if "partial_fields" in filters:
            if "partial_filters" in filters:
                raise ValueError("Use one partial-field filter mapping")
            filters["partial_filters"] = filters.pop("partial_fields")
        unknown = set(filters) - FILTER_KEYS - {"location_prompt", "max_salary"}
        if unknown:
            raise ValueError(f"unsupported profile filters: {', '.join(sorted(unknown))}")
        location_prompt = str(filters.get("location_prompt") or "").strip() or None
        max_salary = filters.get("max_salary")
        criteria_options = {key: value for key, value in filters.items() if key in FILTER_KEYS}
        # Reuse the public criteria validation for profile values.
        partial_fields = payload.get("partial_fields", {})
        SearchCriteria(job_titles[0], sites=sites, partial_fields=partial_fields, **criteria_options)
        if max_salary is not None:
            maximum = float(max_salary)
            minimum = criteria_options.get("min_salary")
            if (not isfinite(maximum) or maximum < 0
                    or minimum is not None and maximum < float(minimum)):
                raise ValueError("max_salary must be nonnegative and at least min_salary")
            max_salary = maximum
        return cls(
            name=name,
            job_titles=job_titles,
            search_queries=search_queries,
            output_directory=output_directory,
            sites=sites,
            location_prompt=location_prompt,
            criteria_options=criteria_options,
            max_salary=max_salary,
            partial_fields=partial_fields,
        )

    def filter_criteria(self):
        return SearchCriteria(
            self.job_titles[0], sites=self.sites, partial_fields=self.partial_fields,
            **self.criteria_options
        )
