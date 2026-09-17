"""Extract navigation targets only; never submit or fill an application."""

import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit
from uuid import UUID

from bs4 import BeautifulSoup

BOARD_DOMAINS = (
    "linkedin.com",
    "indeed.com",
    "wellfound.com",
    "angel.co",
    "simplify.jobs",
    "hiring.cafe",
    "hiringcafe.com",
    "jobright.ai",
    "levels.fyi",
    "trueup.io",
    "ycombinator.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "monster.com",
    "dice.com",
    "builtin.com",
    "theorg.com",
    "talent.com",
    "jooble.org",
    "jobgether.com",
    "lensa.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "t.co",
    "tiktok.com",
    "reddit.com",
    "youtube.com",
)
ATS_DOMAINS = (
    "greenhouse.io",
    "greenhouse.com",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "myworkdaysite.com",
    "smartrecruiters.com",
    "icims.com",
    "jobvite.com",
    "bamboohr.com",
    "applytojob.com",
    "workable.com",
    "recruitee.com",
    "successfactors.com",
    "successfactors.eu",
    "oraclecloud.com",
    "teamtailor.com",
    "breezy.hr",
    "jazz.co",
    "avature.net",
    "eightfold.ai",
    "taleo.net",
)
APPLY_LABEL = re.compile(
    r"^(?:apply(?:\s+(?:now|here|for .+|on .+|to .+))?|"
    r"(?:start|begin|continue) (?:your )?application|"
    r"view (?:original |full )?(?:job|posting))(?:\s*[↗→])?$",
    re.I,
)


def host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def on_domain(url, domains):
    name = host(url)
    return any(name == d or name.endswith("." + d) for d in domains)


def web_url(url):
    try:
        parts = urlsplit(url)
        return parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.username
    except ValueError:
        return False


def simplify_application_url(url):
    """Simplify's published Apply redirect uses the public posting UUID."""
    if host(url) != "simplify.jobs":
        return None
    match = re.match(r"^/p/([^/]+)(?:/|$)", urlsplit(url).path)
    try:
        posting_id = str(UUID(match[1])) if match else None
    except ValueError:
        return None
    return f"https://simplify.jobs/jobs/click/{posting_id}" if posting_id else None


def unwrap(url):
    """Decode known board wrappers, not arbitrary query strings on employer pages."""
    for _ in range(4):
        if not on_domain(url, BOARD_DOMAINS):
            break
        query = parse_qs(urlsplit(url).query)
        target = next(
            (
                query[k][0]
                for k in ("url", "redirect", "redirectUrl", "target", "dest")
                if k in query and web_url(query[k][0])
            ),
            None,
        )
        if not target or target == url:
            break
        url = target
    return url


def extract_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    applications, employers = [], []

    def add(values, value):
        if isinstance(value, str):
            url = urljoin(base_url, value)
            if web_url(url) and url not in values:
                values.append(url)

    for anchor in soup.select("a[href]"):
        label = anchor.get_text(" ", strip=True) or anchor.get("aria-label", "")
        if APPLY_LABEL.fullmatch(label) or "apply" in str(anchor.get("data-testid", "")).lower():
            add(applications, anchor["href"])
        if re.fullmatch(r"(?:visit )?(?:company |employer )?(?:web)?site(?: ↗)?", label, re.I):
            add(employers, anchor["href"])

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in (
                    "applicationUrl",
                    "application_url",
                    "applyUrl",
                    "apply_url",
                    "applicationLink",
                    "application_link",
                ):
                    add(applications, child)
                elif key in ("hiringOrganization", "company") and isinstance(child, dict):
                    for field in ("url", "sameAs", "website", "website_url"):
                        add(employers, child.get(field))
                elif key == "jobPosting" and isinstance(child, dict):
                    # Simplify publishes its outbound application redirect on the current
                    # posting object; this is not a company profile or recommendation URL.
                    add(applications, child.get("url"))
                if key.lower() not in ("recommendations", "similarjobs", "relatedjobs"):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    scripts = 'script[type="application/ld+json"]'
    if not on_domain(base_url, ("levels.fyi",)):
        scripts += ", script#__NEXT_DATA__"
    for script in soup.select(scripts):
        try:
            walk(json.loads(script.string or script.get_text()))
        except (TypeError, ValueError):
            pass
    employers = [url for url in employers if not on_domain(url, BOARD_DOMAINS + ATS_DOMAINS)]
    return applications, employers
