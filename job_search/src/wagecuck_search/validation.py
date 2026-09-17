from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin

from .application_links import (
    APPLY_LABEL,
    ATS_DOMAINS,
    BOARD_DOMAINS,
    extract_links,
    host,
    on_domain,
    simplify_application_url,
    unwrap,
    web_url,
)
from .matching import normalized, parse_date
from .models import JobPosting, utc_now
from .parsing import document, structured_jobs, text
from .providers import access_problem


@dataclass
class ValidationResult:
    url: str | None = None
    kind: str | None = None
    reason: str | None = None
    checked_at: str | None = None


@dataclass
class ValidationPlan:
    job: JobPosting
    targets: list[tuple[str, int]]
    employer_urls: list[str]
    remaining_seconds: float


def company_key(value):
    return re.sub(
        r"\b(?:inc|incorporated|llc|ltd|limited|corp|corporation|co)\b", "", normalized(value)
    ).strip()


def same_company(expected, actual):
    expected, actual = company_key(expected), company_key(actual)
    return bool(expected) and f" {expected} " in f" {actual} "


def destination_problem(job, html, url, status, employer_urls):
    """Require live, role-specific employer/ATS evidence, not just HTTP 200."""
    problem = access_problem(html, url, status)
    if problem:
        return problem, None
    if status is None or not 200 <= status < 300:
        return "Destination did not return a successful response", None
    if not web_url(url) or on_domain(url, BOARD_DOMAINS):
        return "Destination is a job board or social-media page", None
    soup = document(html)
    for node in soup.select("script, style, nav, footer"):
        if node.name != "script" or node.get("type") != "application/ld+json":
            node.decompose()
    visible = soup.get_text(" ", strip=True)
    headings = " ".join(n.get_text(" ", strip=True) for n in soup.select("h1, h2, [role=heading]"))
    if re.search(
        r"(?:job|position|posting|role|requisition) (?:is |has been |was )?"
        r"(?:no longer (?:available|open)|closed|filled|removed|not found)|"
        r"no longer accepting applications|sorry.{0,50}(?:job|position).{0,25}"
        r"(?:unavailable|not available)|page not found",
        visible,
        re.I,
    ):
        return "Application is closed or missing", None
    records = structured_jobs(soup)
    relevant = [r for r in records if normalized(text(r.get("title"))) == normalized(job.title)]
    for record in relevant:
        expiry = parse_date(record.get("validThrough"))
        if expiry and expiry < utc_now():
            return "Application is expired", None
    title_headings = soup.select("h1") or soup.select("h2, [role=heading]")
    title_found = any(
        f" {normalized(job.title)} " in f" {normalized(h.get_text(' ', strip=True))} "
        for h in title_headings
    )
    if len(records) > 1 or (not title_found and not relevant):
        return "Destination does not identify the requested role", None
    employer_hosts = [
        host(v) for v in employer_urls if not on_domain(v, BOARD_DOMAINS + ATS_DOMAINS)
    ]
    employer_host = any(host(url) == h or host(url).endswith("." + h) for h in employer_hosts)
    company_found = same_company(job.company, headings)
    for record in relevant:
        organization = record.get("hiringOrganization", {})
        if isinstance(organization, dict) and same_company(
            job.company, organization.get("name", "")
        ):
            company_found = True
    # ATS pages often put the employer brand in the logo's accessible name or title.
    brand = " ".join([text(soup.title), *(str(n.get("alt", "")) for n in soup.select("img[alt]"))])
    company_found = company_found or same_company(job.company, brand)
    kind = "employer" if employer_host else "ats" if on_domain(url, ATS_DOMAINS) else None
    if kind is None or not (employer_host or company_found):
        return "Could not verify that the destination belongs to this employer", None
    applications, _ = extract_links(html, url)
    native_apply = any(
        not on_domain(unwrap(v), BOARD_DOMAINS)
        and (
            on_domain(unwrap(v), ATS_DOMAINS)
            or any(
                host(unwrap(v)) == h or host(unwrap(v)).endswith("." + h) for h in employer_hosts
            )
        )
        for v in applications
    )
    form = soup.select_one(
        "input[type=email], input[type=file], input[autocomplete=email], "
        'iframe[src*="greenhouse"], iframe[src*="lever.co"]'
    )
    if form:
        owner = form.find_parent("form")
        if owner and owner.get("action") and on_domain(owner["action"], BOARD_DOMAINS):
            form = None
    direct_apply = any(record.get("directApply") is True for record in relevant)
    if not (native_apply or form or direct_apply):
        return "Destination has no application entry point", None
    return None, kind


