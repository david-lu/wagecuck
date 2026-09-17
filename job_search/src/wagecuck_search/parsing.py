from __future__ import annotations

import json
import math
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .application_links import extract_links
from .dedupe import canonical_url
from .models import JobPosting, Salary

LINK_PATTERNS = {
    "wellfound": r"/jobs/\d+",
    "indeed": r"/(?:viewjob|rc/clk|pagead/clk)\?[^#]*(?:jk|vjk)=",
    "linkedin": r"/jobs/view/[^/?]+",
    "simplify": r"/p/[a-zA-Z0-9-]+",
    "hiringcafe": r"/job/[a-zA-Z0-9-]+",
    "jobright": r"/jobs/info/[a-fA-F0-9]+",
    "levels": r"/jobs(?:/title/[^/?#]+)?\?[^#]*\bjobId=\d+",
    "trueup": r"/(?:job|jobs)/[a-zA-Z0-9-]+",
    "yc": r"/companies/[^/?#]+/jobs/[^/?#]+",
    "builtin": r"/job/[^/?#]+/\d+",
}
DOMAINS = {
    "wellfound": ("wellfound.com",),
    "indeed": ("indeed.com",),
    "linkedin": ("linkedin.com",),
    "simplify": ("simplify.jobs",),
    "hiringcafe": ("hiring.cafe", "hiringcafe.com"),
    "jobright": ("jobright.ai",),
    "levels": ("levels.fyi",),
    "trueup": ("trueup.io",),
    "yc": ("ycombinator.com",),
    "builtin": ("builtin.com",),
}


def text(value) -> str:
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)


def document(html: str):
    return BeautifulSoup(html, "html.parser")


def posting_url(site: str, url: str) -> bool:
    host = urlsplit(url).hostname or ""
    accepted = DOMAINS[site]
    return any(host == domain or host.endswith("." + domain) for domain in accepted) and bool(
        re.search(LINK_PATTERNS[site], url)
    )


def job_links(site: str, html: str, base_url: str) -> list[str]:
    found = {}
    for anchor in document(html).select("a[href]"):
        url = urljoin(base_url, anchor["href"])
        if posting_url(site, url):
            found.setdefault(canonical_url(url), url)
    return list(found.values())


def structured_jobs(soup) -> list[dict]:
    found = []

    def walk(value):
        if isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                found.append(value)
            else:
                for child in value.values():
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue
    return found


def number(value) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def salary_from_schema(value) -> Salary | None:
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, dict)), {})
    if not isinstance(value, dict):
        return None
    amount = value.get("value", {})
    if isinstance(amount, (int, float, str)):
        amount = {"value": amount}
    if not isinstance(amount, dict):
        return None
    minimum = number(amount.get("minValue", amount.get("value")))
    maximum = number(amount.get("maxValue", amount.get("value")))
    if minimum is None and maximum is None:
        return None
    period = str(amount.get("unitText", "")).lower()
    period = {
        "annually": "year",
        "yearly": "year",
        "annual": "year",
        "hourly": "hour",
        "monthly": "month",
    }.get(period, period)
    return Salary(minimum, maximum, value.get("currency"), period or None)


