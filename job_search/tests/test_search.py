import asyncio
import copy
from datetime import datetime, timezone

import pytest

from wagecuck_search.dedupe import canonical_url, deduplicate
from wagecuck_search.matching import broad_query, matches
from wagecuck_search.models import JobPosting, Salary, SearchCriteria, SiteResult
from wagecuck_search.pipeline import search

NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


def job(**overrides):
    values = dict(
        url="https://linkedin.com/jobs/view/123",
        title="Senior Software Engineer",
        company="Acme",
        location="Remote; Canada",
        source="linkedin",
        workplace="remote",
        internship=False,
        sponsors_visa=True,
        salary=Salary(150000, 210000, "USD", "year"),
        description="Build Python services and distributed systems.",
        employment_type="full_time",
        posted_at="2026-09-15",
    )
    values.update(overrides)
    return JobPosting(**values)


@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Engineer",
        "Staff Software Engineer",
        "Senior to Staff Software Engineer",
        "Sr. Backend Engineer",
    ],
)
def test_broad_query_removes_seniority_and_specialty(title):
    assert broad_query(title) == "software engineer"


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Senior Software Engineer", True),
        ("Staff Software Engineer", True),
        ("Sr. Software Developer", True),
        ("Principal Software Engineer", False),
        ("Junior Software Engineer", False),
        ("Software Engineer Intern", False),
        ("Senior Software Engineering Manager", False),
        ("Senior Sales Engineer", False),
        ("Software Engineer", False),
    ],
)
def test_senior_to_staff_title_filter(title, expected):
    criteria = SearchCriteria("software engineer", seniority=("senior", "staff"))
    assert matches(job(title=title), criteria, NOW)[0] == expected


def test_seniority_in_title_is_inferred_and_specialty_preserved():
    criteria = SearchCriteria("Senior Backend Software Engineer")
    assert matches(job(title="Senior Backend Software Developer"), criteria, NOW)[0]
    assert not matches(job(title="Staff Backend Software Engineer"), criteria, NOW)[0]
    assert not matches(job(title="Senior Frontend Software Engineer"), criteria, NOW)[0]


def test_remote_is_combined_with_country_not_assumed_worldwide():
    criteria = SearchCriteria("software engineer", locations=("remote", "Canada"))
    assert matches(job(), criteria, NOW)[0]
    assert not matches(job(location="Remote; United States"), criteria, NOW)[0]
    assert not matches(job(location="Vancouver, Canada", workplace="hybrid"), criteria, NOW)[0]


def test_short_location_names_match_whole_tokens():
    criteria = SearchCriteria("software engineer", locations=("US",))
    assert not matches(job(location="Austin"), criteria, NOW)[0]
    assert matches(job(location="Austin, TX, US"), criteria, NOW)[0]


@pytest.mark.parametrize(
    "field, value",
    [("internship", True), ("sponsors_visa", False), ("employment_type", "contract")],
)
def test_explicit_mismatches_are_rejected(field, value):
    criteria = SearchCriteria(
        "software engineer",
        internship=False,
        sponsors_visa=True,
        employment_type="full_time",
        include_unknown=True,
    )
    assert not matches(job(**{field: value}), criteria, NOW)[0]


def test_unknown_filter_is_strict_unless_opted_in():
    criteria = SearchCriteria("software engineer", sponsors_visa=True)
    assert not matches(job(sponsors_visa=None), criteria, NOW)[0]
    criteria.include_unknown = True
    accepted, notes = matches(job(sponsors_visa=None), criteria, NOW)
    assert accepted and "Unverified filter: sponsors_visa" in notes


def test_salary_requires_compatible_currency_and_period_and_range_overlap():
    criteria = SearchCriteria("software engineer", min_salary=200000)
    assert matches(job(), criteria, NOW)[0]
    assert not matches(job(salary=Salary(150000, 190000, "USD", "year")), criteria, NOW)[0]
    assert not matches(job(salary=Salary(250000, 300000, "CAD", "year")), criteria, NOW)[0]
    assert not matches(job(salary=Salary(250000, 300000, "USD", "month")), criteria, NOW)[0]
    assert not matches(job(salary=Salary(250000, None, None, "year")), criteria, NOW)[0]


