import asyncio
import json

from wagecuck_search.models import SearchCriteria
from wagecuck_search.providers import (
    BrowserProvider,
    PostingUnavailable,
    SiteAccessError,
    levels_search_data,
    search_url,
)

HTML = """<h1>Senior Software Engineer</h1>
<a class="topcard__org-name-link">Acme</a>
<span class="topcard__flavor--bullet">Toronto</span>"""


class Page:
    url = "https://linkedin.com/jobs/search"

    async def wait_for_selector(self, *args, **kwargs):
        pass

    async def content(self):
        return HTML


class Context:
    closed = False

    def set_default_timeout(self, value):
        pass

    def set_default_navigation_timeout(self, value):
        pass

    async def new_page(self):
        return Page()

    async def close(self):
        self.closed = True


class Browser:
    def __init__(self):
        self.context = Context()

    async def new_context(self):
        return self.context


class Provider(BrowserProvider):
    def __init__(self, browser, pages, blocked=None, delay=0):
        super().__init__("linkedin", browser)
        self.pages = pages
        self.visited = []
        self.blocked = blocked
        self.delay = delay

    async def _discover(self, page, query, page_number):
        return "<p>No jobs found</p>", self.pages[page_number], {}

    async def _navigate(self, page, url):
        self.visited.append(url)
        if self.delay:
            await asyncio.sleep(self.delay)
        if url == self.blocked:
            raise SiteAccessError("HTTP 403")


def test_pagination_repeats_stop_without_refetching_and_close_context():
    browser = Browser()
    provider = Provider(browser, [["https://linkedin.com/jobs/view/1"]] * 3)
    result = asyncio.run(provider.fetch("software engineer", SearchCriteria("software engineer")))
    assert len(result.jobs) == 1
    assert len(provider.visited) == 1
    assert result.limited and result.status == "partial"
    assert browser.context.closed


def test_candidate_budget_is_enforced_and_exposed():
    browser = Browser()
    provider = Provider(
        browser, [["https://linkedin.com/jobs/view/1", "https://linkedin.com/jobs/view/2"]]
    )
    result = asyncio.run(
        provider.fetch("software engineer", SearchCriteria("software engineer", max_per_site=1))
    )
    assert result.discovered == 2 and len(result.jobs) == 1
    assert result.limited and browser.context.closed


def test_detail_block_preserves_partial_results_and_closes_context():
    browser = Browser()
    provider = Provider(
        browser,
        [["https://linkedin.com/jobs/view/1", "https://linkedin.com/jobs/view/2"]],
        blocked="https://linkedin.com/jobs/view/2",
    )
    result = asyncio.run(provider.fetch("software engineer", SearchCriteria("software engineer")))
    assert len(result.jobs) == 1 and result.status == "partial"
    assert result.errors and browser.context.closed


def test_site_deadline_is_bounded_and_closes_context():
    browser = Browser()
    provider = Provider(browser, [["https://linkedin.com/jobs/view/1"]], delay=1)
    result = asyncio.run(
        provider.fetch(
            "software engineer", SearchCriteria("software engineer", site_timeout_seconds=0.01)
        )
    )
    assert result.status == "error" and result.limited
    assert "timed out" in result.errors[0]
    assert browser.context.closed


def test_removed_posting_does_not_stop_remaining_discovery():
    class RemovedProvider(Provider):
        async def _navigate(self, page, url):
            if url.endswith("/1"):
                raise PostingUnavailable("HTTP 410")

    provider = RemovedProvider(
        Browser(), [["https://linkedin.com/jobs/view/1", "https://linkedin.com/jobs/view/2"]]
    )
    result = asyncio.run(
        provider.fetch("software engineer", SearchCriteria("software engineer", max_pages=1))
    )
    assert result.unavailable == 1
    assert len(result.jobs) == 1
    assert result.status == "ok"


def test_new_board_search_urls_use_only_the_broad_title():
    query = "software engineer"
    assert search_url("hiringcafe", query).startswith(
        "https://hiring.cafe/jobs/software-engineer"
    )
    assert "software-engineer-jobs-in-united-states" in search_url("jobright", query)
    assert search_url("levels", query) == "https://www.levels.fyi/jobs/title/software-engineer"
    assert "query=software+engineer" in search_url("trueup", query)
    assert "query=software+engineer" in search_url("yc", query)
    assert "search=software+engineer" in search_url("builtin", query)


def test_levels_bulk_records_keep_only_their_own_application_url():
    payload = {
        "props": {
            "pageProps": {
                "initialJobsData": {
                    "totalMatchingJobs": 73600,
                    "results": [
                        {
                            "companyName": "Acme",
                            "jobs": [
                                {
                                    "id": "123",
                                    "title": "Senior Software Engineer",
                                    "locations": ["San Francisco, CA"],
                                    "applicationUrl": "https://boards.greenhouse.io/acme/jobs/123",
                                    "postingDate": "2026-09-01",
                                    "expiryDate": "2026-10-01",
                                    "minBaseSalary": 180000,
                                    "maxBaseSalary": 240000,
                                    "baseSalaryCurrency": "USD",
                                    "workArrangement": "remote",
                                }
                            ],
                        },
                        {
                            "companyName": "Other",
                            "jobs": [
                                {
                                    "id": "456",
                                    "title": "Software Engineer",
                                    "locations": [],
                                    "applicationUrl": "https://linkedin.com/jobs/view/456",
                                }
                            ],
                        },
                    ],
                }
            }
        }
    }
    html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"
    jobs, advertised = levels_search_data(html)
    assert advertised == 73600 and len(jobs) == 2
    assert jobs[0].application_urls == ["https://boards.greenhouse.io/acme/jobs/123"]
    assert jobs[0].location == "Remote; San Francisco, CA"
    assert jobs[0].salary.minimum == 180000 and jobs[0].salary.period == "year"
    assert jobs[1].application_urls == ["https://linkedin.com/jobs/view/456"]


def test_detail_enrichment_uses_bounded_parallel_workers():
    class ConcurrentProvider(Provider):
        def __init__(self):
            super().__init__(
                Browser(),
                [[f"https://linkedin.com/jobs/view/{value}" for value in range(4)]],
            )
            self.active = 0
            self.most_active = 0

        async def _navigate(self, page, url):
            self.active += 1
            self.most_active = max(self.most_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1

    provider = ConcurrentProvider()
    result = asyncio.run(
        provider.fetch(
            "software engineer",
            SearchCriteria("software engineer", max_pages=1, detail_workers=3),
        )
    )
    assert len(result.jobs) == 4
    assert provider.most_active == 3
