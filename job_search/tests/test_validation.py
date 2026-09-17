import asyncio
import json
from types import SimpleNamespace

import pytest

from wagecuck_search.application_links import extract_links, simplify_application_url, unwrap
from wagecuck_search.models import JobPosting, SearchCriteria, SiteResult
from wagecuck_search.pipeline import search
from wagecuck_search.validation import (
    BrowserValidator,
    ValidationPlan,
    ValidationResult,
    destination_problem,
)

SOURCE = "https://linkedin.com/jobs/view/123"
NATIVE = "https://careers.acme.com/jobs/senior-engineer"
ATS = "https://boards.greenhouse.io/acme/jobs/123"


def posting(**changes):
    return JobPosting(
        **(
            dict(
                url=SOURCE,
                title="Senior Software Engineer",
                company="Acme",
                location="Canada",
                source="linkedin",
            )
            | changes
        )
    )


def page_html(**changes):
    record = {
        "@type": "JobPosting",
        "title": "Senior Software Engineer",
        "hiringOrganization": {"name": "Acme"},
        **changes,
    }
    return (
        "<title>Acme Careers</title><h1>Senior Software Engineer</h1>"
        '<form><input type="email"><input type="file"></form>'
        '<script type="application/ld+json">' + json.dumps(record) + "</script>"
    )


@pytest.mark.parametrize(
    "url, employers, kind",
    [
        (NATIVE, ["https://acme.com"], "employer"),
        (ATS, [], "ats"),
        ("https://acme.wd1.myworkdayjobs.com/en-US/jobs/job/123", [], "ats"),
    ],
)
def test_employer_and_employer_branded_ats_are_valid(url, employers, kind):
    assert destination_problem(posting(), page_html(), url, 200, employers) == (None, kind)


@pytest.mark.parametrize(
    "url",
    [
        SOURCE,
        "https://indeed.com/viewjob?jk=123",
        "https://simplify.jobs/p/abc",
        "https://wellfound.com/jobs/123",
        "https://www.facebook.com/jobs/123",
        "https://www.glassdoor.com/job/123",
    ],
)
def test_boards_and_social_pages_never_qualify_even_with_a_form(url):
    assert destination_problem(posting(), page_html(), url, 200, [url])[0]


@pytest.mark.parametrize("status", [403, 404, 410, 429, 500, None])
def test_non_success_http_status_is_rejected(status):
    assert destination_problem(posting(), page_html(), NATIVE, status, ["https://acme.com"])[0]


@pytest.mark.parametrize(
    "html",
    [
        "<h1>Page not found</h1>",
        page_html() + "<p>This position is no longer available.</p>",
        page_html(validThrough="2001-01-01"),
        "<h1>Sign in</h1><form><input type=email></form>",
        '<h1>Acme Careers</h1><h2>Senior Software Engineer</h2><a href="/apply">Apply</a>',
        "<h1>Senior Software Engineer</h1><p>No application entry point</p>",
        '<h1>Senior Software Engineer</h1><a href="https://linkedin.com/jobs/view/123">Apply</a>',
        '<h1>Senior Software Engineer</h1><a href="https://unknown-board.com/jobs/123">Apply</a>',
        '<h1>Senior Software Engineer</h1><form action="https://linkedin.com/apply">'
        "<input type=email></form>",
    ],
)
def test_http_200_is_not_enough_for_closed_generic_or_board_only_pages(html):
    assert destination_problem(posting(), html, NATIVE, 200, ["https://acme.com"])[0]


def test_ats_must_match_employer_and_unknown_domain_cannot_self_assert_ownership():
    html = page_html(hiringOrganization={"name": "Other"}).replace("Acme Careers", "Other")
    assert destination_problem(posting(), html, ATS, 200, [])[0]
    assert destination_problem(posting(), page_html(), "https://unknown.com/apply/123", 200, [])[0]
    assert destination_problem(
        posting(), page_html(), "https://acme.com.evil.org/job/123", 200, ["https://acme.com"]
    )[0]


def test_extract_apply_links_and_employer_provenance_without_navigation_links():
    html = (
        f'<a href="{ATS}">Apply now</a><a href="https://acme.com">Website</a>'
        '<a href="https://unrelated.com">Share</a>'
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"apply_url": NATIVE}})
        + "</script>"
    )
    applications, employers = extract_links(html, SOURCE)
    assert applications == [ATS, NATIVE]
    assert employers == ["https://acme.com"]
    assert unwrap("https://linkedin.com/redir/redirect?url=https%3A%2F%2Facme.com%2Fjob%2F1") == (
        "https://acme.com/job/1"
    )


