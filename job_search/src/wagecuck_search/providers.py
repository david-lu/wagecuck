from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin

import httpx
from dotenv import load_dotenv

from .application_links import web_url
from .dedupe import canonical_url
from .models import JobPosting, Salary, SearchCriteria, SiteResult
from .parsing import document, job_links, parse_posting, posting_url, salary_from_text
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
    if site == "a16z":
        # Further pages load through the board's Show more jobs control.
        return "https://jobs.a16z.com/jobs?" + urlencode({"titlePrefix": query})
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
    if site == "rolesweep":
        return "https://rolesweep.com/jobs?" + urlencode(
            {"keyword": query, "page": page + 1}
        )
    if site == "remote_rocketship":
        slug = quote(re.sub(r"\s+", "-", query.strip().lower()))
        return f"https://www.remoterocketship.com/jobs/{slug}/?" + urlencode(
            {"page": page + 1, "sort": "DateAdded", "jobTitle": query}
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


def rolesweep_search_data(html: str, base_url: str) -> list[JobPosting]:
    """Parse complete RoleSweep result cards without visiting every detail page."""
    jobs = []
    for anchor in document(html).select("a[href]"):
        url = urljoin(base_url, anchor.get("href", ""))
        title_node = anchor.select_one("h2")
        if not title_node or not posting_url("rolesweep", url):
            continue
        title = title_node.get_text(" ", strip=True)
        info = next(
            (
                node.get_text(" ", strip=True)
                for node in anchor.select("div")
                if " · " in node.get_text(" ", strip=True)
                and len(node.get_text(" ", strip=True)) < 300
            ),
            "",
        )
        company, separator, location = info.partition(" · ")
        if not title or not separator or not company or not location:
            continue
        description = " ".join(
            node.get_text(" ", strip=True) for node in anchor.select("p")
        )
        labels = {node.get_text(" ", strip=True).casefold() for node in anchor.select("span")}
        mode = next((value for value in ("remote", "hybrid", "on-site") if value in labels), None)
        employment = next(
            (value for value in ("full-time", "part-time", "contract", "temporary") if value in labels),
            None,
        )
        jobs.append(
            JobPosting(
                url=url,
                title=title,
                company=company,
                location=location,
                source="rolesweep",
                salary=salary_from_text(anchor.get_text(" ", strip=True)),
                description=description,
                internship=(
                    True if re.search(r"\b(?:intern|internship|co[ -]?op)\b", title, re.I) else None
                ),
                workplace={"on-site": "onsite"}.get(mode, mode),
                employment_type=employment.replace("-", "_") if employment else None,
            )
        )
    return jobs


def remote_rocketship_search_data(html: str, base_url: str) -> list[JobPosting]:
    """Parse Remote Rocketship cards, including their published native Apply URLs."""
    soup = document(html)
    jobs = []
    seen = set()
    for anchor in soup.select("a[href]"):
        url = urljoin(base_url, anchor.get("href", ""))
        key = canonical_url(url)
        if key in seen or not posting_url("remote_rocketship", url):
            continue
        title = anchor.get_text(" ", strip=True)
        card = anchor.find_parent("div", attrs={"role": "button"})
        if not title or title.casefold() == "view job" or card is None:
            continue
        company_node = card.select_one('h4 a[href*="/company/"]')
        company = company_node.get_text(" ", strip=True) if company_node else ""
        if not company:
            continue
        text_value = card.get_text(" ", strip=True)
        paragraphs = [node.get_text(" ", strip=True) for node in card.select("p")]
        location = next((value for value in paragraphs if re.search(r"\bRemote\b", value, re.I)), "Remote")
        description = max(
            (
                value
                for value in paragraphs
                if not re.search(r"\b(?:minutes?|hours?|days?) ago\b", value, re.I)
                and value != location
            ),
            key=len,
            default="",
        )
        application_urls = [
            urljoin(base_url, node["href"])
            for node in card.select("a[href]")
            if node.get_text(" ", strip=True).casefold() == "apply"
        ]
        employer_urls = [
            urljoin(base_url, node["href"])
            for node in card.select("a[href]")
            if node.get_text(" ", strip=True).casefold() == "website"
        ]
        employment = next(
            (
                normalized
                for label, normalized in (
                    ("Full Time", "full_time"),
                    ("Part Time", "part_time"),
                    ("Contract", "contract"),
                    ("Temporary", "temporary"),
                )
                if label.casefold() in text_value.casefold()
            ),
            None,
        )
        jobs.append(
            JobPosting(
                url=url,
                title=title,
                company=company,
                location=location,
                source="remote_rocketship",
                salary=salary_from_text(text_value),
                description=description,
                internship=(
                    True if re.search(r"\b(?:intern|internship|co[ -]?op)\b", title, re.I) else None
                ),
                workplace="remote",
                employment_type=employment,
                application_urls=list(dict.fromkeys(application_urls)),
                employer_urls=list(dict.fromkeys(employer_urls)),
            )
        )
        seen.add(key)
    return jobs


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
        r"just a moment|access denied|are you (?:a )?human|pardon our interruption|"
        r"unusual traffic|automated queries",
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
                        if self.site in ("levels", "rolesweep", "remote_rocketship") and seed:
                            return "job", seed, None
                        detail = await context.new_page()
                        try:
                            await self._navigate(detail, url)
                            result.detail_visits += 1
                            await detail.wait_for_selector(
                                'h1, script[type="application/ld+json"]', state="attached"
                            )
                            job = parse_posting(
                                self.site, await detail.content(), url,
                                partial_fields=criteria.partial_fields,
                            )
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
        if self.site == "rolesweep":
            jobs = rolesweep_search_data(html, page.url)
            seeds = {job.url: job for job in jobs}
            return html, list(seeds), seeds
        if self.site == "remote_rocketship":
            jobs = remote_rocketship_search_data(html, page.url)
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
            "rolesweep": 'a[href^="/jobs/"]',
            "a16z": 'article h2 a[href^="/jobs/"]',
            "remote_rocketship": 'a[href*="/company/"][href*="/jobs/"]',
        }
        try:
            await page.wait_for_selector(selectors[self.site], state="attached")
        except Exception:
            pass  # Inspect explicit empty/blocked states after the bounded wait.
        return await page.content()


