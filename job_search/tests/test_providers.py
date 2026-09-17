import asyncio

from wagecuck_search.models import SearchCriteria
from wagecuck_search.providers import BrowserProvider, PostingUnavailable, SiteAccessError

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