def test_simplify_posting_redirect_is_extracted_from_published_next_data():
    url = "https://simplify.jobs/jobs/click/123"
    data = {
        "props": {
            "pageProps": {
                "jobPosting": {"url": url, "job": {"company": {"url": "https://acme.com"}}}
            }
        }
    }
    html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script>"
    applications, employers = extract_links(html, "https://simplify.jobs/p/123")
    assert applications == [url] and employers == ["https://acme.com"]


def test_levels_ignores_unrelated_applications_in_listing_page_state():
    html = (
        '<a href="https://linkedin.com/jobs/view/current">Apply now</a>'
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"related_jobs": [{"apply_url": "https://other.example/apply"}]})
        + "</script>"
    )
    applications, _ = extract_links(html, "https://www.levels.fyi/jobs?jobId=123")
    assert applications == ["https://linkedin.com/jobs/view/current"]


class Browser:
    def __init__(self, pages):
        self.documents = pages
        self.closed = False
        self.visited = []

    async def new_context(self):
        return self

    def set_default_timeout(self, value):
        pass

    async def new_page(self):
        return self

    async def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url, self.html, status = self.documents[url]
        return SimpleNamespace(url=self.url, status=status)

    async def wait_for_load_state(self, *args, **kwargs):
        pass

    async def content(self):
        return self.html

    async def close(self):
        self.closed = True


def test_validator_follows_apply_redirect_and_returns_final_native_url():
    tracking = "https://linkedin.com/jobs/externalApply/123"
    source_html = (
        f'<a href="{tracking}">Apply now</a><a href="https://acme.com">Company website</a>'
    )
    browser = Browser({SOURCE: (SOURCE, source_html, 200), tracking: (NATIVE, page_html(), 200)})
    result = asyncio.run(
        BrowserValidator(browser, SearchCriteria("software engineer")).validate(posting())
    )
    assert result.url == NATIVE and result.kind == "employer" and result.checked_at
    assert browser.visited == [SOURCE, tracking]
    assert browser.closed


def test_validator_rejects_redirect_to_closed_application():
    browser = Browser(
        {
            SOURCE: (SOURCE, f'<a href="{ATS}">Apply</a>', 200),
            ATS: (ATS, "<h1>Job not found</h1>", 404),
        }
    )
    result = asyncio.run(
        BrowserValidator(browser, SearchCriteria("software engineer")).validate(posting())
    )
    assert result.url is None and result.reason == "HTTP 404"
    assert browser.closed


def test_static_source_and_native_pages_validate_without_launching_a_page():
    class HttpBrowser(Browser):
        @property
        def request(self):
            return self

        async def new_page(self):
            raise AssertionError("Static evidence should not launch a browser page")

        async def get(self, url, **kwargs):
            self.visited.append(url)
            final, html, status = self.documents[url]

            class Response:
                async def text(self):
                    return html

                async def dispose(self):
                    pass

            response = Response()
            response.url, response.status = final, status
            return response

    browser = HttpBrowser(
        {
            SOURCE: (
                SOURCE,
                f'<a href="{NATIVE}">Apply now</a><a href="https://acme.com">Website</a>',
                200,
            ),
            NATIVE: (NATIVE, page_html(), 200),
        }
    )
    result = asyncio.run(
        BrowserValidator(browser, SearchCriteria("software engineer")).validate(posting())
    )
    assert result.url == NATIVE
    assert browser.closed and browser.visited == [SOURCE, NATIVE]


def test_slow_redirect_destinations_do_not_hold_the_source_host_slots():
    async def scenario():
        release = asyncio.Event()
        employers_started = asyncio.Event()
        started = []
        disposed = []

        class Response:
            def __init__(self, url, status=200, location=None):
                self.url, self.status = url, status
                self.headers = {"location": location} if location else {}

            async def text(self):
                return "document"

            async def dispose(self):
                disposed.append(self.url)

        class Request:
            async def get(self, url, **kwargs):
                assert kwargs["max_redirects"] == 0
                if url.startswith("https://board.test/redirect/"):
                    return Response(url, 302, "https://employer.test/" + url.rsplit("/", 1)[1])
                if url.startswith("https://employer.test/"):
                    started.append(url)
                    if len(started) == 2:
                        employers_started.set()
                    await release.wait()
                return Response(url)

        validator = BrowserValidator(None, SearchCriteria("software engineer"))
        request = Request()
        tasks = [
            asyncio.create_task(validator._http_document(request, f"https://board.test/redirect/{i}"))
            for i in range(2)
        ]
        try:
            await asyncio.wait_for(employers_started.wait(), 1)
            result = await asyncio.wait_for(
                validator._http_document(request, "https://board.test/next-job"), 1
            )
            assert result == ("document", "https://board.test/next-job", 200)
            assert len(disposed) == 3
        finally:
            release.set()
            await asyncio.gather(*tasks)
        assert len(disposed) == 5

    asyncio.run(scenario())


