from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import JobPosting, SearchCriteria, utc_now

LEVEL_PATTERNS = {
    "intern": r"\b(?:intern|internship|co[ -]?op)\b",
    "junior": r"\b(?:junior|jr\.?|entry[ -]level|new grad(?:uate)?)\b",
    "mid": r"\b(?:mid[ -]?(?:level)?|intermediate)\b",
    "senior": r"\b(?:senior|sr\.?)\b",
    "staff": r"\bstaff\b",
    "principal": r"\bprincipal\b",
    "lead": r"\b(?:lead|tech lead)\b",
    "manager": r"\b(?:manager|director|head|vp|vice president)\b",
}


def normalized(text: str) -> str:
    text = text.casefold().replace("&", " and ")
    text = re.sub(r"\bsr\b\.?", "senior", text)
    text = re.sub(r"\bjr\b\.?", "junior", text)
    text = re.sub(r"\bfull[ -]?stack\b", "fullstack", text)
    text = re.sub(r"\bback[ -]?end\b", "backend", text)
    text = re.sub(r"\bfront[ -]?end\b", "frontend", text)
    return " ".join(re.findall(r"\w+", text))


def levels(title: str) -> set[str]:
    return {level for level, pattern in LEVEL_PATTERNS.items() if re.search(pattern, title, re.I)}


def title_terms(title: str) -> set[str]:
    for pattern in LEVEL_PATTERNS.values():
        title = re.sub(pattern, " ", title, flags=re.I)
    title = re.sub(r"\b(?:to|level|and|or)\b", " ", title, flags=re.I)
    return set(normalized(title).split())


def broad_query(title: str) -> str:
    terms = title_terms(title)
    if "software" in terms and terms & {"engineer", "engineering", "developer", "development"}:
        return "software engineer"
    if "engineer" in terms and terms & {"backend", "frontend", "fullstack"}:
        return "software engineer"
    stripped = title
    for pattern in LEVEL_PATTERNS.values():
        stripped = re.sub(pattern, " ", stripped, flags=re.I)
    stripped = re.sub(r"\b(?:to|level|and|or)\b", " ", stripped, flags=re.I)
    return normalized(stripped) or normalized(title)


def title_matches(requested: str, actual: str) -> bool:
    wanted, found = title_terms(requested), title_terms(actual)
    # Treat these as spelling/title variants, without broadening the user's specialty.
    aliases = {"developer": "engineer", "engineering": "engineer", "development": "engineer"}
    wanted = {aliases.get(t, t) for t in wanted}
    found = {aliases.get(t, t) for t in found}
    return wanted <= found


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def matches(
    job: JobPosting, criteria: SearchCriteria, now: datetime | None = None
) -> tuple[bool, list[str]]:
    now = now or utc_now()
    rejected, unknown = [], []

    def check(name: str, value: bool | None):
        if value is None:
            unknown.append(name)
        elif not value:
            rejected.append(name)

    check("title", title_matches(criteria.job_title, job.title))
    requested_levels = set(criteria.seniority) or levels(criteria.job_title)
    if requested_levels:
        actual = levels(job.title) or set(job.experience_levels)
        check("seniority", bool(actual & requested_levels) if actual else None)
        # A Senior Engineering Manager is not a senior individual contributor.
        if "manager" in actual and "manager" not in requested_levels:
            rejected.append("management role")
    location_terms = [v for v in criteria.locations if normalized(v) != "remote"]
    remote_requested = any(normalized(v) == "remote" for v in criteria.locations)
    if location_terms:
        check(
            "location",
            any(f" {normalized(v)} " in f" {normalized(job.location)} " for v in location_terms)
            if job.location != "Unknown"
            else None,
        )
    workplace = criteria.workplace or ("remote" if remote_requested else None)
    if workplace:
        check("workplace", job.workplace == workplace if job.workplace else None)
    for name in ("internship", "sponsors_visa", "employment_type"):
        expected, actual = getattr(criteria, name), getattr(job, name)
        if expected is not None:
            check(name, actual == expected if actual is not None else None)
    if criteria.min_salary is not None:
        salary = job.salary
        if (
            salary is None
            or salary.currency != criteria.salary_currency
            or salary.period != criteria.salary_period
        ):
            check("salary", None)
        else:
            upper = salary.maximum if salary.maximum is not None else salary.minimum
            check("salary", upper >= criteria.min_salary if upper is not None else None)
    if criteria.posted_within_days:
        posted = parse_date(job.posted_at)
        check(
            "posted_at",
            now - timedelta(days=criteria.posted_within_days) <= posted <= now if posted else None,
        )
    expires = parse_date(job.valid_through)
    if expires and expires < now:
        rejected.append("expired")
    searchable = f"{job.title} {job.description}".casefold()
    for keyword in criteria.keywords:
        check(
            f"keyword: {keyword}",
            keyword.casefold() in searchable
            if job.description or keyword.casefold() in job.title.casefold()
            else None,
        )
    for keyword in criteria.exclude_keywords:
        if keyword.casefold() in searchable:
            rejected.append(f"excluded keyword: {keyword}")
    if normalized(job.company) in {normalized(v) for v in criteria.exclude_companies}:
        rejected.append("excluded company")
    if unknown and not criteria.include_unknown:
        rejected.extend(f"unknown {name}" for name in unknown)
    return not rejected, rejected or [f"Unverified filter: {name}" for name in unknown]