def _salary(value, *, minimum=None, maximum=None, currency=None) -> Salary | None:
    from .parsing import salary_from_text

    parsed = salary_from_text(str(value or ""))
    if parsed:
        return parsed
    if isinstance(minimum, (int, float)) or isinstance(maximum, (int, float)):
        return Salary(minimum, maximum, currency, "year", str(value) if value else None)
    return None


def backchannel_jobs(payload: dict) -> list[JobPosting]:
    jobs = []
    for value in payload.get("jobs", []):
        if not isinstance(value, dict) or not value.get("title") or not value.get("post_url"):
            continue
        description = str(value.get("summary") or "")
        jobs.append(
            JobPosting(
                url=value["post_url"],
                title=str(value["title"]),
                company=str(value.get("company") or value.get("author_name") or "Unknown"),
                location=str(value.get("location") or "Unknown"),
                source="backchannel",
                salary=_salary(value.get("salary")),
                posted_at=value.get("posted_at"),
                last_updated=value.get("extracted_at"),
                description=description,
                internship=(
                    True
                    if re.search(r"\b(?:intern|internship|co[ -]?op)\b", value["title"], re.I)
                    else None
                ),
                workplace=("remote" if re.search(r"\bremote\b", str(value.get("location")), re.I) else None),
                note=f"Apply via recruiter ({value['apply_method']})"
                if value.get("apply_method")
                else None,
            )
        )
    return jobs


def theirstack_jobs(payload: dict) -> list[JobPosting]:
    jobs = []
    for value in payload.get("data", []):
        if not isinstance(value, dict) or not value.get("job_title") and not value.get("title"):
            continue
        native = value.get("url")
        source_url = value.get("source_url") or native
        if not web_url(source_url):
            continue
        location = value.get("long_location") or value.get("short_location")
        if not location:
            location = "; ".join(str(v) for v in value.get("cities", []) if v)
        if value.get("remote") and not re.search(r"\bremote\b", str(location), re.I):
            location = "; ".join(filter(None, ("Remote", str(location or ""))))
        employment = value.get("employment_status") or value.get("employment_type")
        title = str(value.get("job_title") or value.get("title"))
        jobs.append(
            JobPosting(
                url=source_url,
                title=title,
                company=str(value.get("company") or "Unknown"),
                location=str(location or "Unknown"),
                source="theirstack",
                salary=_salary(
                    value.get("salary_string"),
                    minimum=value.get("min_annual_salary"),
                    maximum=value.get("max_annual_salary"),
                    currency=value.get("salary_currency"),
                ),
                posted_at=value.get("date_posted") or value.get("posted_at"),
                last_updated=value.get("discovered_at"),
                description=str(value.get("description") or ""),
                internship=True if employment == "internship" else None,
                employment_type=str(employment).lower().replace("-", "_") if employment else None,
                workplace="remote" if value.get("remote") else None,
                application_urls=[native] if web_url(native) and native != source_url else [],
            )
        )
    return jobs


