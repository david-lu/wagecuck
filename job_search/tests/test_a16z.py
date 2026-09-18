import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright

from wagecuck_search.a16z import A16zProvider, search_data
from wagecuck_search.cli import parser
from wagecuck_search.models import SITES, SearchCriteria
from wagecuck_search.parsing import job_links, parse_posting, posting_url
from wagecuck_search.profiles import SearchProfile
from wagecuck_search.providers import provider_for, search_url

BASE = "https://jobs.a16z.com/jobs"
JOB = BASE + "/acme/123--staff-software-engineer"
NATIVE = "https://jobs.ashbyhq.com/acme/123/application"


def card(job_id="123"):
    return f"""<article>
      <div data-nosnippet><a href="/jobs/acme">Acme</a></div>
      <h2><a href="/jobs/acme/{job_id}--staff-software-engineer">Staff Software Engineer</a></h2>
      <div>
        <span>CA$172K - CA$237K</span><span><span>&middot;</span><span>Canada</span></span>
        <span><span>&middot;</span><time datetime="2026-09-18T10:00:00Z">Posted recently</time></span>
      </div>
      <div><span>Series A</span><span>51-200 employees</span></div>
      <a href="https://jobs.ashbyhq.com/acme/{job_id}/application">Apply</a>
    </article>"""


def listing(*ids):
    return (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "ItemList", "numberOfItems": 2})
        + "</script>"
        + '<nav><a href="/jobs">All jobs</a><a href="/jobs/acme">Acme</a></nav>'
        + "".join(card(value) for value in ids)
    )


def detail(job_id="123"):
    return f"""<main>
      <header><a href="/jobs/acme">Acme</a><h1>Staff Software Engineer</h1>
        <dl>
          <div><dt>Location</dt><dd>Canada</dd></div>
          <div><dt>Work arrangement</dt><dd>Remote</dd></div>
          <div><dt>Job type</dt><dd>Full-time</dd></div>
          <div><dt>Compensation</dt><dd>CA$172K - CA$237K</dd></div>
        </dl>
      </header>
      <article aria-labelledby="about-role">
        <time datetime="2026-09-18T10:00:00Z">Sep 18, 2026</time>
        <div class="prose-job">Build accessible interfaces. Visa sponsorship is available.</div>
        <a href="https://jobs.ashbyhq.com/acme/{job_id}/application">Apply for this role</a>
      </article>
    </main>"""


def test_source_is_enabled_for_cli_and_existing_search_profile():
    profile = SearchProfile.load(
        Path(__file__).parents[1] / "profiles" / "los-angeles-or-remote.json"
    )
    assert "a16z" in SITES and "a16z" in SearchCriteria("software engineer").sites
    assert "a16z" in profile.sites
    args = parser().parse_args(["--job-title", "software engineer", "--sites", "a16z"])
    assert args.sites == ["a16z"]
    assert isinstance(provider_for("a16z", None), A16zProvider)
    assert parse_qs(urlsplit(search_url("a16z", "software engineer")).query) == {
        "titlePrefix": ["software engineer"]
    }


@pytest.mark.parametrize("job_id", ["123", "12.F1B", "dc19c028-dc52-428b-8c11-d5c170080bda"])
def test_posting_identity_accepts_native_ids_but_excludes_company_navigation(job_id):
    url = BASE + f"/acme/{job_id}--software-engineer"
    assert posting_url("a16z", url)
    assert not posting_url("a16z", BASE + "/acme")
    assert not posting_url("a16z", BASE)
    assert not posting_url("a16z", url.replace("jobs.a16z.com", "jobs.a16z.com.example.org"))


