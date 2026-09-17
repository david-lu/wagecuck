from __future__ import annotations

import asyncio
import re
from urllib.parse import quote, urlencode

from .dedupe import canonical_url
from .models import SearchCriteria, SiteResult
from .parsing import document, job_links, parse_posting


def search_url(site: str, query: str, page: int = 0) -> str:
    # Only the broad title is sent upstream. All personal preferences are local filters.
    if site == "wellfound":
        slug = quote(re.sub(r"\s+", "-", query))
        return f"https://wellfound.com/role/{slug}?page={page + 1}"
    if site == "indeed":
        return "https://www.indeed.com/jobs?" + urlencode({"q": query, "start": page * 10})
    if site == "linkedin":
        return "https://www.linkedin.com/jobs/search/?" + urlencode(
            {"keywords": query, "start": page * 25}
        )
    if site == "simplify":
        return "https://simplify.jobs/jobs"
    raise ValueError(f"Unsupported site: {site}")


class SiteAccessError(RuntimeError):
    pass


class PostingUnavailable(SiteAccessError):
    """A removed posting is normal search churn, not a block on the entire board."""


def access_problem(html: str, url: str, status: int | None = None) -> str | None:
    if status is not None and status >= 400:
        return f"HTTP {status}"
    soup = document(html)
    heading = " ".join(v.get_text(" ", strip=True) for v in soup.select("title, h1, h2"))
    if re.search(
        r"captcha|verify (?:you|your)|security (?:check|verification)|"
        r"just a moment|access denied|are you (?:a )?human|pardon our interruption",
        heading,
        re.I,
    ):
        return "Site requires a security check"
    if re.search(r"/(?:authwall|login|checkpoint|users/sign_in)(?:[/?]|$)", url):
        return "Site requires sign-in"
    return None


class BrowserProvider:
    """Read public search/detail pages in a fresh, independent browser context."""

    def __init__(self, site: str, browser):
        self.site = site
        self.browser = browser
        self.progress = None

    def _progress(self, result):
        if self.progress:
            self.progress(
                {
                    "phase": "discovery",
                    "site": self.site,
                    "pages": result.pages,
                    "discovered": result.discovered,
                    "records": len(result.jobs),
                    "advertised_total": result.advertised_total,
                }
            )

    async def fetch(self, query: str, criteria: SearchCriteria) -> SiteResult:
        result = SiteResult(self.site)
        context = None
        try:
            async with asyncio.timeout(criteria.site_timeout_seconds):
                context = await self.browser.new_context()
                context.set_default_timeout(criteria.timeout_seconds * 1000)
                context.set_default_navigation_timeout(criteria.timeout_seconds * 1000)
                page = await context.new_page()
                detail = await context.new_page()
                seen = set()
                for page_number in range(criteria.max_pages):
                    html, links, seeds = await self._discover(page, query, page_number)
                    result.pages += 1
                    problem = access_problem(html, page.url)
                    if problem:
                        raise SiteAccessError(problem)
                    new_links = [url for url in links if canonical_url(url) not in seen]
                    if not new_links:
                        if not seen and not re.search(
                            r"no (?:matching |search |new )?(?:jobs|results)|0 (?:jobs|results)|"
                            r"couldn.t find any|no jobs found",
                            document(html).get_text(" "),
                            re.I,
                        ):
                            raise SiteAccessError(
                                "No recognizable listings or explicit empty result; "
                                "the page may require sign-in or its layout changed"
                            )
                        if links:
                            result.limited = True
                            result.errors.append("Search page repeated; pagination stopped")
                        break
                    result.discovered += len(new_links)
                    for url in new_links:
                        seen.add(canonical_url(url))
                        seed = seeds.get(url)
                        try:
                            await self._navigate(detail, url)
                            result.detail_visits += 1
                            await detail.wait_for_selector(
                                'h1, script[type="application/ld+json"]', state="attached"
                            )
                            job = parse_posting(self.site, await detail.content(), url)
                            if seed:
                                job = self._enrich_seed(seed, job)
                            if job:
                                result.jobs.append(job)
                            else:
                                result.errors.append(f"Could not extract title and company: {url}")
                        except PostingUnavailable:
                            result.unavailable += 1
                        except SiteAccessError as exc:
                            if seed:
                                seed.note = "Detail unavailable; using published search metadata"
                                result.jobs.append(seed)
                            result.errors.append(f"{exc}: {url}")
                            # Stop this site when it blocks detail access; retain prior jobs.
                            result.status = "partial" if result.jobs else "blocked"
                            return result
                        except Exception as exc:
                            if seed:
                                seed.note = "Detail unavailable; using published search metadata"
                                result.jobs.append(seed)
                            result.errors.append(f"Detail failed ({type(exc).__name__}): {url}")
                        if len(seen) >= criteria.max_per_site:
                            result.limited = True
                            return self._finish(result)
                        # A bounded request rate per site; different sites run concurrently.
                        await asyncio.sleep(0.5)
                    if page_number == criteria.max_pages - 1:
                        result.limited = True
                    self._progress(result)
                return self._finish(result)
        except TimeoutError:
            result.errors.append(f"Site timed out after {criteria.site_timeout_seconds:g}s")
            result.status = "partial" if result.jobs else "error"
            result.limited = True
        except SiteAccessError as exc:
            result.errors.append(str(exc))
            result.status = "partial" if result.jobs else "blocked"
        except Exception as exc:
            result.errors.append(f"{type(exc).__name__}: {str(exc)[:300]}")
            result.status = "partial" if result.jobs else "error"
        finally:
            if context is not None:
                await context.close()
        return result

    @staticmethod
    def _finish(result):
        if result.errors:
            result.status = "partial" if result.jobs else "error"
        return result

    @staticmethod
    def _enrich_seed(seed, detail):
        if detail is None:
            seed.note = "Detail could not be parsed; using published search metadata"
            return seed
        for key in ("last_updated", "posted_at", "internship", "employment_type", "workplace"):
            if getattr(detail, key) is None:
                setattr(detail, key, getattr(seed, key))
        if seed.location != "Unknown":
            detail.location = seed.location
            if detail.note == "Location not published":
                detail.note = None
        detail.experience_levels = seed.experience_levels
        if detail.salary is None:
            detail.salary = seed.salary
        elif seed.salary and detail.salary.currency is None:
            detail.salary.currency = seed.salary.currency
        return detail

    async def _discover(self, page, query, page_number):
        await self._navigate(page, search_url(self.site, query, page_number))
        html = await self._listing_html(page)
        return html, job_links(self.site, html, page.url), {}

    async def _navigate(self, page, url):
        response = await page.goto(url, wait_until="domcontentloaded")
        if response and response.status in (404, 410):
            raise PostingUnavailable(f"HTTP {response.status}")
        problem = access_problem(
            await page.content(), page.url, response.status if response else None
        )
        if problem:
            raise SiteAccessError(problem)

    async def _listing_html(self, page):
        selectors = {
            "wellfound": 'a[href*="/jobs/"]',
            "indeed": 'a[href*="jk="]',
            "linkedin": 'a[href*="/jobs/view/"]',
            "simplify": 'a[href*="/p/"]',
        }
        try:
            await page.wait_for_selector(selectors[self.site], state="attached")
        except Exception:
            pass  # Inspect explicit empty/blocked states after the bounded wait.
        return await page.content()
