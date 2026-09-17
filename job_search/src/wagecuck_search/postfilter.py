"""Filter generated jobs by joining one agent decision per unique location."""

from collections import Counter
from dataclasses import replace
from math import isfinite
from time import perf_counter

from .dedupe import canonical_url
from .export import posting
from .location_agent import classify_locations
from .matching import matches


async def filter_jobs(jobs, criteria, *, location_prompt=None, location_map_path=None,
                      agent=None, max_salary=None):
    started = perf_counter()
    if max_salary is not None and (not isfinite(max_salary) or max_salary < 0):
        raise ValueError("max_salary must be finite and nonnegative")
    if (max_salary is not None and criteria.min_salary is not None
            and max_salary < criteria.min_salary):
        raise ValueError("max_salary must be at least min_salary")
    location_rows = []
    location_lookup = {}
    if location_prompt:
        if location_map_path is None:
            raise ValueError("location_map_path is required with a location prompt")
        location_rows = await classify_locations(jobs, location_prompt, location_map_path, agent)
        location_lookup = {
            (row["raw_location"], row["workplace"]): row for row in location_rows
        }
    options = replace(criteria, locations=())
    accepted, rejected, reasons, seen = [], [], Counter(), {}
    duplicates = 0
    for original in jobs:
        job = posting(original)
        keep, notes = matches(job, options, check_title=False)
        notes = list(notes)
        if location_prompt:
            decision = location_lookup[(job.location.strip(), (job.workplace or "").strip())]
            if str(decision["matches"]).lower() != "true":
                keep = False
                notes.append("location")
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
            continue
        result = dict(original)
        if notes:
            result["note"] = "; ".join(dict.fromkeys(
                value for value in [result.get("note"), *notes] if value
            ))
        key = canonical_url(job.url)
        if key in seen:
            duplicates += 1
            target = seen[key]
            target["sources"] = list(target.get("sources") or [])
            for source in result.get("sources") or []:
                if source not in target["sources"]:
                    target["sources"].append(source)
        else:
            accepted.append(result)
            seen[key] = result
    canonical = {row["canonical_location"].strip().casefold() for row in location_rows
                 if row["canonical_location"].strip()}
    return {"jobs": accepted, "locations": location_rows, "rejections": rejected,
            "summary": {"stage": "filter", "input": len(jobs), "returned": len(accepted),
                        "filtered_out": len(rejected), "deduplicated": duplicates,
                        "filter_reasons": dict(reasons),
                        "location_prompt": location_prompt,
                        "raw_unique_locations": len(location_rows),
                        "unique_locations": len(canonical),
                        "agent_location_calls": 1 if location_rows else 0,
                        "agent_model": getattr(agent, "model", None) if location_rows else None,
                        "elapsed_seconds": round(perf_counter() - started, 4)}}