def test_freshness_uses_posted_date_not_updated_date_and_expired_always_rejected():
    criteria = SearchCriteria("software engineer", posted_within_days=7)
    assert matches(job(), criteria, NOW)[0]
    assert not matches(job(posted_at="2026-07-01", last_updated="2026-09-15"), criteria, NOW)[0]
    assert not matches(job(posted_at="garbage"), criteria, NOW)[0]
    assert not matches(job(valid_through="2026-09-10"), criteria, NOW)[0]


def test_keywords_and_company_exclusions():
    criteria = SearchCriteria(
        "software engineer",
        keywords=("Python",),
        exclude_keywords=("clearance",),
        exclude_companies=("Other",),
    )
    assert matches(job(), criteria, NOW)[0]
    assert not matches(job(description="Requires security clearance. Python."), criteria, NOW)[0]
    assert not matches(job(company="OTHER"), criteria, NOW)[0]


@pytest.mark.parametrize(
    "left, right",
    [
        (
            "https://www.linkedin.com/jobs/view/senior-engineer-123/?trk=test",
            "https://linkedin.com/jobs/view/123",
        ),
        ("https://www.indeed.com/rc/clk?jk=abc&from=web", "https://indeed.com/viewjob?jk=abc"),
        (
            "https://wellfound.com/jobs/123-senior-software-engineer?ref=web",
            "https://wellfound.com/jobs/123",
        ),
        ("https://simplify.jobs/p/abc-def/Senior-Engineer", "https://simplify.jobs/p/abc-def"),
    ],
)
def test_tracking_and_slug_variants_are_same_url(left, right):
    assert canonical_url(left) == canonical_url(right)


def test_cross_site_merge_before_filter_enriches_unknown_sponsorship():
    left = job(sponsors_visa=None)
    right = job(
        source="wellfound",
        url="https://wellfound.com/jobs/22-staff",
        company="Acme Inc.",
        title="Sr. Software Engineer",
    )
    unique, removed = deduplicate([left, right])
    assert len(unique) == 1 and removed == {"wellfound": 1}
    assert unique[0].sponsors_visa is True
    assert len(unique[0].sources) == 2
    assert left.sponsors_visa is None  # Inputs are not mutated.


def test_different_level_location_team_and_unknown_identity_are_not_merged():
    values = [
        job(),
        job(url="https://linkedin.com/jobs/view/2", title="Staff Software Engineer"),
        job(url="https://linkedin.com/jobs/view/3", location="Remote; US"),
        job(url="https://linkedin.com/jobs/view/4", title="Senior Software Engineer, Data"),
        job(url="https://linkedin.com/jobs/view/5", location="Unknown"),
        job(url="https://linkedin.com/jobs/view/6", location="Unknown"),
    ]
    assert len(deduplicate(values)[0]) == len(values)


def test_late_duplicate_can_link_two_groups_and_conflicts_stay_unknown():
    first = job(sponsors_visa=True)
    second = job(
        url="https://wellfound.com/jobs/22",
        source="wellfound",
        company="Other",
        sponsors_visa=False,
    )
    bridge = job(url=second.url, source="wellfound", sponsors_visa=True)
    unique, removed = deduplicate([first, second, bridge])
    assert len(unique) == 1 and sum(removed.values()) == 2
    assert unique[0].sponsors_visa is None
    assert "Conflicting sponsors_visa" in unique[0].note


def test_duplicate_metadata_keeps_dates_and_enriches_compatible_salary():
    first = job(salary=Salary(150000, 210000, None, "year"), posted_at="2026-09-12")
    second = job(
        source="wellfound",
        url="https://wellfound.com/jobs/22",
        salary=Salary(150000, 210000, "USD", "year", "$150k - $210k"),
        posted_at="2026-09-10",
        last_updated="2026-09-15",
    )
    merged = deduplicate([first, second])[0][0]
    assert merged.salary.currency == "USD"
    assert merged.posted_at == "2026-09-10"
    assert merged.last_updated == "2026-09-15"