@pytest.mark.parametrize("location", ["/redirect", "javascript:alert(1)"])
def test_redirect_loops_and_non_web_destinations_are_bounded(location):
    class Request:
        async def get(self, url, **kwargs):
            class Response:
                status = 302
                headers = {"location": location}

                async def dispose(self):
                    pass

            response = Response()
            response.url = url
            return response

    with pytest.raises(ValueError, match="redirect"):
        asyncio.run(
            BrowserValidator(None, SearchCriteria("engineer"))._http_document(
                Request(), "https://board.test/redirect"
            )
        )


def test_rendered_application_evidence_does_not_wait_for_network_idle():
    class ReadyPage(Browser):
        async def wait_for_load_state(self, *args, **kwargs):
            raise AssertionError("No network-idle wait should be needed")

        async def wait_for_timeout(self, *args, **kwargs):
            raise AssertionError("Application is already ready")

    browser = ReadyPage({ATS: (ATS, page_html(), 200)})
    result = asyncio.run(
        BrowserValidator(browser, SearchCriteria("software engineer")).validate(
            posting(url=ATS, source="simplify")
        )
    )
    assert result.url == ATS and result.kind == "ats"


def test_browser_fallback_uses_the_destination_already_resolved_by_http():
    tracking = "https://simplify.jobs/jobs/click/123"

    class RedirectBrowser(Browser):
        @property
        def request(self):
            return self

        async def get(self, url, **kwargs):
            class Response:
                async def text(self):
                    return "<div id=app></div>"

                async def dispose(self):
                    pass

            response = Response()
            response.url = url
            response.status = 302 if url == tracking else 200
            response.headers = {"location": ATS} if url == tracking else {}
            return response

    browser = RedirectBrowser({ATS: (ATS, page_html(), 200)})
    result = asyncio.run(
        BrowserValidator(browser, SearchCriteria("software engineer")).validate(
            posting(url=tracking, source="simplify")
        )
    )
    assert result.url == ATS
    assert browser.visited == [ATS]


def test_http_preparation_defers_javascript_and_preserves_employer_provenance():
    class DeferredBrowser(Browser):
        @property
        def request(self):
            return self

        async def get(self, url, **kwargs):
            class Response:
                status = 200

                async def text(self):
                    if url == SOURCE:
                        return f'<a href="{NATIVE}">Apply</a><a href="https://acme.com">Website</a>'
                    return "<div id=app></div>"

                async def dispose(self):
                    pass

            response = Response()
            response.url = url
            return response

    async def scenario():
        browser = DeferredBrowser({NATIVE: (NATIVE, page_html(), 200)})
        validator = BrowserValidator(browser, SearchCriteria("software engineer"))
        plan = await validator.prepare(posting())
        assert isinstance(plan, ValidationPlan)
        assert browser.visited == []
        assert plan.employer_urls == ["https://acme.com"]
        assert plan.targets == [(NATIVE, 1)]
        assert 0 < plan.remaining_seconds <= 60
        result = await validator.finish(plan)
        assert result.url == NATIVE and result.kind == "employer"
        assert browser.visited == [NATIVE]

    asyncio.run(scenario())


def test_structured_direct_apply_is_evidence_only_on_a_verified_role_and_employer():
    html = page_html(directApply=True).replace(
        '<form><input type="email"><input type="file"></form>', ""
    )
    assert destination_problem(posting(), html, ATS, 200, []) == (None, "ats")
    assert destination_problem(posting(), html, SOURCE, 200, [])[0]
    assert destination_problem(posting(), html, "https://unknown.test/job/1", 200, [])[0]
    assert destination_problem(posting(title="Staff Software Engineer"), html, ATS, 200, [])[0]
    assert destination_problem(posting(company="Other"), html, ATS, 200, [])[0]
    assert destination_problem(posting(), html.replace('true', 'false'), ATS, 200, [])[0]


