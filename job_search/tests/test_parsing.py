import json

import pytest

from wagecuck_search.parsing import (
    job_links,
    parse_posting,
    salary_from_schema,
    salary_from_text,
    sponsorship,
    workplace,
)
from wagecuck_search.providers import access_problem

URLS = {
    "wellfound": "https://wellfound.com/jobs/123-senior-software-engineer",
    "indeed": "https://www.indeed.com/viewjob?jk=abc",
    "linkedin": "https://www.linkedin.com/jobs/view/senior-software-engineer-123",
    "simplify": "https://simplify.jobs/p/abcd-efgh/Senior-Software-Engineer",
}


def schema(**changes):
    value = {
        "@type": "JobPosting",
        "title": "Staff Software Engineer",
        "hiringOrganization": {"name": "Acme"},
        "jobLocation": [
            {
                "address": {
                    "addressLocality": "Vancouver",
                    "addressRegion": "BC",
                    "addressCountry": {"name": "Canada"},
                }
            }
        ],
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "Canada"},
        "baseSalary": {
            "currency": "CAD",
            "value": {"minValue": 180000, "maxValue": 240000, "unitText": "YEAR"},
        },
        "description": "<p>Python services. Visa sponsorship available.</p>",
        "employmentType": "FULL_TIME",
        "datePosted": "2026-09-10",
        "dateModified": "2026-09-12",
    }
    value.update(changes)
    return '<script type="application/ld+json">' + json.dumps({"@graph": [value]}) + "</script>"


@pytest.mark.parametrize("site", URLS)
def test_all_boards_parse_jobposting_schema(site):
    posting = parse_posting(site, schema(url=URLS[site]), URLS[site])
    assert posting.title == "Staff Software Engineer"
    assert posting.company == "Acme"
    assert posting.location == "Remote; Canada"
    assert posting.workplace == "remote"
    assert posting.salary.minimum == 180000
    assert posting.salary.currency == "CAD"
    assert posting.salary.period == "year"
    assert posting.sponsors_visa is True
    assert posting.internship is False
    assert posting.posted_at == "2026-09-10"
    assert posting.last_updated == "2026-09-12"


@pytest.mark.parametrize("site", URLS)
def test_listing_links_ignore_other_hosts_and_navigation_and_tracking_duplicates(site):
    url = URLS[site]
    separator = "&" if "?" in url else "?"
    html = (
        f'<a href="{url}">Senior Engineer</a>'
        f'<a href="{url}{separator}utm_source=search">Apply</a>'
        '<a href="https://example.org/jobs/123">unrelated</a>'
        '<a href="/jobs">Browse</a>'
    )
    assert job_links(site, html, url) == [url]


@pytest.mark.parametrize(
    "site, company, location",
    [
        (
            "linkedin",
            '<a class="topcard__org-name-link">Acme</a>',
            '<span class="topcard__flavor--bullet">Toronto, Canada</span>',
        ),
        (
            "indeed",
            '<div data-testid="inlineHeader-companyName">Acme</div>',
            '<div data-testid="job-location">Toronto, Canada</div>',
        ),
        (
            "wellfound",
            '<a href="/company/acme">Acme</a>',
            '<div itemprop="jobLocation">Toronto, Canada</div>',
        ),
        (
            "simplify",
            '<a href="/c/acme">Acme</a>',
            '<div itemprop="jobLocation">Toronto, Canada</div>',
        ),
    ],
)
def test_board_specific_dom_fallback(site, company, location):
    html = "<h1>Senior Software Engineer</h1>" + company + location
    posting = parse_posting(site, html, URLS[site])
    assert posting.company == "Acme" and posting.location == "Toronto, Canada"
    assert posting.last_updated is None
    assert posting.sponsors_visa is None
    assert posting.internship is None


def test_recommendation_schema_is_not_the_requested_job():
    html = schema(url=URLS["wellfound"]) + "<h1>Please log in</h1>"
    assert parse_posting("linkedin", html, URLS["linkedin"]) is None


def test_internship_title_overrides_full_time():
    posting = parse_posting("linkedin", schema(title="Software Engineer Intern"), URLS["linkedin"])
    assert posting.internship is True


@pytest.mark.parametrize(
    "description, expected",
    [
        ("Visa Sponsorship Not Available", False),
        ("We cannot provide visa sponsorship.", False),
        ("Visa sponsorship is not available for this role.", False),
        ("Must work without sponsorship.", False),
        ("We provide visa sponsorship.", True),
        ("Visa sponsorship available", True),
        ("Visa sponsorship may be considered.", None),
        ("Are you seeking visa sponsorship?", None),
        ("Work with international teams.", None),
    ],
)
def test_sponsorship_requires_an_explicit_assertion(description, expected):
    assert sponsorship(description) is expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Hybrid (remote two days)", "hybrid"),
        ("Remote; Canada", "remote"),
        ("Not remote", "onsite"),
        ("New York", None),
    ],
)
def test_workplace_policy(value, expected):
    assert workplace(value) == expected


def test_salary_preserves_currency_period_and_does_not_guess_dollar_currency():
    salary = salary_from_text("CAD 180k – 240k per year")
    assert (salary.minimum, salary.maximum, salary.currency, salary.period) == (
        180000,
        240000,
        "CAD",
        "year",
    )
    assert salary_from_text("$180k – $240k").currency is None
    assert salary_from_text("$180k – $240k").period is None
    assert salary_from_text("USD 100 per hour").period == "hour"
    assert salary_from_schema({"currency": "USD", "value": "invalid"}) is None
    assert salary_from_text("Salary between USD 160000 and USD 245000 per year").maximum == 245000
    assert sponsorship("Known for innovation; visa sponsorship available.") is True


def test_block_detection_does_not_mistake_job_description_for_challenge():
    assert access_problem("<h1>Security check</h1>", URLS["indeed"])
    assert access_problem("<h1>Jobs</h1>", URLS["indeed"], 403) == "HTTP 403"
    assert access_problem("<h1>Login</h1>", "https://linkedin.com/authwall")
    assert (
        access_problem(
            "<h1>Senior Software Engineer</h1><p>Build captcha tools</p>", URLS["linkedin"]
        )
        is None
    )