def salary_from_text(value: str) -> Salary | None:
    # A bare dollar sign has unknown currency, not an assumed USD value.
    match = re.search(
        r"(?P<currency>USD|CAD|GBP|EUR|US\$|CA\$|C\$|\$|£|€)\s*"
        r"(?P<low>\d[\d,]*(?:\.\d+)?)(?P<lk>[kK]?)"
        r"(?:\s*(?:-|–|—|to|and)\s*(?:USD|CAD|GBP|EUR|US\$|CA\$|C\$|\$|£|€)?\s*"
        r"(?P<high>\d[\d,]*(?:\.\d+)?)(?P<hk>[kK]?))?"
        r"(?P<suffix>\s*(?:USD|CAD|GBP|EUR)?(?:\s*(?:(?:a|per)\s+|/\s*)?"
        r"(?:year|yr|annum|annually|hour|hr|month|week|day)\b)?)",
        value,
        re.I,
    )
    if not match:
        return None
    low = float(match["low"].replace(",", "")) * (1000 if match["lk"] else 1)
    high = (
        float(match["high"].replace(",", "")) * (1000 if match["hk"] else 1)
        if match["high"]
        else None
    )
    currency = {"US$": "USD", "CA$": "CAD", "C$": "CAD", "£": "GBP", "€": "EUR"}.get(
        match["currency"].upper(), match["currency"].upper()
    )
    suffix = match["suffix"].lower()
    if currency == "$":
        explicit = re.search(r"\b(USD|CAD|GBP|EUR)\b", suffix, re.I)
        currency = explicit[1].upper() if explicit else None
    period = next(
        (
            p
            for p, pattern in {
                "year": r"year|yr|annum|annually",
                "hour": r"hour|hr",
                "month": r"month",
                "week": r"week",
                "day": r"day",
            }.items()
            if re.search(pattern, suffix)
        ),
        None,
    )
    return Salary(low, high, currency, period, match[0].strip())


def sponsorship(value: str) -> bool | None:
    negative = r"\b(?:no|without|not (?:offer|provide|available)|unable to (?:offer|provide)|cannot|can't|will not|do not|does not)\b[^.\n]{0,65}(?:visa|sponsor)|(?:visa sponsorship|sponsorship)[\s:–-]*(?:not available|unavailable|not provided|no\b)|(?:visa sponsorship|sponsorship)\s+(?:is\s+)?not (?:available|offered|provided)"
    positive = r"(?:visa sponsorship|sponsorship)[\s:–-]*(?:available|provided|offered|yes\b)|(?:offer|provide|offers|provides)\s+(?:work\s+)?visa sponsorship|(?:visa sponsorship)\s+is\s+available"
    if re.search(negative, value, re.I):
        return False
    if re.search(positive, value, re.I):
        return True
    return None


def workplace(value: str) -> str | None:
    if re.search(r"\bhybrid\b", value, re.I):
        return "hybrid"
    if re.search(r"\b(?:not remote|no remote|on[ -]?site only|fully on[ -]?site)\b", value, re.I):
        return "onsite"
    if re.search(r"\b(?:remote|telecommute|work from home)\b", value, re.I):
        return "remote"
    if re.search(r"\b(?:on[ -]?site|in person)\b", value, re.I):
        return "onsite"
    return None