def test_cards_preserve_native_links_salary_and_locations_without_mixing_jobs():
    html = listing("123", "456", "123")
    jobs, total = search_data(html, BASE)
    assert total == 2 and len(jobs) == 2
    assert jobs[0].url == JOB and jobs[0].company == "Acme"
    assert jobs[0].location == "Canada"
    assert jobs[0].salary.currency == "CAD"
    assert jobs[0].salary.minimum == 172000 and jobs[0].salary.maximum == 237000
    assert jobs[0].posted_at == "2026-09-18T10:00:00Z"
    assert jobs[0].application_urls == [NATIVE]
    assert jobs[1].application_urls == ["https://jobs.ashbyhq.com/acme/456/application"]
    assert job_links("a16z", html, BASE) == [job.url for job in jobs]


def test_detail_keeps_remote_restrictions_description_and_native_apply_target():
    seed = search_data(listing("123"), BASE)[0][0]
    job = A16zProvider._enrich_seed(seed, parse_posting("a16z", detail(), JOB))
    assert (job.title, job.company, job.location) == (
        "Staff Software Engineer",
        "Acme",
        "Remote; Canada",
    )
    assert job.workplace == "remote" and job.employment_type == "full_time"
    assert job.salary.currency == "CAD" and job.salary.maximum == 237000
    assert job.sponsors_visa is True and job.internship is False
    assert job.posted_at == "2026-09-18T10:00:00Z"
    assert job.application_urls == [NATIVE]
    assert job.description == "Build accessible interfaces. Visa sponsorship is available."


def test_unparseable_detail_retains_published_card_metadata():
    seed = search_data(listing("123"), BASE)[0][0]
    job = A16zProvider._enrich_seed(seed, None)
    assert job.application_urls == [NATIVE] and job.location == "Canada"
    assert "published search metadata" in job.note


def test_card_parser_handles_missing_optional_metadata_and_bad_schema():
    html = '<script type="application/ld+json">invalid</script><article>'
    html += '<div data-nosnippet><a href="/jobs/acme">Acme</a></div>'
    html += '<h2><a href="/jobs/acme/123--software-engineer">Software Engineer</a></h2>'
    html += "</article>"
    jobs, total = search_data(html, BASE)
    assert total is None and len(jobs) == 1
    assert jobs[0].location == "Unknown" and jobs[0].salary is None


@pytest.mark.parametrize(
    "max_pages,max_per_site,expected,limited",
    [(3, 10, 2, False), (1, 10, 1, True), (3, 1, 1, True)],
)
def test_browser_pagination_enrichment_and_budgets(max_pages, max_per_site, expected, limited):
    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            requests = []
            contexts = []

            async def serve(route):
                url = route.request.url
                requests.append(url)
                if urlsplit(url).path == "/jobs":
                    html = listing("123") + "<button>Show more jobs</button>"
                    html += (
                        "<script>document.querySelector('button').onclick = function() {"
                        "this.insertAdjacentHTML('beforebegin', "
                        + json.dumps(card("456"))
                        + "); this.remove(); };</script>"
                    )
                else:
                    job_id = urlsplit(url).path.rsplit("/", 1)[-1].split("--")[0]
                    html = detail(job_id)
                await route.fulfill(status=200, content_type="text/html", body=html)

            class RoutedBrowser:
                async def new_context(self):
                    context = await browser.new_context()
                    contexts.append(context)
                    await context.route("**/*", serve)
                    return context

            result = await provider_for("a16z", RoutedBrowser()).fetch(
                "software engineer",
                SearchCriteria(
                    "software engineer",
                    sites=("a16z",),
                    max_pages=max_pages,
                    max_per_site=max_per_site,
                    timeout_seconds=3,
                    site_timeout_seconds=20,
                ),
            )
            assert result.status == "ok", result.errors
            assert len(result.jobs) == expected and result.limited is limited
            assert result.advertised_total == 2 and result.detail_visits == expected
            assert all(job.location == "Remote; Canada" for job in result.jobs)
            assert len({job.url for job in result.jobs}) == expected
            assert sum(urlsplit(url).path == "/jobs" for url in requests) == 1
            assert all(not context.pages for context in contexts)
            await browser.close()

    asyncio.run(run())