def test_distinct_explicit_levels_with_generic_titles_are_not_merged():
    first = job(title="Software Engineer", experience_levels=("senior",))
    second = job(
        title="Software Engineer",
        experience_levels=("mid",),
        url="https://simplify.jobs/p/different",
        source="simplify",
    )
    assert len(deduplicate([first, second])[0]) == 2


def test_contradictory_remote_location_and_workplace_are_rejected():
    with pytest.raises(ValueError, match="conflicts"):
        SearchCriteria("software engineer", locations=("Remote",), workplace="onsite")


class FakeProvider:
    def __init__(self, site, jobs=(), error=False):
        self.site, self.jobs, self.error = site, list(jobs), error
        self.queries = []

    async def fetch(self, query, criteria):
        self.queries.append(query)
        if self.error:
            raise RuntimeError("Site unavailable")
        return SiteResult(self.site, copy.deepcopy(self.jobs), discovered=len(self.jobs), pages=1)


class FakeValidator:
    async def validate(self, posting):
        from wagecuck_search.validation import ValidationResult

        return ValidationResult(
            "https://jobs.lever.co/acme/" + posting.title.replace(" ", "-"), "ats"
        )


def test_four_site_run_dedupes_before_filter_and_reports_every_stage():
    criteria = SearchCriteria("Senior to Staff Software Engineer", sponsors_visa=True)
    providers = [
        FakeProvider(
            "wellfound",
            [job(source="wellfound", url="https://wellfound.com/jobs/1", sponsors_visa=None)],
        ),
        FakeProvider("indeed", [job(source="indeed", url="https://indeed.com/viewjob?jk=2")]),
        FakeProvider("linkedin", [job(title="Junior Software Engineer")]),
        FakeProvider(
            "simplify",
            [
                job(
                    source="simplify",
                    url="https://simplify.jobs/p/4",
                    title="Staff Software Engineer",
                )
            ],
        ),
    ]
    report = asyncio.run(search(criteria, providers, FakeValidator()))
    assert all(p.queries == ["software engineer"] for p in providers)
    assert len(report["jobs"]) == 2
    summary = report["summary"]
    assert summary["fetched"] == 4
    assert summary["deduplicated"] == 1
    assert summary["filtered_out"] == 1
    assert summary["returned"] == 2
    assert len(summary["sites"]) == 4
    assert sum(v["returned"] for v in summary["sites"].values()) == 2
    assert summary["sites"]["indeed"]["deduplicated"] == 1
    assert summary["sites"]["indeed"]["matched_with_duplicates"] == 1
    for posting in report["jobs"]:
        assert all(posting[field] for field in ("url", "title", "company", "location"))
        assert "description" not in posting


def test_one_failed_site_does_not_lose_other_sites_and_missing_provider_is_visible():
    providers = [FakeProvider("linkedin", [job()]), FakeProvider("indeed", error=True)]
    report = asyncio.run(search(SearchCriteria("software engineer"), providers, FakeValidator()))
    assert len(report["jobs"]) == 1
    assert report["summary"]["partial"]
    assert report["summary"]["sites"]["indeed"]["status"] == "error"
    assert report["summary"]["sites"]["wellfound"]["status"] == "error"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"job_title": "  "},
        {"job_title": "!!!"},
        {"min_salary": -1},
        {"min_salary": float("nan")},
        {"max_pages": 0},
        {"max_per_site": 0},
        {"sites": ()},
        {"timeout_seconds": float("inf")},
        {"posted_within_days": 0},
    ],
)
def test_invalid_criteria(kwargs):
    with pytest.raises(ValueError):
        SearchCriteria(**({"job_title": "software engineer"} | kwargs))
