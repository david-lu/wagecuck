"""Read the public search response the Simplify job-board UI itself requests."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

from .matching import matches
from .models import JobPosting, Salary, SiteResult
from .parsing import number, parse_posting, workplace
from .providers import BrowserProvider, PostingUnavailable, SiteAccessError
from .workers import map_bounded


def search_response(response, query, page_number):
    parts = urlsplit(response.url)
    if not (parts.hostname or "").endswith(".simplify.jobs") or parts.path != "/multi_search":
        return False
    try:
        searches = response.request.post_data_json.get("searches", [])
        return any(s.get("q") == query and s.get("page") == page_number + 1 for s in searches)
    except (AttributeError, ValueError):
        return False


def timestamp(value):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def search_postings(payload):
    found = {}
    for result in payload.get("results", []):
        hits = list(result.get("hits", []))
        for group in result.get("grouped_hits", []):
            hits.extend(group.get("hits", []))
        for hit in hits:
            item = hit.get("document", {})
            try:
                posting_id = str(UUID(item.get("posting_id", "")))
            except (ValueError, TypeError, AttributeError):
                continue
            if not item.get("title") or not item.get("company_name"):
                continue
            url = "https://simplify.jobs/p/" + posting_id
            experience = {
                "Senior": "senior",
                "Staff": "staff",
                "Principal": "principal",
                "Mid Level": "mid",
                "Junior": "junior",
                "Internship": "intern",
            }
            employment = str(item.get("type", "")).lower().replace("-", "_")
            mode = workplace(item.get("travel_requirements", ""))
            location = "; ".join(item.get("locations", []))
            if mode == "remote":
                location = "; ".join(filter(None, ["Remote", location]))
            minimum, maximum = number(item.get("min_salary")), number(item.get("max_salary"))
            salary = None
            if minimum is not None or maximum is not None:
                # Numeric period enums are not guessed; detail JSON-LD supplies the period.
                salary = Salary(minimum, maximum, item.get("currency_type"))
            found[url] = JobPosting(
                url=url,
                title=item["title"],
                company=item["company_name"],
                location=location or "Unknown",
                source="simplify",
                salary=salary,
                workplace=mode,
                employment_type=employment or None,
                internship=(
                    True
                    if employment == "internship"
                    else False
                    if employment in ("full_time", "part_time", "contract")
                    else None
                ),
                posted_at=timestamp(item.get("start_date")),
                last_updated=timestamp(item.get("updated_date")),
                experience_levels=tuple(
                    experience[v] for v in item.get("experience_level", []) if v in experience
                ),
                # Company H1B history does not establish sponsorship for this particular job.
            )
    return found


class SimplifyProvider(BrowserProvider):
    async def fetch(self, query, criteria):
        result = SiteResult(self.site)
        context = None
        try:
            async with asyncio.timeout(criteria.site_timeout_seconds):
                context = await self.browser.new_context()
                context.set_default_timeout(criteria.timeout_seconds * 1000)
                page = await context.new_page()
                await self._navigate(page, "https://simplify.jobs/jobs")
                async with page.expect_response(lambda r: search_response(r, query, 0)) as event:
                    field = page.locator('input[placeholder="Find your dream role"]')
                    await field.fill(query)
                    await field.press("Enter")
                observed = await event.value
                if observed.status != 200:
                    raise SiteAccessError(f"Search returned HTTP {observed.status}")
                template = next(
                    s
                    for s in observed.request.post_data_json["searches"]
                    if s.get("q") == query and not s.get("group_by")
                )
                # Reuse only the public search credentials already issued to this page.
                # Never persist or print these headers or the request URL (which may contain a key).
                headers = {
                    k: v
                    for k, v in observed.request.headers.items()
                    if k in ("content-type", "x-typesense-api-key", "origin")
                }
                await self._collect(
                    context.request, observed.url, headers, template, criteria, result
                )
                await page.close()
                # The broad result set is retained. Only missing requested metadata needs
                # detail enrichment; native-URL validation happens after local filtering.
                relaxed = replace(criteria, include_unknown=True)
                candidates = []
                for job in result.jobs:
                    accepted, _ = matches(job, relaxed)
                    _, reasons = matches(job, criteria)
                    if accepted and (
                        criteria.exclude_keywords
                        or any(
                            r.startswith("unknown ") and r != "unknown seniority" for r in reasons
                        )
                    ):
                        candidates.append(job)

                async def enrich(job):
                    detail = await context.new_page()
                    result.detail_visits += 1
                    try:
                        await self._navigate(detail, job.url)
                        parsed = parse_posting(self.site, await detail.content(), job.url)
                        if parsed:
                            enriched = self._enrich_seed(job, parsed)
                            job.__dict__.update(enriched.__dict__)
                    except PostingUnavailable:
                        result.unavailable += 1
                        job.valid_through = "1970-01-01"
                    except Exception:
                        result.errors.append(f"Could not enrich posting: {job.url}")
                    finally:
                        await detail.close()

                await map_bounded(candidates, enrich, criteria.detail_workers)
        except TimeoutError:
            result.errors.append(
                "Discovery/enrichment deadline reached; completed records retained"
            )
            result.limited = True
        except SiteAccessError as exc:
            result.errors.append(str(exc))
            result.status = "partial" if result.jobs else "blocked"
        except Exception as exc:
            # Do not expose request URLs or public search credentials through exception text.
            result.errors.append(f"Search failed ({type(exc).__name__})")
        finally:
            if context:
                await context.close()
        return self._finish(result) if result.status == "ok" else result

    async def _collect(self, request, url, headers, template, criteria, result):
        seen = set()
        page_size = min(250, criteria.max_per_site)
        for page_number in range(1, criteria.max_pages + 1):
            body = {
                "searches": [
                    {**template, "q": template["q"], "page": page_number, "per_page": page_size}
                ]
            }
            response = await request.post(
                url, headers=headers, data=body, timeout=criteria.timeout_seconds * 1000
            )
            try:
                if response.status != 200:
                    raise SiteAccessError(f"Search returned HTTP {response.status}")
                payload = await response.json()
            finally:
                await response.dispose()
            rows = payload.get("results")
            if not rows or "error" in rows[0]:
                raise SiteAccessError("Public search rejected pagination or changed format")
            found = rows[0].get("found")
            if isinstance(found, int):
                result.advertised_total = found
            seeds = search_postings(payload)
            if rows[0].get("hits") and not seeds:
                raise SiteAccessError("Search returned records but their posting schema changed")
            result.pages += 1
            fresh = [job for key, job in seeds.items() if key not in seen]
            seen.update(seeds)
            result.discovered = len(seen)
            remaining = criteria.max_per_site - len(result.jobs)
            result.jobs.extend(fresh[:remaining])
            self._progress(result)
            if not fresh:
                if seeds:
                    result.limited = True
                    result.errors.append(
                        "Search pagination repeated; stopped without claiming exhaustion"
                    )
                break
            exhausted = (
                not rows[0].get("hits")
                or len(rows[0]["hits"]) < page_size
                or isinstance(found, int)
                and page_number * page_size >= found
            )
            if exhausted:
                break
            if len(result.jobs) >= criteria.max_per_site or page_number == criteria.max_pages:
                result.limited = True
                break
            await asyncio.sleep(0.25)