def location_from_schema(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "; ".join(filter(None, (location_from_schema(v) for v in value)))
    if not isinstance(value, dict):
        return ""
    address = value.get("address", value)
    if isinstance(address, str):
        return address
    if not isinstance(address, dict):
        return ""
    parts = []
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        entry = address.get(key)
        if isinstance(entry, dict):
            entry = entry.get("name")
        if entry:
            parts.append(str(entry))
    return ", ".join(parts) or str(value.get("name", ""))


def first_text(soup, selectors: str) -> str:
    node = soup.select_one(selectors)
    return node.get_text(" ", strip=True) if node else ""


def parse_posting(site: str, html: str, url: str) -> JobPosting | None:
    application_urls, employer_urls = extract_links(html, url)
    soup = document(html)
    jobs = structured_jobs(soup)
    # Ignore recommendation JSON-LD when another posting is being visited.
    data = next(
        (
            v
            for v in jobs
            if v.get("url") and canonical_url(urljoin(url, v["url"])) == canonical_url(url)
        ),
        None,
    )
    if data is None:
        data = next((v for v in jobs if not v.get("url")), {})
    title = text(data.get("title")) or first_text(soup, "h1")
    if not title:
        return None
    company = data.get("hiringOrganization", {})
    company = company.get("name", "") if isinstance(company, dict) else company
    company_selectors = {
        "linkedin": ".topcard__org-name-link, .topcard__flavor a",
        "indeed": '[data-testid="inlineHeader-companyName"], [data-company-name="true"]',
        "wellfound": 'a[href^="/company/"]',
        "simplify": 'a[href^="/c/"]',
        "hiringcafe": 'a[href*="/company/"], [class*="company"]',
        "jobright": 'a[href*="/company/"], [data-testid*="company"], [class*="company"]',
        "levels": 'a[href*="/companies/"], [class*="company"]',
        "trueup": 'a[href*="/company/"], [class*="company"]',
        "yc": 'a[href^="/companies/"]:not([href*="/jobs/"]), [class*="company"]',
        "builtin": 'a[href*="/company/"], [data-id="company-title"], [class*="company"]',
    }
    company = text(company) or first_text(soup, company_selectors[site])
    if not company:
        return None  # Do not emit an unrelated h1 as a job.
    description = text(data.get("description")) or first_text(
        soup,
        '#jobDescriptionText, .show-more-less-html__markup, [itemprop="description"], '
        '[class*="jobDescription"], [data-testid="job-description"]',
    )
    # Metadata before the related-jobs section is useful for explicit visa/workplace labels.
    for node in soup.select("script, style, nav, footer"):
        node.decompose()
    visible = soup.get_text(" ", strip=True)
    visible = re.split(r"Similar Jobs|Similar jobs|People also viewed|Related jobs", visible)[0]
    description = description or visible
    location = location_from_schema(data.get("jobLocation"))
    if not location:
        location = first_text(
            soup,
            '.topcard__flavor--bullet, [data-testid="job-location"], '
            '[data-testid="inlineHeader-companyLocation"], '
            '[data-testid="jobsearch-JobInfoHeader-companyLocation"], '
            '[itemprop="jobLocation"], [data-testid*="location"], [class*="location"]',
        )
    remote = data.get("jobLocationType") == "TELECOMMUTE"
    restrictions = location_from_schema(data.get("applicantLocationRequirements"))
    if remote:
        location = "; ".join(filter(None, ["Remote", restrictions or location]))
    work_mode = "remote" if remote else workplace(location)
    if work_mode is None:
        policy = re.search(r"Remote Work Policy\s+(.{0,80})", visible, re.I)
        if policy:
            work_mode = workplace(policy[1])
    if not location and work_mode == "remote":
        location = "Remote"
    employment = data.get("employmentType", "")
    if isinstance(employment, list):
        employment = " ".join(employment)
    employment = str(employment).lower().replace("-", "_").replace(" ", "_")
    internship = True if re.search(r"\b(?:intern|internship|co[ -]?op)\b", title, re.I) else None
    if "intern" in employment:
        internship = True
    elif internship is None and employment in ("full_time", "part_time", "contractor", "temporary"):
        internship = False
    employment = {"contractor": "contract"}.get(employment, employment) or None
    visa_text = description + " " + visible[:2500]
    if site == "simplify":
        visa_text = re.sub(
            r"Company (?:Does Not Provide|Provides?) H1B Sponsorship", "", description, flags=re.I
        )
    elif site == "jobright":
        # Jobright appends company-level historical filing data and explicitly says it
        # is not a promise for the role. Keep only the job-specific text before that block.
        visa_text = re.split(r"Company H-?1B Sponsorship", visa_text, flags=re.I)[0]
    return JobPosting(
        url=url,
        title=title,
        company=company,
        location=location or "Unknown",
        source=site,
        application_urls=application_urls,
        employer_urls=employer_urls,
        salary=salary_from_schema(data.get("baseSalary")) or salary_from_text(description),
        last_updated=data.get("dateModified"),
        posted_at=data.get("datePosted"),
        valid_through=data.get("validThrough"),
        internship=internship,
        sponsors_visa=sponsorship(visa_text),
        description=description,
        employment_type=employment,
        workplace=work_mode,
        note="Location not published" if not location else None,
    )