def hn_jobs(payload: dict, query: str) -> list[JobPosting]:
    jobs = []
    for value in payload.get("hits", []):
        comment_id = value.get("objectID")
        raw = value.get("comment_text")
        if not comment_id or not raw:
            continue
        soup = document(raw)
        lines = [v.strip() for v in soup.get_text("\n", strip=True).splitlines() if v.strip()]
        if not lines:
            continue
        header = lines[0]
        parts = [v.strip() for v in re.split(r"\s*\|\s*", header) if v.strip()]
        company = parts[0][:160]
        location = next(
            (v for v in parts[1:] if re.search(r"remote|onsite|hybrid|[A-Z]{2}\b|,", v, re.I)),
            "Unknown",
        )
        phrase = re.compile(
            r"(?:senior|staff|principal|lead|junior|founding)?\s*"
            + r"\b"
            + r"\s+".join(re.escape(word) for word in query.split())
            + r"\b(?:\s+[\w+#./-]+){0,5}",
            re.I,
        )
        title = next((match[0].strip() for line in lines for match in [phrase.search(line)] if match), query.title())
        applications = []
        for anchor in soup.select("a[href]"):
            candidate = anchor.get("href")
            if web_url(candidate) and "news.ycombinator.com" not in candidate:
                applications.append(candidate)
        url = f"https://news.ycombinator.com/item?id={comment_id}"
        jobs.append(
            JobPosting(
                url=url,
                title=title,
                company=company,
                location=location,
                source="hn",
                posted_at=value.get("created_at"),
                description=" ".join(lines),
                internship=True if re.search(r"\b(?:intern|internship|co[ -]?op)\b", title, re.I) else None,
                workplace="remote" if re.search(r"\bremote\b", location, re.I) else None,
                application_urls=list(dict.fromkeys(applications)),
            )
        )
    return jobs


