"""Lossless stage CSVs, also compatible with the original saved job-list CSV."""

import csv
import json
import math
from dataclasses import asdict, fields
from pathlib import Path

from .models import JobPosting, Salary

FIELDS = [
    "url", "title", "company", "location", "salary_minimum", "salary_maximum",
    "salary_currency", "salary_period", "salary_text", "last_updated", "posted_at",
    "internship", "sponsors_visa", "workplace", "employment_type", "experience_levels",
    "note", "url_validated_at", "application_url_type", "source_sites", "source_urls",
    "description", "valid_through", "source", "sources_json", "application_urls_json",
    "employer_urls_json",
]


def csv_row(job):
    result = {key: job.get(key, "") for key in FIELDS}
    for key in ("minimum", "maximum", "currency", "period", "text"):
        result["salary_" + key] = (job.get("salary") or {}).get(key, "")
    result["experience_levels"] = " | ".join(job.get("experience_levels") or [])
    sources = job.get("sources") or []
    result["source_sites"] = " | ".join(dict.fromkeys(s["site"] for s in sources))
    result["source_urls"] = " | ".join(s["url"] for s in sources)
    for key in ("sources", "application_urls", "employer_urls"):
        result[key + "_json"] = json.dumps(job.get(key) or [], ensure_ascii=False)
    return {k: "" if v is None else str(v).lower() if isinstance(v, bool) else v
            for k, v in result.items()}


def write_table(path, columns, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def write_jobs(path, jobs):
    write_table(path, FIELDS, (csv_row(j) for j in jobs))


def read_jobs(path):
    # Large descriptions may exceed the csv module's default 128 KiB field limit.
    csv.field_size_limit(16 * 1024 * 1024)
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"url", "title", "company", "location"} <= set(reader.fieldnames or []):
            raise ValueError("Job CSV requires url, title, company, and location columns")
        jobs = []
        for number, row in enumerate(reader, 2):
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("wrong number of CSV columns")
                if not all(row[k].strip() for k in ("url", "title", "company", "location")):
                    raise ValueError("empty required job field")
                job = {k: v for k, v in row.items() if v and k in
                       {f.name for f in fields(JobPosting)} | {
                           "url_validated_at", "application_url_type"}}
                for key in ("internship", "sponsors_visa"):
                    if row.get(key):
                        if row[key].lower() not in ("true", "false"):
                            raise ValueError(f"{key} must be true, false, or blank")
                        job[key] = row[key].lower() == "true"
                salary = {k: row.get("salary_" + k) for k in
                          ("minimum", "maximum", "currency", "period", "text")}
                for key in ("minimum", "maximum"):
                    salary[key] = float(salary[key]) if salary[key] else None
                    if salary[key] is not None and (
                        not math.isfinite(salary[key]) or salary[key] < 0
                    ):
                        raise ValueError("salary must be finite and nonnegative")
                if any(salary.values()):
                    job["salary"] = salary
                job["experience_levels"] = [v.strip() for v in
                                            row.get("experience_levels", "").split("|") if v.strip()]
                for key in ("sources", "application_urls", "employer_urls"):
                    if row.get(key + "_json"):
                        value = json.loads(row[key + "_json"])
                        valid = isinstance(value, list)
                        if key == "sources":
                            valid = valid and all(
                                isinstance(v, dict) and {"site", "url"} <= v.keys()
                                and all(isinstance(v[k], str) for k in ("site", "url"))
                                for v in value
                            )
                        else:
                            valid = valid and all(isinstance(v, str) for v in value)
                        if not valid:
                            raise ValueError(f"invalid {key}_json")
                        job[key] = value
                if "sources" not in job:
                    sites = [v.strip() for v in row.get("source_sites", "").split("|") if v.strip()]
                    urls = [v.strip() for v in row.get("source_urls", "").split("|") if v.strip()]
                    job["sources"] = [{"site": sites[min(i, len(sites) - 1)], "url": url}
                                      for i, url in enumerate(urls)] if sites else []
                jobs.append(job)
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid job CSV row {number}: {exc}") from exc
    return jobs


def posting(job):
    values = {f.name: job[f.name] for f in fields(JobPosting) if f.name in job}
    if values.get("salary"):
        values["salary"] = Salary(**values["salary"])
    values.setdefault("source", next(iter(job.get("sources") or []), {}).get("site", "csv"))
    return JobPosting(**values)


def full_job(job):
    return asdict(job)
