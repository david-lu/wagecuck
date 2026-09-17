from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote, urlencode

from .dedupe import canonical_url
from .models import JobPosting, Salary, SearchCriteria, SiteResult
from .parsing import document, job_links, parse_posting
from .workers import map_bounded


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
    slug = quote(re.sub(r"\s+", "-", query.strip().lower()))
    if site == "hiringcafe":
        return f"https://hiring.cafe/jobs/{slug}?page={page + 1}"
    if site == "jobright":
        return f"https://jobright.ai/jobs/{slug}-jobs-in-united-states?" + urlencode(
            {"page": page + 1}
        )
    if site == "levels":
        base = f"https://www.levels.fyi/jobs/title/{slug}"
        return base if page == 0 else base + "?" + urlencode({"page": page + 1})
    if site == "trueup":
        return "https://www.trueup.io/engineering?" + urlencode(
            {"query": query, "page": page + 1}
        )
    if site == "yc":
        return "https://www.ycombinator.com/jobs?" + urlencode(
            {"query": query, "page": page + 1}
        )
    if site == "builtin":
        return "https://builtin.com/jobs/dev-engineering?" + urlencode(
            {"search": query, "page": page + 1}
        )
    raise ValueError(f"Unsupported site: {site}")


def levels_search_data(html: str) -> tuple[list[JobPosting], int | None]:
    """Read Levels' current result records without mixing related Apply URLs."""
    script = document(html).select_one("script#__NEXT_DATA__")
    if not script:
        return [], None
    try:
        payload = json.loads(script.string or script.get_text())
        data = payload["props"]["pageProps"]["initialJobsData"]
    except (KeyError, TypeError, ValueError):
        return [], None
    postings = []
    for company in data.get("results", []):
        if not isinstance(company, dict):
            continue
        for value in company.get("jobs", []):
            if not isinstance(value, dict) or not value.get("id") or not value.get("title"):
                continue
            locations = [str(item) for item in value.get("locations", []) if item]
            mode = {"remote": "remote", "office": "onsite"}.get(value.get("workArrangement"))
            location = "; ".join(locations) or "Unknown"
            if mode == "remote" and not re.search(r"\bremote\b", location, re.I):
                location = "; ".join(("Remote", *locations))
            minimum, maximum = value.get("minBaseSalary"), value.get("maxBaseSalary")
            salary = None
            if isinstance(minimum, (int, float)) or isinstance(maximum, (int, float)):
                salary = Salary(minimum, maximum, value.get("baseSalaryCurrency"), "year")
            application = value.get("applicationUrl")
            postings.append(
                JobPosting(
                    url=f"https://www.levels.fyi/jobs?jobId={value['id']}",
                    title=str(value["title"]),
                    company=str(company.get("companyName") or "Unknown"),
                    location=location,
                    source="levels",
                    salary=salary,
                    posted_at=value.get("postingDate"),
                    valid_through=value.get("expiryDate"),
                    internship=(
                        True
                        if re.search(r"\b(?:intern|internship|co[ -]?op)\b", value["title"], re.I)
                        else None
                    ),
                    workplace=mode,
                    application_urls=[application]
                    if isinstance(application, str) and application.startswith(("http://", "https://"))
                    else [],
                )
            )
    total = data.get("totalMatchingJobs")
    return postings, total if isinstance(total, int) and total >= 0 else None


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
                seen = set()
                for page_number in range(criteria.max_pages):
                    html, links, seeds = await self._discover(page, query, page_number)
                    if getattr(self, "_advertised_total", None) is not None:
                        result.advertised_total = self._advertised_total
                    result.pages += 1
                    problem = access_problem(html, page.url)
                    if problem:
                        raise SiteAccessError(problem)
                    new_links = [url for url in links if canonical_url(url) not in seen]
                    if not new_links:
                        if (
                            result.advertised_total is not None
                            and len(seen) < result.advertised_total
                        ):
                            result.limited = True
                            result.errors.append(
                                "Pagination stopped before the source's advertised total"
                            )
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
                        if links and not result.limited:
                            result.limited = True
                            result.errors.append("Search page repeated; pagination stopped")
                        break
                    result.discovered += len(new_links)
                    remaining = criteria.max_per_site - len(seen)
                    selected = new_links[:remaining]
                    for url in selected:
                        seen.add(canonical_url(url))

                    async def enrich(url):
                        seed = seeds.get(url)
                        if self.site == "levels" and seed:
                            return "job", seed, None
                        detail = await context.new_page()
                        try:
                            await self._navigate(detail, url)
                            result.detail_visits += 1
                            await detail.wait_for_selector(
                                'h1, script[type="application/ld+json"]', state="attached"
                            )
                            job = parse_posting(self.site, await detail.content(), url)
                            if seed:
                                job = self._enrich_seed(seed, job)
                            return "job", job or seed, (
                                None if job or seed else f"Could not extract title and company: {url}"
                            )
                        except PostingUnavailable:
                            return "unavailable", None, None
                        except SiteAccessError as exc:
                            if seed:
                                seed.note = "Detail unavailable; using published search metadata"
                            return "blocked", seed, f"{exc}: {url}"
                        except Exception as exc:
                            if seed:
                                seed.note = "Detail unavailable; using published search metadata"
                            return (
                                "error",
                                seed,
                                f"Detail failed ({type(exc).__name__}): {url}",
                            )
                        finally:
                            close = getattr(detail, "close", None)
                            if close:
                                await close()

                    enriched = await map_bounded(selected, enrich, criteria.detail_workers)
                    blocked = 0
                    for state, job, error in enriched:
                        if job:
                            result.jobs.append(job)
                        if error:
                            result.errors.append(error)
                        if state == "unavailable":
                            result.unavailable += 1
                        elif state == "blocked":
                            blocked += 1
                    # A single stale/protected detail must not discard the rest of a
                    # working result set. Stop only when every attempted detail was blocked.
                    if blocked == len(enriched):
                        result.status = "partial" if result.jobs else "blocked"
                        return result
                    if len(selected) < len(new_links) or len(seen) >= criteria.max_per_site:
                        result.limited = True
                        return self._finish(result)
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
        if self.site == "levels":
            jobs, self._advertised_total = levels_search_data(html)
            seeds = {job.url: job for job in jobs}
            return html, list(seeds), seeds
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
            "hiringcafe": 'a[href*="/job/"]',
            "jobright": 'a[href*="/jobs/info/"]',
            "levels": 'a[href*="jobId="]',
            "trueup": 'a[href*="/job/"]',
            "yc": 'a[href*="/companies/"][href*="/jobs/"]',
            "builtin": 'a[href*="/job/"]',
        }
        try:
            await page.wait_for_selector(selectors[self.site], state="attached")
        except Exception:
            pass  # Inspect explicit empty/blocked states after the bounded wait.
        return await page.content()
