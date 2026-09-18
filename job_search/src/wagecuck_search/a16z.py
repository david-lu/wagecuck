"""Public a16z portfolio jobs, using the board's rendered search and pagination."""

import json
import re
from urllib.parse import urljoin

from .application_links import web_url
from .dedupe import canonical_url
from .models import JobPosting
from .parsing import document, job_links, posting_url, salary_from_text, workplace
from .providers import BrowserProvider, search_url


def search_data(html, base_url):
    soup = document(html)
    total = None
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "ItemList":
            count = data.get("numberOfItems")
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                total = count
    jobs = {}
    for card in soup.select("article"):
        anchor = card.select_one("h2 a[href]")
        company_node = card.select_one("[data-nosnippet] a[href]")
        if not anchor or not company_node:
            continue
        url = urljoin(base_url, anchor["href"])
        if not posting_url("a16z", url):
            continue
        title = anchor.get_text(" ", strip=True)
        company = company_node.get_text(" ", strip=True)
        if not title or not company:
            continue
        metadata = anchor.find_parent("h2").find_next_sibling("div")
        locations, salary = [], None
        for item in metadata.find_all("span", recursive=False) if metadata else []:
            if item.find("time"):
                continue
            value = item.get_text(" ", strip=True).strip(" ·")
            compensation = salary_from_text(value)
            if compensation:
                salary = compensation
            elif value:
                locations.append(value)
        location = "; ".join(locations) or "Unknown"
        posted = card.select_one("time[datetime]")
        applications = [
            urljoin(base_url, link["href"])
            for link in card.select("a[href]")
            if link.get_text(" ", strip=True).casefold() == "apply"
            and web_url(urljoin(base_url, link["href"]))
        ]
        jobs.setdefault(
            canonical_url(url),
            JobPosting(
                url=url,
                title=title,
                company=company,
                location=location,
                source="a16z",
                salary=salary,
                posted_at=posted["datetime"] if posted else None,
                workplace=workplace(location),
                internship=True
                if re.search(r"\b(?:intern|internship|co[ -]?op)\b", title, re.I)
                else None,
                application_urls=list(dict.fromkeys(applications)),
            ),
        )
    return list(jobs.values()), total


class A16zProvider(BrowserProvider):
    async def _discover(self, page, query, page_number):
        if page_number == 0:
            self._advertised_total = None
            await self._navigate(page, search_url(self.site, query))
            html = await self._listing_html(page)
        else:
            more = page.get_by_role("button", name="Show more jobs", exact=True)
            if not await more.count():
                return await page.content(), [], {}
            count = await page.locator("article h2 a").count()
            await more.click()
            await page.wait_for_function(
                "previous => document.querySelectorAll('article h2 a').length > previous",
                arg=count,
            )
            html = await page.content()
        jobs, total = search_data(html, page.url)
        if total is not None:
            self._advertised_total = total
        return html, job_links(self.site, html, page.url), {job.url: job for job in jobs}

    @staticmethod
    def _enrich_seed(seed, detail):
        # Detail pages disclose workplace restrictions absent from compact cards.
        location = detail.location if detail and detail.location != "Unknown" else None
        job = BrowserProvider._enrich_seed(seed, detail)
        if location:
            job.location = location
        job.application_urls = list(dict.fromkeys([*job.application_urls, *seed.application_urls]))
        return job
