"""Filter jobs by joining boolean decisions for each unique partial-field value."""

from collections import Counter
from dataclasses import replace
from math import isfinite
from pathlib import Path
from time import perf_counter

from .export import posting
from .location_agent import classify_locations
from .matching import matches
from .partial_fields import field_values, value_id
from .partial_filter import classify_values


async def filter_jobs(jobs, criteria, *, location_prompt=None, location_map_path=None,
                      agent=None, max_salary=None, progress=None,
                      partial_map_dir=None, partial_agent=None):
    started = perf_counter()
    if progress:
        progress({"phase": "filter_started", "input": len(jobs)})
    if max_salary is not None and (not isfinite(max_salary) or max_salary < 0):
        raise ValueError("max_salary must be finite and nonnegative")
    if (max_salary is not None and criteria.min_salary is not None
            and max_salary < criteria.min_salary):
        raise ValueError("max_salary must be at least min_salary")
    if location_prompt and "location" in criteria.partial_filters:
        raise ValueError("Use either location_prompt or a location partial filter")
    location_rows = []
    location_lookup = {}
    if location_prompt:
        if location_map_path is None:
            raise ValueError("location_map_path is required with a location prompt")
        location_rows = await classify_locations(
            jobs, location_prompt, location_map_path, agent, progress
        )
        location_lookup = {
            (row["raw_location"], row["workplace"]): row for row in location_rows
        }
    partial_rows, partial_lookups = {}, {}
    partial_calls_before = getattr(partial_agent, "request_count", 0)
    for name, prompt in criteria.partial_filters.items():
        path = Path(partial_map_dir) / f"{name}.csv" if partial_map_dir else None
        rows = await classify_values(
            jobs, name, prompt, path=path, agent=partial_agent, progress=progress
        )
        partial_rows[name] = rows
        partial_lookups[name] = {
            row["value_id"]: str(row["matches"]).casefold() == "true" for row in rows
        }
    options = replace(criteria, locations=())
    accepted, rejected, reasons = [], [], Counter()
    total = len(jobs)
    for processed, original in enumerate(jobs, start=1):
        job = posting(original)
        keep, notes = matches(job, options, check_title=False)
        notes = list(notes)
        if location_prompt:
            decisions = [
                location_lookup[(value, (job.workplace or "").strip())]
                for value in field_values(original, "location")
            ]
            if not any(str(decision["matches"]).lower() == "true" for decision in decisions):
                keep = False
                notes.append("location")
        for name, lookup in partial_lookups.items():
            values = field_values(original, name)
            context = (job.workplace or "").strip() if name == "location" else ""
            if not values and criteria.include_unknown:
                notes.append(f"Unverified filter: {name}")
            elif not any(lookup[value_id(name, value, context)] for value in values):
                keep = False
                notes.append(name if values else f"unknown {name}")
        if max_salary is not None:
            salary = job.salary
            lower = salary.minimum if salary and salary.minimum is not None else (
                salary.maximum if salary else None)
            known = salary and salary.currency == criteria.salary_currency and (
                salary.period == criteria.salary_period) and lower is not None
            if known and lower > max_salary:
                keep = False
                notes.append("salary above maximum")
            elif not known and not criteria.include_unknown:
                keep = False
                notes.append("unknown salary")
            elif not known:
                notes.append("Unverified filter: maximum salary")
        if not keep:
            reasons.update(n for n in notes if not n.startswith("Unverified filter:"))
            rejected.append({"url": job.url, "title": job.title, "reason": "; ".join(notes)})
        else:
            # Filtering is pure row selection. Accepted records retain exactly the
            # fields and values produced by the search stage.
            accepted.append(original)
        if progress and (processed % 1000 == 0 or processed == total):
            progress({
                "phase": "filter_row_progress",
                "processed": processed,
                "total": total,
                "kept": len(accepted),
                "removed": len(rejected),
            })
    canonical = {row["canonical_location"].strip().casefold() for row in location_rows
                 if row["canonical_location"].strip()}
    summary = {"stage": "filter", "input": len(jobs), "returned": len(accepted),
                        "filtered_out": len(rejected),
                        "filter_reasons": dict(reasons),
                        "partial_fields": {
                            name: {"unique_values": len(rows), "matching_values": sum(
                                str(row["matches"]).casefold() == "true" for row in rows
                            ), "unclassified_values": sum(
                                row["reason"].startswith("Unclassified:") for row in rows
                            )} for name, rows in partial_rows.items()
                        },
                        "location_prompt": location_prompt,
                        "raw_unique_locations": len(location_rows),
                        "unique_locations": len(canonical),
                        "unclassified_locations": sum(
                            str(row.get("reason", "")).startswith("Unclassified:")
                            for row in location_rows
                        ),
                        "agent_location_calls": (
                            getattr(agent, "request_count", 1) if location_rows else 0
                        ),
                        "agent_model": getattr(agent, "model", None) if location_rows else None,
                        "agent_partial_field_calls": (
                            partial_agent.request_count - partial_calls_before
                            if hasattr(partial_agent, "request_count") else None
                        ) if partial_rows else 0,
                        "partial_field_model": getattr(partial_agent, "model", None),
                        "elapsed_seconds": round(perf_counter() - started, 4)}
    if progress:
        progress({
            "phase": "filter_complete",
            "input": len(jobs),
            "returned": len(accepted),
            "filtered_out": len(rejected),
            "elapsed_seconds": summary["elapsed_seconds"],
        })
    return {"jobs": accepted, "locations": location_rows,
            "partial_field_values": partial_rows, "rejections": rejected,
            "summary": summary}
