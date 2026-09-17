from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

SITES = (
    "wellfound",
    "indeed",
    "linkedin",
    "simplify",
    "hiringcafe",
    "jobright",
    "levels",
    "trueup",
    "yc",
    "builtin",
)
LEVELS = ("intern", "junior", "mid", "senior", "staff", "principal", "lead", "manager")


@dataclass
class Salary:
    minimum: float | None = None
    maximum: float | None = None
    currency: str | None = None
    period: str | None = None
    text: str | None = None


@dataclass
class JobPosting:
    url: str
    title: str
    company: str
    location: str
    source: str
    salary: Salary | None = None
    last_updated: str | None = None
    internship: bool | None = None
    note: str | None = None
    sponsors_visa: bool | None = None
    description: str = ""
    posted_at: str | None = None
    valid_through: str | None = None
    employment_type: str | None = None
    workplace: str | None = None
    experience_levels: tuple[str, ...] = ()
    sources: list[dict[str, str]] = field(default_factory=list)
    application_urls: list[str] = field(default_factory=list)
    employer_urls: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.sources:
            self.sources = [{"site": self.source, "url": self.url}]

    def output(self) -> dict:
        result = asdict(self)
        for key in ("description", "valid_through", "source", "application_urls", "employer_urls"):
            result.pop(key)
        if not self.experience_levels:
            result.pop("experience_levels")
        return {key: value for key, value in result.items() if value is not None}


@dataclass
class SearchCriteria:
    job_title: str
    locations: tuple[str, ...] = ()
    seniority: tuple[str, ...] = ()
    min_salary: float | None = None
    salary_currency: str = "USD"
    salary_period: str = "year"
    internship: bool | None = None
    sponsors_visa: bool | None = None
    workplace: str | None = None
    employment_type: str | None = None
    keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    exclude_companies: tuple[str, ...] = ()
    posted_within_days: int | None = None
    include_unknown: bool = False
    sites: tuple[str, ...] = SITES
    max_pages: int = 200
    max_per_site: int = 10000
    timeout_seconds: float = 30
    site_timeout_seconds: float = 900
    show_browser: bool = False
    browser_channel: str | None = None
    validation_timeout_seconds: float = 60
    validation_workers: int = 8
    detail_workers: int = 4

    def __post_init__(self):
        self.job_title = self.job_title.strip()
        if not self.job_title or not any(c.isalnum() for c in self.job_title):
            raise ValueError("job_title must contain a job title")
        for key in ("locations", "seniority", "keywords", "exclude_keywords", "exclude_companies"):
            values = tuple(dict.fromkeys(v.strip() for v in getattr(self, key) if v.strip()))
            setattr(self, key, values)
        self.sites = tuple(dict.fromkeys(self.sites))
        if not self.sites or set(self.sites) - set(SITES):
            raise ValueError(f"sites must be selected from {', '.join(SITES)}")
        if set(self.seniority) - set(LEVELS):
            raise ValueError(f"seniority must be selected from {', '.join(LEVELS)}")
        if self.workplace not in (None, "remote", "hybrid", "onsite"):
            raise ValueError("workplace must be remote, hybrid, or onsite")
        if any(v.casefold() == "remote" for v in self.locations) and self.workplace in (
            "hybrid",
            "onsite",
        ):
            raise ValueError("location remote conflicts with the requested workplace")
        if self.employment_type not in (None, "full_time", "part_time", "contract", "temporary"):
            raise ValueError("unsupported employment_type")
        if self.salary_period not in ("year", "month", "week", "day", "hour"):
            raise ValueError("unsupported salary_period")
        self.salary_currency = self.salary_currency.upper()
        if len(self.salary_currency) != 3 or not self.salary_currency.isalpha():
            raise ValueError("salary_currency must be a three-letter currency code")
        if self.min_salary is not None and (
            not math.isfinite(self.min_salary) or self.min_salary < 0
        ):
            raise ValueError("min_salary must be a finite nonnegative number")
        if self.posted_within_days is not None and self.posted_within_days < 1:
            raise ValueError("posted_within_days must be positive")
        if not 1 <= self.max_pages <= 10000 or not 1 <= self.max_per_site <= 1000000:
            raise ValueError("max_pages must be 1..10000 and max_per_site must be 1..1000000")
        if not 1 <= self.validation_workers <= 64 or not 1 <= self.detail_workers <= 8:
            raise ValueError("validation_workers must be 1..64 and detail_workers must be 1..8")
        for value in (
            self.timeout_seconds,
            self.site_timeout_seconds,
            self.validation_timeout_seconds,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("timeouts must be finite and positive")


@dataclass
class SiteResult:
    site: str
    jobs: list[JobPosting] = field(default_factory=list)
    discovered: int = 0
    pages: int = 0
    status: str = "ok"
    errors: list[str] = field(default_factory=list)
    limited: bool = False
    unavailable: int = 0
    advertised_total: int | None = None
    detail_visits: int = 0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