def test_simplify_redirect_shortcut_still_validates_the_native_employer_and_role():
    source = "https://simplify.jobs/p/eba93cba-69a6-44f0-8558-bff28dcb36f1"
    shortcut = "https://simplify.jobs/jobs/click/eba93cba-69a6-44f0-8558-bff28dcb36f1"
    assert simplify_application_url(source) == shortcut
    assert simplify_application_url("https://simplify.jobs.evil.test/p/123") is None
    assert simplify_application_url("https://simplify.jobs/p/not-a-uuid") is None
    browser = Browser({shortcut: (ATS, page_html(), 200)})
    result = asyncio.run(
        BrowserValidator(browser, SearchCriteria("software engineer")).validate(
            posting(url=source, source="simplify")
        )
    )
    assert result.url == ATS and browser.visited == [shortcut]


@pytest.mark.parametrize("native", [ATS, NATIVE])
def test_http_shortcut_skips_board_html_only_when_ats_identity_can_be_checked(native):
    source = "https://simplify.jobs/p/eba93cba-69a6-44f0-8558-bff28dcb36f1"
    shortcut = simplify_application_url(source)

    class ShortcutBrowser(Browser):
        def __init__(self):
            super().__init__({native: (native, page_html(), 200)})
            self.http_urls = []

        @property
        def request(self):
            return self

        async def get(self, url, **kwargs):
            self.http_urls.append(url)

            class Response:
                async def text(self):
                    if url == source:
                        return f'<a href="{shortcut}">Apply</a><a href="https://acme.com">Website</a>'
                    return "<div id=app></div>"

                async def dispose(self):
                    pass

            response = Response()
            response.url = url
            response.status = 302 if url == shortcut else 200
            response.headers = {"location": native} if url == shortcut else {}
            return response

    async def scenario():
        browser = ShortcutBrowser()
        validator = BrowserValidator(browser, SearchCriteria("software engineer"))
        plan = await validator.prepare(posting(url=source, source="simplify"))
        assert isinstance(plan, ValidationPlan)
        assert (source in browser.http_urls) == (native == NATIVE)
        assert plan.targets == [(native, 0)]
        result = await validator.finish(plan)
        assert result.url == native

    asyncio.run(scenario())


def test_recycling_only_replaces_browsers_owned_by_the_validator():
    async def scenario():
        first, second = Browser({}), Browser({})

        async def factory():
            assert first.closed
            return second

        external = BrowserValidator(first, SearchCriteria("software engineer"))
        await external.recycle()
        assert not first.closed
        owned = BrowserValidator(first, SearchCriteria("software engineer"), browser_factory=factory)
        await owned.recycle()
        assert owned.browser is second
        await owned.close()
        assert second.closed

    asyncio.run(scenario())


class Provider:
    site = "linkedin"

    async def fetch(self, query, criteria):
        return SiteResult(
            self.site,
            [
                posting(),
                posting(url=SOURCE + "4", location="Remote"),
                posting(url=SOURCE + "5", location="US"),
            ],
            discovered=3,
        )


class Validator:
    async def validate(self, job):
        if job.location == "US":
            return ValidationResult(reason="HTTP 410")
        return ValidationResult(ATS, "ats")


def test_pipeline_validates_after_filters_dedupes_native_urls_and_counts_rejections():
    report = asyncio.run(
        search(SearchCriteria("software engineer", sites=("linkedin",)), [Provider()], Validator())
    )
    assert len(report["jobs"]) == 1
    result = report["jobs"][0]
    assert result["url"] == ATS and len(result["sources"]) == 2
    assert result["url_validated_at"] and result["application_url_type"] == "ats"
    summary = report["summary"]
    assert summary["validation_attempted"] == 3
    assert summary["validation_rejected"] == 1
    assert summary["application_url_deduplicated"] == summary["deduplicated"] == 1
    assert summary["validation_rejections"][0]["reason"] == "HTTP 410"
    assert summary["sites"]["linkedin"]["returned"] == 1


def test_validator_failure_cannot_be_relaxed_by_include_unknown():
    class Broken:
        async def validate(self, job):
            raise TimeoutError()

    report = asyncio.run(
        search(
            SearchCriteria("software engineer", sites=("linkedin",), include_unknown=True),
            [Provider()],
            Broken(),
        )
    )
    assert report["jobs"] == []
    assert report["summary"]["validation_rejected"] == 3