class BrowserValidator:
    def __init__(self, browser, criteria, *, browser_factory=None):
        self.browser, self.criteria = browser, criteria
        self.browser_factory = browser_factory
        self.http_hosts = {}

    async def recycle(self):
        """Release browser processes between batches, when this validator owns them."""
        if self.browser_factory:
            await self.browser.close()
            self.browser = await self.browser_factory()

    async def close(self):
        await self.browser.close()

    async def _http_document(self, request, candidate):
        # A board's outbound redirect must release its host slot before waiting
        # on an unrelated employer. Otherwise two slow employers stall the board.
        seen = set()
        while web_url(candidate) and candidate not in seen and len(seen) < 10:
            seen.add(candidate)
            limiter = self.http_hosts.setdefault(host(candidate), asyncio.Semaphore(2))
            async with limiter:
                response = await request.get(
                    candidate, timeout=self.criteria.timeout_seconds * 1000, max_redirects=0
                )
                try:
                    final, status = response.url, response.status
                    if status in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            return await response.text(), final, status
                        candidate = urljoin(final, location)
                    else:
                        return await response.text(), final, status
                finally:
                    await response.dispose()
        raise ValueError("Invalid or looping application redirect")

    async def _http_load(self, context, candidate, job, employer_urls):
        """Use server HTML when it provides enough evidence; render only when necessary."""
        request = getattr(context, "request", None)
        if request is None:
            return None
        try:
            html, final, status = await self._http_document(request, candidate)
            if status in (404, 410, 429):
                return html, final, status, False
            if status < 200 or status >= 300 or access_problem(html, final, status):
                return html, final, status, True
            applications, _ = extract_links(html, final)
            if on_domain(final, BOARD_DOMAINS) and applications:
                return html, final, status, False
            if not on_domain(final, BOARD_DOMAINS):
                reason, _ = destination_problem(job, html, final, status, employer_urls)
                if reason is None or reason in (
                    "Application is closed or missing",
                    "Application is expired",
                ):
                    return html, final, status, False
            # Preserve the resolved URL even when server HTML needs rendering.
            # Replaying the board redirect in a browser duplicates network work.
            return html, final, status, True
        except Exception:
            pass
        return None

    def _render_complete(self, job, html, final, status, employer_urls):
        if status in (404, 410, 429):
            return True
        if on_domain(final, BOARD_DOMAINS):
            return bool(extract_links(html, final)[0])
        reason, _ = destination_problem(job, html, final, status, employer_urls)
        return reason in (None, "Application is closed or missing", "Application is expired")

    async def _render(self, page, candidate, job, employer_urls):
        response = await page.goto(candidate, wait_until="domcontentloaded")
        # Observe application evidence instead of waiting for analytics/ads to
        # become network-idle on every navigation. Keep the hydration allowance.
        for delay in (0, 250, 500, 750, 1000):
            if delay:
                await page.wait_for_timeout(delay)
            html, final = await page.content(), page.url
            status = response.status if response else None
            if response and response.url != final.split("#")[0]:
                response = await page.goto(final, wait_until="domcontentloaded")
                status = response.status if response else None
                html, final = await page.content(), page.url
            if self._render_complete(job, html, final, status, employer_urls):
                break
        return html, final, status

    async def validate(self, job):
        return await self._validate(job)

    async def prepare(self, job):
        """Finish HTTP-only checks, or return a small browser continuation."""
        return await self._validate(job, http_only=True)

    async def finish(self, plan):
        return await self._validate(plan.job, plan=plan)

    async def _validate(self, job, *, http_only=False, plan=None):
        context = None
        started = time.monotonic()
        budget = plan.remaining_seconds if plan else self.criteria.validation_timeout_seconds
        try:
            async with asyncio.timeout(budget):
                context = await self.browser.new_context()
                context.set_default_timeout(self.criteria.timeout_seconds * 1000)
                page = None
                # Establish employer provenance before trying a custom careers-domain link.
                sources = [(s["url"], 0) for s in job.sources]
                applications = [(v, 0) for v in job.application_urls]
                pending = applications + sources if job.employer_urls else sources + applications
                shortcuts = {
                    link for source, _ in sources
                    if (link := simplify_application_url(source))
                }
                pending = [(v, 0) for v in sorted(shortcuts)] + pending
                source_urls = {url for url, _ in sources}
                employer_urls = list(job.employer_urls)
                render_targets = []
                render_first = set()
                if plan:
                    pending = list(plan.targets)
                    employer_urls = list(plan.employer_urls)
                    render_first = {url for url, _ in pending}
                visited = set()
                reason = "No employer application link found"
                while pending and len(visited) < 10:
                    candidate, depth = pending.pop(0)
                    candidate = unwrap(candidate)
                    if not web_url(candidate) or candidate in visited or depth > 4:
                        continue
                    visited.add(candidate)
                    try:
                        fetched = (
                            None if candidate in render_first
                            else await self._http_load(context, candidate, job, employer_urls)
                        )
                        if fetched:
                            html, final, status, needs_render = fetched
                            if candidate in shortcuts and on_domain(final, ATS_DOMAINS):
                                # An ATS must independently identify the company and role.
                                # Loading Simplify's full page adds no evidence for that check.
                                pending = [(v, d) for v, d in pending if v not in source_urls]
                        else:
                            final, needs_render = candidate, True
                        loaded = not needs_render
                        if needs_render:
                            if http_only:
                                render_targets.append((final, depth))
                                if fetched and on_domain(final, BOARD_DOMAINS):
                                    _, employers = extract_links(html, final)
                                    employer_urls.extend(employers)
                                continue
                            if page is None:
                                if hasattr(context, "route"):
                                    # Match asset URLs in the browser so scripts, styles,
                                    # and API calls do not need a Python route round-trip.
                                    await context.route(
                                        re.compile(
                                            r"\.(?:png|jpe?g|gif|webp|ico|svg|woff2?|ttf|"
                                            r"otf|mp4|webm|mp3)(?:\?|$)", re.I
                                        ),
                                        lambda route: route.abort(),
                                    )
                                page = await context.new_page()
                            html, final, status = await self._render(
                                page, final, job, employer_urls
                            )
                        visited.add(final)
                        problem = access_problem(html, final, status)
                        if problem:
                            reason = problem
                            continue
                        applications, employers = extract_links(html, final)
                        # Employer identity must originate at a known discovery source, not
                        # be self-asserted by an arbitrary external destination.
                        if on_domain(final, BOARD_DOMAINS):
                            employer_urls.extend(employers)
                        if not on_domain(final, BOARD_DOMAINS):
                            reason, kind = destination_problem(
                                job, html, final, status, employer_urls
                            )
                            if reason is None:
                                return ValidationResult(
                                    final, kind, checked_at=utc_now().isoformat()
                                )
                        pending.extend((v, depth + 1) for v in applications if v not in visited)
                        if not applications and not loaded:
                            # Follow a board's navigation button, never a form submit button.
                            buttons = page.get_by_role("button", name=APPLY_LABEL)
                            for index in range(min(await buttons.count(), 3)):
                                button = buttons.nth(index)
                                if not await button.is_visible() or not await button.is_enabled():
                                    continue
                                if await button.evaluate("el => !!el.form"):
                                    continue
                                previous = set(context.pages)
                                before = page.url
                                await button.click()
                                await page.wait_for_timeout(1000)
                                for opened in context.pages:
                                    if opened not in previous:
                                        await opened.wait_for_load_state("domcontentloaded")
                                        pending.append((opened.url, depth + 1))
                                        await opened.close()
                                if page.url != before:
                                    pending.append((page.url, depth + 1))
                                else:
                                    links, _ = extract_links(await page.content(), page.url)
                                    pending.extend((v, depth + 1) for v in links)
                                break
                    except Exception as exc:
                        reason = f"Destination could not be loaded ({type(exc).__name__})"
                if http_only and render_targets:
                    return ValidationPlan(
                        job, render_targets, employer_urls,
                        max(0, budget - (time.monotonic() - started)),
                    )
                return ValidationResult(reason=reason or "No validated employer application found")
        except TimeoutError:
            return ValidationResult(reason="Application URL validation timed out")
        except Exception as exc:
            return ValidationResult(
                reason=f"Application URL validation failed ({type(exc).__name__})"
            )
        finally:
            if context:
                await context.close()
