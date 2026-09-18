"""Fast validation through public ATS posting APIs."""

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit


@dataclass(frozen=True)
class AtsPosting:
    url: str
    title: str
    company: str | None = None
    description: str = ""


def api_target(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = [value for value in parts.path.split("/") if value]
    query = parse_qs(parts.query)

    if host.endswith("greenhouse.io") or host.endswith("greenhouse.com"):
        board = query.get("for", [None])[0]
        posting_id = query.get("token", [None])[0] or query.get("gh_jid", [None])[0]
        if "jobs" in path:
            index = path.index("jobs")
            board = board or (path[index - 1] if index else None)
            posting_id = posting_id or (path[index + 1] if len(path) > index + 1 else None)
        if board and posting_id:
            return "greenhouse", (
                "https://boards-api.greenhouse.io/v1/boards/"
                f"{quote(board, safe='')}/jobs/{quote(posting_id, safe='')}?questions=true"
            )

    if host in ("jobs.lever.co", "jobs.eu.lever.co") and len(path) >= 2:
        api_host = "api.eu.lever.co" if host.startswith("jobs.eu.") else "api.lever.co"
        return "lever", (
            f"https://{api_host}/v0/postings/{quote(path[0], safe='')}/"
            f"{quote(path[1], safe='')}"
        )

    if host == "jobs.ashbyhq.com" and path:
        return "ashby", (
            "https://api.ashbyhq.com/posting-api/job-board/"
            f"{quote(path[0], safe='')}"
        )

    if host.endswith("smartrecruiters.com") and len(path) >= 2:
        match = re.match(r"(\d+)(?:-|$)", path[1]) or re.match(
            r"([0-9a-fA-F-]{36})(?:-|$)", path[1]
        )
        if match:
            return "smartrecruiters", (
                "https://api.smartrecruiters.com/v1/companies/"
                f"{quote(path[0], safe='')}/postings/{quote(match.group(1), safe='')}"
            )
    return None


class AtsApiVerifier:
    def __init__(self, client):
        self.client = client
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(8)

    async def _payload(self, url):
        async with self.lock:
            task = self.tasks.get(url)
            if task is None:
                task = asyncio.create_task(self._fetch(url))
                self.tasks[url] = task
        try:
            return await task
        except Exception:
            async with self.lock:
                if self.tasks.get(url) is task:
                    self.tasks.pop(url, None)
            raise

    async def _fetch(self, url):
        async with self.semaphore:
            response = await self.client.get(url)
        try:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        finally:
            await response.aclose()

    async def verify(self, url):
        target = api_target(url)
        if target is None:
            return None
        kind, endpoint = target
        payload = await self._payload(endpoint)
        if payload is None:
            return AtsPosting("", "")

        if kind == "greenhouse":
            return AtsPosting(
                url,
                str(payload.get("title", "")),
                str(payload.get("company_name", "")) or None,
                str(payload.get("content") or ""),
            )
        if kind == "lever":
            return AtsPosting(
                str(payload.get("applyUrl") or url),
                str(payload.get("text", "")),
                description="\n".join([
                    str(payload.get("description") or payload.get("descriptionPlain") or ""),
                    *(str(section.get("content") or "") for section in payload.get("lists", [])),
                    str(payload.get("additional") or ""),
                ]),
            )
        if kind == "smartrecruiters":
            company = payload.get("company") or {}
            return AtsPosting(
                str(payload.get("applyUrl") or url),
                str(payload.get("name", "")),
                str(company.get("name", "")) or None,
                "\n".join(
                    str(section.get("text") or "")
                    for section in (payload.get("jobAd") or {}).get("sections", {}).values()
                    if isinstance(section, dict)
                ),
            )

        # Ashby exposes one public document per job board. Match the exact hosted
        # job/apply URL so a similarly titled posting cannot be substituted.
        clean = url.rstrip("/").split("?", 1)[0]
        for posting in payload.get("jobs", []):
            job_url = str(posting.get("jobUrl", "")).rstrip("/").split("?", 1)[0]
            apply_url = str(posting.get("applyUrl", "")).rstrip("/").split("?", 1)[0]
            if clean in (job_url, apply_url) or clean.removesuffix("/application") in (
                job_url,
                apply_url.removesuffix("/apply"),
            ):
                return AtsPosting(
                    str(posting.get("applyUrl") or url),
                    str(posting.get("title", "")),
                    description=str(
                        posting.get("descriptionHtml") or posting.get("descriptionPlain") or ""
                    ),
                )
        return AtsPosting("", "")
