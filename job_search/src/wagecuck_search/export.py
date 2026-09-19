"""Lossless job CSVs, also compatible with the original saved job-list CSV."""

import csv
import json
import math
from dataclasses import asdict, fields
from pathlib import Path

from .array_fields import all_fields, comma_separated, items, normalize_fields, validate_name
from .job_fields import job_field_types, normalize_field_types, normalize_scalars, scalar_from_csv
from .models import JobPosting, Salary

FIELDS = [
    "url", "title", "company", "location", "programming_languages", "frameworks",
    "salary_minimum", "salary_maximum",
    "salary_currency", "salary_period", "salary_text", "last_updated", "posted_at",
    "internship", "sponsors_visa", "workplace", "employment_type", "experience_levels",
    "note", "url_validated_at", "application_url_type", "source_sites", "source_urls",
    "description", "valid_through", "source", "sources_json", "application_urls_json",
    "employer_urls_json", "array_fields_json", "fields_json", "field_types_json", "enrichment_json",
]


def csv_row(job):
    result = {key: job.get(key, "") for key in FIELDS}
    arrays = all_fields(job)
    scalars = normalize_scalars(job.get("fields") or {})
    if set(arrays) & set(scalars):
        raise ValueError("A CSV column cannot be both scalar and array")
    result.update(scalars)
    result["fields_json"] = json.dumps(scalars, ensure_ascii=False)
    result["field_types_json"] = json.dumps(job_field_types(job), ensure_ascii=False)
    result["enrichment_json"] = json.dumps(job.get("enrichment") or {}, ensure_ascii=False)
    result.update({name: comma_separated(values) for name, values in arrays.items()
                   if name != "location"})
    result["array_fields_json"] = json.dumps(arrays, ensure_ascii=False)
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


def write_jobs(path, jobs, *, extra_columns=()):
    jobs = list(jobs)
    extra = sorted({name for job in jobs for name in
                    (*all_fields(job), *(job.get("fields") or {}))} - set(FIELDS))
    extra = sorted(set(extra) | (set(extra_columns) - set(FIELDS)))
    write_table(path, [*FIELDS, *extra], (csv_row(j) for j in jobs))


def write_filtered_job_rows(source, destination, accepted_indices):
    """Copy accepted CSV rows byte-for-field without changing the input schema."""
    csv.field_size_limit(16 * 1024 * 1024)
    remaining = set(accepted_indices)
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with source.open(encoding="utf-8-sig", newline="") as input_stream:
        reader = csv.DictReader(input_stream)
        columns = reader.fieldnames or []
        if "url" not in columns:
            raise ValueError("Job CSV requires a url column")
        with temporary.open("w", encoding="utf-8-sig", newline="") as output_stream:
            writer = csv.DictWriter(output_stream, fieldnames=columns)
            writer.writeheader()
            for index, row in enumerate(reader):
                if index in remaining:
                    writer.writerow(row)
                    remaining.remove(index)
    missing = len(remaining)
    if missing:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"Filtered jobs contain {missing} rows absent from the source CSV")
    temporary.replace(destination)


def read_jobs(path, *, array_columns=(), field_types=None):
    # Large descriptions may exceed the csv module's default 128 KiB field limit.
    csv.field_size_limit(16 * 1024 * 1024)
    overrides = normalize_field_types(field_types or {})
    for name in array_columns:
        validate_name(name)
        if name in overrides and overrides[name] != "array_field":
            raise ValueError(f"{name}: --array-column conflicts with its declared type")
        overrides[name] = "array_field"
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"url", "title", "company"} <= set(reader.fieldnames or []):
            raise ValueError("Job CSV requires url, title, and company columns")
        jobs = []
        for number, row in enumerate(reader, 2):
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("wrong number of CSV columns")
                if not all(row[k].strip() for k in ("url", "title", "company")):
                    raise ValueError("empty required job field")
                job = {k: v for k, v in row.items() if v and k in
                       {f.name for f in fields(JobPosting)} | {
                           "url_validated_at", "application_url_type"}}
                job.setdefault("location", "Unknown")
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
                arrays = normalize_fields(json.loads(row.get("array_fields_json") or row.get("partial_fields_json") or "{}"))
                types = normalize_field_types(json.loads(row.get("field_types_json") or "{}"))
                for name, kind in overrides.items():
                    if name in types and types[name] != kind:
                        raise ValueError(f"{name}: supplied type conflicts with saved field type")
                    types[name] = kind
                scalars = normalize_scalars(json.loads(row.get("fields_json") or "{}"))
                # Visible CSV columns are editable; the JSON carries atomic locations
                # and distinguishes a comma within one item from an item separator.
                names = (set(arrays) | ({"programming_languages", "languages", "frameworks"}
                         - set(scalars) - {name for name, kind in types.items() if kind != "array_field"})
                         | {name for name, kind in types.items() if kind == "array_field"})
                for name in names:
                    try:
                        validate_name(name)
                    except ValueError:
                        continue
                    if name != "location" and name in row:
                        arrays[name] = items(row[name])
                scalar_names = (set(row) - set(FIELDS) - {"partial_fields_json"} - names) | set(scalars)
                for name in scalar_names:
                    validate_name(name)
                    if name in row:
                        scalars[name] = scalar_from_csv(row[name], scalars.get(name), types.get(name))
                if set(arrays) & set(scalars):
                    raise ValueError("A field cannot be both scalar and array")
                job["fields"] = scalars
                job["field_types"] = types
                job["enrichment"] = json.loads(row.get("enrichment_json") or "{}")
                if not isinstance(job["enrichment"], dict):
                    raise ValueError("enrichment_json must be an object")
                if arrays:
                    job["array_fields"] = arrays
                job_field_types(job)
                jobs.append(job)
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid job CSV row {number}: {exc}") from exc
    # A blank cell must not turn an otherwise numeric column into string_field
    # when validation or field filling re-exports the CSV.
    observed = {}
    for job in jobs:
        types = job_field_types(job)
        for name, value in job["fields"].items():
            if value is not None:
                observed.setdefault(name, set()).add(types[name])
    for job in jobs:
        for name, kinds in observed.items():
            if name in job["fields"] and job["fields"][name] is None and len(kinds) == 1:
                job["field_types"].setdefault(name, next(iter(kinds)))
    return jobs


def posting(job):
    values = {f.name: job[f.name] for f in fields(JobPosting) if f.name in job}
    if values.get("salary"):
        values["salary"] = Salary(**values["salary"])
    values.setdefault("source", next(iter(job.get("sources") or []), {}).get("site", "csv"))
    return JobPosting(**values)


def full_job(job):
    return asdict(job)