class JsonProvider:
    """Base for public JSON feeds that do not need one browser page per posting."""

    def __init__(self, site: str, browser=None):
        self.site = site
        self.progress = None
        self._client = None

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

    async def request(self, method, url, criteria, **kwargs):
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=criteria.timeout_seconds, follow_redirects=True
            )
        response = await self._client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class BackchannelProvider(JsonProvider):
    async def fetch(self, query: str, criteria: SearchCriteria) -> SiteResult:
        result = SiteResult(self.site)
        try:
            limit = min(criteria.max_per_site, 5000)
            payload = await self.request(
                "GET",
                "https://www.backchanneljobs.com/api/jobs?"
                + urlencode({"search": query, "sortBy": "newest", "limit": limit}),
                criteria,
            )
            result.jobs = backchannel_jobs(payload)[:limit]
            result.discovered = len(payload.get("jobs", []))
            result.advertised_total = result.discovered
            result.pages = 1
            result.limited = result.discovered >= limit
            self._progress(result)
        except Exception as exc:
            result.status = "error"
            result.errors.append(f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            await self.close()
        return result


class TheirStackProvider(JsonProvider):
    async def fetch(self, query: str, criteria: SearchCriteria) -> SiteResult:
        result = SiteResult(self.site)
        load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)
        token = os.environ.get("THEIRSTACK_API_KEY")
        if not token:
            result.status = "blocked"
            result.errors.append("THEIRSTACK_API_KEY is required; no API credits were consumed")
            return result
        try:
            offset = 0
            while offset < criteria.max_per_site and result.pages < criteria.max_pages:
                limit = min(100, criteria.max_per_site - offset)
                body = {
                    "job_title_or": [query],
                    "posted_at_max_age_days": criteria.posted_within_days or 365,
                    "is_closed": False,
                    "easy_apply": False,
                    "limit": limit,
                    "offset": offset,
                    "include_total_results": offset == 0,
                }
                payload = await self.request(
                    "POST",
                    "https://api.theirstack.com/v1/jobs/search",
                    criteria,
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )
                batch = theirstack_jobs(payload)
                result.jobs.extend(batch)
                result.discovered += len(payload.get("data", []))
                result.pages += 1
                total = payload.get("metadata", {}).get("total_results")
                if isinstance(total, int):
                    result.advertised_total = total
                self._progress(result)
                if len(payload.get("data", [])) < limit:
                    break
                offset += limit
            result.limited = bool(
                result.advertised_total and len(result.jobs) < result.advertised_total
            )
        except Exception as exc:
            result.status = "partial" if result.jobs else "error"
            result.errors.append(f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            await self.close()
        return result


class HackerNewsProvider(JsonProvider):
    async def fetch(self, query: str, criteria: SearchCriteria) -> SiteResult:
        result = SiteResult(self.site)
        try:
            stories = await self.request(
                "GET",
                "https://hn.algolia.com/api/v1/search_by_date?"
                + urlencode(
                    {
                        "tags": "story",
                        "query": "Who is hiring?",
                        "restrictSearchableAttributes": "title",
                        "hitsPerPage": 30,
                    }
                ),
                criteria,
            )
            story = next(
                (
                    hit
                    for hit in stories.get("hits", [])
                    if re.match(r"^(?:Ask HN: )?Who is hiring\?", hit.get("title", ""), re.I)
                ),
                None,
            )
            if not story:
                raise SiteAccessError("Could not locate the current Who is hiring thread")
            page = 0
            while page < criteria.max_pages and len(result.jobs) < criteria.max_per_site:
                limit = min(100, criteria.max_per_site - len(result.jobs))
                payload = await self.request(
                    "GET",
                    "https://hn.algolia.com/api/v1/search?"
                    + urlencode(
                        {
                            "tags": f"comment,story_{story['objectID']}",
                            "query": query,
                            "hitsPerPage": limit,
                            "page": page,
                        }
                    ),
                    criteria,
                )
                if page == 0:
                    result.advertised_total = payload.get("nbHits")
                batch = hn_jobs(payload, query)
                result.jobs.extend(batch)
                result.discovered += len(payload.get("hits", []))
                result.pages += 1
                self._progress(result)
                page += 1
                if page >= payload.get("nbPages", 0) or not payload.get("hits"):
                    break
            result.limited = bool(
                result.advertised_total and len(result.jobs) < result.advertised_total
            )
        except Exception as exc:
            result.status = "partial" if result.jobs else "error"
            result.errors.append(f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            await self.close()
        return result


class VentureLoopProvider(JsonProvider):
    async def fetch(self, query: str, criteria: SearchCriteria) -> SiteResult:
        result = SiteResult(self.site)
        try:
            payload = await self.request(
                "GET",
                "https://ventureloop.com/api/jobs/search?" + urlencode({"query": query}),
                criteria,
            )
            result.pages = 1
            result.advertised_total = payload.get("totalJobCount")
            result.discovered = len(payload.get("jobs", []))
            unlocked = [job for job in payload.get("jobs", []) if not job.get("accessRestricted")]
            if not unlocked:
                result.status = "blocked"
                result.limited = bool(result.advertised_total)
                result.errors.append(
                    "Anonymous search exposes counts and previews; company and application URLs require an account"
                )
            self._progress(result)
        except Exception as exc:
            result.status = "error"
            result.errors.append(f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            await self.close()
        return result


class UnavailableProvider:
    def __init__(self, site: str, browser, reason: str):
        self.site = site
        self.reason = reason
        self.progress = None

    async def fetch(self, query: str, criteria: SearchCriteria) -> SiteResult:
        return SiteResult(self.site, status="blocked", errors=[self.reason])


def provider_for(site: str, browser):
    if site == "a16z":
        from .a16z import A16zProvider

        return A16zProvider(site, browser)
    if site == "simplify":
        from .simplify import SimplifyProvider

        return SimplifyProvider(site, browser)
    if site == "theirstack":
        return TheirStackProvider(site, browser)
    if site == "backchannel":
        return BackchannelProvider(site, browser)
    if site == "hn":
        return HackerNewsProvider(site, browser)
    if site == "ventureloop":
        return VentureLoopProvider(site, browser)
    unavailable = {
        "jobshifu": "JobShifu search requires an authenticated account",
        "mygreenhouse": "MyGreenhouse discovery requires an authenticated account",
        "google": "Google Jobs returned an automated-traffic verification challenge",
    }
    if site in unavailable:
        return UnavailableProvider(site, browser, unavailable[site])
    return BrowserProvider(site, browser)
