from __future__ import annotations

import copy
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .matching import levels, normalized, parse_date
from .models import JobPosting


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    query = parse_qs(parts.query)
    if host.endswith("indeed.com"):
        job_id = (query.get("jk") or query.get("vjk") or [None])[0]
        if job_id:
            return f"https://{host}/viewjob?{urlencode({'jk': job_id})}"
    if host.endswith("linkedin.com"):
        match = re.search(r"/jobs/view/(?:.*-)?(\d+)(?:/|$)", parts.path)
        if match:
            return f"https://linkedin.com/jobs/view/{match[1]}"
    if host == "wellfound.com":
        match = re.search(r"/jobs/(\d+)", parts.path)
        if match:
            return f"https://wellfound.com/jobs/{match[1]}"
    if host == "simplify.jobs":
        match = re.search(r"/p/([^/]+)", parts.path)
        if match:
            return f"https://simplify.jobs/p/{match[1]}"
    if host in {"hiring.cafe", "hiringcafe.com"}:
        match = re.search(r"/job/([^/]+)", parts.path)
        if match:
            return f"https://hiringcafe.com/job/{match[1]}"
    if host == "jobright.ai":
        match = re.search(r"/jobs/info/([a-fA-F0-9]+)", parts.path)
        if match:
            return f"https://jobright.ai/jobs/info/{match[1]}"
    if host == "levels.fyi" and parts.path.startswith("/jobs"):
        job_id = (query.get("jobId") or query.get("jobid") or [None])[0]
        if job_id:
            return f"https://levels.fyi/jobs?{urlencode({'jobId': job_id})}"
    if host == "ycombinator.com":
        match = re.search(r"(/companies/[^/]+/jobs/[^/]+)", parts.path)
        if match:
            return "https://ycombinator.com" + match[1]
    if host == "builtin.com":
        match = re.search(r"(/job/[^/]+/\d+)", parts.path)
        if match:
            return "https://builtin.com" + match[1]
    retained = {
        k: v
        for k, v in query.items()
        if not k.lower().startswith("utm_")
        and k.lower() not in {"trk", "trackingid", "ref", "refid", "source", "from", "fbclid"}
    }
    return urlunsplit(
        (parts.scheme.lower(), host, parts.path.rstrip("/"), urlencode(retained, doseq=True), "")
    )


def identity(job: JobPosting) -> tuple | None:
    if any(
        not v.strip() or v.casefold() == "unknown" for v in (job.title, job.company, job.location)
    ):
        return None
    company = re.sub(
        r"\b(?:incorporated|inc|llc|ltd|limited|corp|corporation)\b", "", normalized(job.company)
    ).strip()
    seniority = tuple(sorted(levels(job.title) or job.experience_levels))
    return company, normalized(job.title), normalized(job.location), seniority


def deduplicate(jobs: list[JobPosting]) -> tuple[list[JobPosting], dict[str, int]]:
    """Union URL/identity matches, including a late record linking two earlier groups."""
    parents = list(range(len(jobs)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen = {}
    for index, job in enumerate(jobs):
        keys = [("url", canonical_url(s["url"])) for s in job.sources]
        keys.append(("url", canonical_url(job.url)))
        if identity(job):
            keys.append(("identity", identity(job)))
        for key in keys:
            if key in seen:
                a, b = root(index), root(seen[key])
                parents[max(a, b)] = min(a, b)
            else:
                seen[key] = index
    groups = {}
    removed = {}
    conflicts: dict[int, set[str]] = {}
    for index, original in enumerate(jobs):
        group = root(index)
        if group not in groups:
            groups[group] = copy.deepcopy(original)
            conflicts[group] = set()
            continue
        removed[original.source] = removed.get(original.source, 0) + 1
        target = groups[group]
        for key in ("application_urls", "employer_urls"):
            setattr(target, key, list(dict.fromkeys(getattr(target, key) + getattr(original, key))))
        for source in original.sources:
            if source not in target.sources:
                target.sources.append(copy.deepcopy(source))
        for key in (
            "salary",
            "last_updated",
            "posted_at",
            "valid_through",
            "internship",
            "sponsors_visa",
            "employment_type",
            "workplace",
        ):
            old, new = getattr(target, key), getattr(original, key)
            if key in conflicts[group]:
                continue
            if key in ("posted_at", "last_updated") and old and new:
                dates = [(parse_date(v), v) for v in (old, new)]
                if all(d[0] is not None for d in dates):
                    chosen = min(dates) if key == "posted_at" else max(dates)
                    setattr(target, key, chosen[1])
                    continue
            if key == "salary" and old and new:
                attributes = ("minimum", "maximum", "currency", "period")
                if all(
                    getattr(old, a) is None
                    or getattr(new, a) is None
                    or getattr(old, a) == getattr(new, a)
                    for a in attributes
                ):
                    for attribute in (*attributes, "text"):
                        if getattr(old, attribute) is None:
                            setattr(old, attribute, getattr(new, attribute))
                    continue
            if old is None:
                setattr(target, key, copy.deepcopy(new))
            elif new is not None and old != new:
                # Conflicting assertions are not reliable evidence for a requested filter.
                setattr(target, key, None)
                conflicts[group].add(key)
                target.note = "; ".join(
                    filter(None, [target.note, f"Conflicting {key} across sites"])
                )
        for key in ("company", "location"):
            if getattr(target, key) == "Unknown" and getattr(original, key) != "Unknown":
                setattr(target, key, getattr(original, key))
        if len(original.description) > len(target.description):
            target.description = original.description
        if not target.experience_levels:
            target.experience_levels = original.experience_levels
        if original.note and original.note not in (target.note or ""):
            target.note = "; ".join(filter(None, [target.note, original.note]))
    return list(groups.values()), removed
