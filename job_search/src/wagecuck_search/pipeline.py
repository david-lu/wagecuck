from __future__ import annotations

import asyncio
from dataclasses import asdict

from .application_links import BOARD_DOMAINS, on_domain, web_url
from .array_fields import enrich_job, merge_fields
from .dedupe import canonical_url, deduplicate
from .job_fields import job_field_types, merge_field_types, merge_scalars
from .matching import broad_query, matches
from .models import SearchCriteria, SiteResult, utc_now
from .workers import map_bounded


async def search(
    criteria: SearchCriteria, providers=None, validator=None, *, progress=None, on_discovery=None,
    filter_candidates=True, deduplicate_candidates=True,
) -> dict:
    """Discover, deduplicate, filter, and validate employer application destinations."""
    if filter_candidates and (criteria.array_filters or criteria.field_filters):
        raise ValueError("Field predicates require filter_csv() or filter_jobs()")
    started = utc_now()
    query = broad_query(criteria.job_title)
    results = await _fetch(criteria, query, providers, progress) if providers is not None else None
    if providers is None or validator is None:
        from playwright.async_api import async_playwright

        from .providers import provider_for
        from .validation import BrowserValidator

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=not criteria.show_browser, channel=criteria.browser_channel
                )
                native = None
                try:
                    if results is None:
                        results = await _fetch(
                            criteria,
                            query,
                            [provider_for(site, browser) for site in criteria.sites],
                            progress,
                        )
                    if validator is None:
                        native = BrowserValidator(
                            browser, criteria,
                            browser_factory=lambda: playwright.chromium.launch(
                                headless=not criteria.show_browser, channel=criteria.browser_channel
                            ),
                        )
                    return await _validated_report(
                        criteria,
                        query,
                        results,
                        started,
                        validator or native,
                        progress,
                        on_discovery,
                        filter_candidates=filter_candidates,
                        deduplicate_candidates=deduplicate_candidates,
                    )
                finally:
                    if native:
                        await native.close()
                    else:
                        await browser.close()
        except Exception as exc:
            results = (
                results
                if results is not None
                else [
                    SiteResult(
                        site,
                        status="error",
                        errors=[f"Browser unavailable ({type(exc).__name__}): {str(exc)[:400]}"],
                    )
                    for site in criteria.sites
                ]
            )
            return await _validated_report(
                criteria, query, results, started, None, progress, on_discovery,
                filter_candidates=filter_candidates,
                deduplicate_candidates=deduplicate_candidates,
            )
    return await _validated_report(
        criteria, query, results, started, validator, progress, on_discovery,
        filter_candidates=filter_candidates,
        deduplicate_candidates=deduplicate_candidates,
    )


async def _validated_report(
    criteria, query, results, started, validator, progress=None, on_discovery=None,
    *, filter_candidates=True, deduplicate_candidates=True,
):
    if on_discovery:
        on_discovery(results)
    report = build_report(
        criteria,
        query,
        results,
        started,
        apply_filters=filter_candidates,
        deduplicate_candidates=deduplicate_candidates,
    )
    candidates = [job for result in results for job in result.jobs]
    if deduplicate_candidates:
        candidates, _ = deduplicate(candidates)
    by_url = {job.url: job for job in candidates}

    async def one(posting):
        from .validation import ValidationResult

        if validator is None:
            return ValidationResult(reason="Application URL validator unavailable")
        try:
            return await asyncio.wait_for(
                validator.validate(by_url[posting["url"]]),
                criteria.validation_timeout_seconds + 5,
            )
        except Exception as exc:
            return ValidationResult(reason=f"URL validation failed ({type(exc).__name__})")

    completed = 0
    if progress:
        progress(
            {
                "phase": "validation",
                "completed": 0,
                "total": len(report["jobs"]),
                "discovered": sum(r.discovered for r in results),
            }
        )

    def checked(index, result):
        nonlocal completed
        completed += 1
        if progress and (completed % 25 == 0 or completed == len(report["jobs"])):
            progress({"phase": "validation", "completed": completed, "total": len(report["jobs"])})

    if validator and callable(getattr(validator, "prepare", None)) and callable(
        getattr(validator, "finish", None)
    ):
        from .validation import ValidationPlan, ValidationResult

        async def prepare(posting):
            try:
                return await asyncio.wait_for(
                    validator.prepare(by_url[posting["url"]]),
                    criteria.validation_timeout_seconds + 5,
                )
            except Exception as exc:
                return ValidationResult(reason=f"URL validation failed ({type(exc).__name__})")

        def prepared(index, result):
            if not isinstance(result, ValidationPlan):
                checked(index, result)

        checks = await map_bounded(
            report["jobs"], prepare, criteria.validation_workers, prepared
        )
        deferred = [(i, value) for i, value in enumerate(checks) if isinstance(value, ValidationPlan)]
        if progress:
            progress({"phase": "browser_validation", "remaining": len(deferred),
                      "workers": min(criteria.validation_workers, 8)})

        async def finish(entry):
            index, plan = entry
            try:
                result = await asyncio.wait_for(
                    validator.finish(plan), criteria.validation_timeout_seconds + 5
                )
            except Exception as exc:
                result = ValidationResult(reason=f"URL validation failed ({type(exc).__name__})")
            checks[index] = result
            return result

        recycle = getattr(validator, "recycle", None)
        batch_size = 25 if callable(recycle) else max(1, len(deferred))
        for offset in range(0, len(deferred), batch_size):
            if callable(recycle):
                try:
                    await recycle()
                except Exception as exc:
                    # Preserve completed results if a later browser launch fails.
                    for index, _ in deferred[offset:]:
                        result = ValidationResult(
                            reason=f"Browser unavailable ({type(exc).__name__})"
                        )
                        checks[index] = result
                        checked(index, result)
                    break
            await map_bounded(
                deferred[offset:offset + batch_size], finish,
                min(criteria.validation_workers, 8), checked,
            )
    else:
        checks = await map_bounded(report["jobs"], one, criteria.validation_workers, checked)
    summary = report["summary"]
    summary.update(
        validation_attempted=len(checks),
        validation_rejected=0,
        validation_rejections=[],
        application_url_deduplicated=0,
        discovery_deduplicated=summary["deduplicated"],
    )
    for stats in summary["sites"].values():
        stats.update(
            validation_attempted=0, validation_rejected=0, returned=0, matched_with_duplicates=0
        )
    validated = []
    for posting, check in zip(report["jobs"], checks):
        original = by_url[posting["url"]]
        stats = summary["sites"][original.source]
        stats["validation_attempted"] += 1
        if (
            check.reason
            or not check.url
            or not web_url(check.url)
            or on_domain(check.url, BOARD_DOMAINS)
            or check.kind not in ("employer", "ats")
        ):
            summary["validation_rejected"] += 1
            stats["validation_rejected"] += 1
            summary["validation_rejections"].append(
                {
                    "title": posting["title"],
                    "company": posting["company"],
                    "sources": posting["sources"],
                    "reason": check.reason or "No verified employer application URL",
                }
            )
            continue
        # The verified page replaces prior values for each field it extracts,
        # including empty values; board snippets are not additional page evidence.
        posting["array_fields"] = {**posting.get("array_fields", {}), **check.array_fields}
        posting["fields"] = {**posting.get("fields", {}), **check.fields}
        for name in check.fields:
            posting.get("field_types", {}).pop(name, None)
        posting["field_types"] = job_field_types(posting)
        if not filter_candidates:
            posting["description"] = check.description
        posting["url"] = check.url
        posting["url_validated_at"] = check.checked_at or utc_now().isoformat()
        posting["application_url_type"] = check.kind
        validated.append((posting, original.source))

    # This is deliberately the final transformation. Every filtered row is
    # validated first; only then can two discovery records be identified as
    # the same opening by their resolved native application URL.
    accepted, seen = [], {}
    for posting, source in validated:
        stats = summary["sites"][source]
        key = canonical_url(posting["url"])
        if key in seen:
            target = seen[key]
            target["array_fields"] = merge_fields(
                target.get("array_fields"), posting.get("array_fields")
            )
            target["fields"] = merge_scalars(target.get("fields"), posting.get("fields"))
            target["field_types"] = merge_field_types(target.get("field_types"), posting.get("field_types"))
            if len(posting.get("description", "")) > len(target.get("description", "")):
                target["description"] = posting["description"]
            for origin in posting["sources"]:
                if origin not in target["sources"]:
                    target["sources"].append(origin)
            summary["application_url_deduplicated"] += 1
            stats["deduplicated"] += 1
            continue
        seen[key] = posting
        accepted.append(posting)
        stats["returned"] += 1
    for site, stats in summary["sites"].items():
        stats["matched_with_duplicates"] = sum(
            any(s["site"] == site for s in p["sources"]) for p in accepted
        )
    report["jobs"] = accepted
    summary["returned"] = len(accepted)
    summary["deduplicated"] += summary["application_url_deduplicated"]
    summary["partial"] |= bool(summary["validation_rejected"])
    summary["finished_at"] = utc_now().isoformat()
    return report


async def _fetch(criteria, query, providers, progress=None):
    selected = {provider.site: provider for provider in providers}

    async def one(site):
        try:
            if site not in selected:
                raise ValueError(f"No provider configured for {site}")
            selected[site].progress = progress
            # Providers should preserve partial results on their own internal deadline.
            result = await asyncio.wait_for(
                selected[site].fetch(query, criteria), timeout=criteria.site_timeout_seconds + 10
            )
            for job in result.jobs:
                enrich_job(job)
            return result
        except Exception as exc:
            return SiteResult(site, status="error", errors=[f"{type(exc).__name__}: {exc}"])

    return await asyncio.gather(*(one(site) for site in criteria.sites))


def build_report(
    criteria, query, results, started, *, apply_filters=True, deduplicate_candidates=True
):
    raw = [job for result in results for job in result.jobs]
    if deduplicate_candidates:
        unique, removed = deduplicate(raw)
    else:
        unique, removed = raw, {}
    matched = []
    reasons = {}
    for job in unique:
        accepted, notes = matches(job, criteria, started) if apply_filters else (True, [])
        if accepted:
            if notes:
                job.note = "; ".join(filter(None, [job.note, *notes]))
            matched.append(job)
        else:
            for reason in notes:
                reasons[reason] = reasons.get(reason, 0) + 1
    per_site = {}
    for result in results:
        per_site[result.site] = {
            "status": result.status,
            "discovered": result.discovered,
            "advertised_total": result.advertised_total,
            "detail_visits": result.detail_visits,
            "fetched": len(result.jobs),
            "unavailable": result.unavailable,
            "deduplicated": removed.get(result.site, 0),
            "returned": sum(job.source == result.site for job in matched),
            "matched_with_duplicates": sum(
                any(s["site"] == result.site for s in job.sources) for job in matched
            ),
            "pages": result.pages,
            "limited": result.limited,
            "errors": result.errors,
        }
    return {
        "criteria": asdict(criteria),
        "broad_query": query,
        "jobs": [job.output() if apply_filters else asdict(job) for job in matched],
        "summary": {
            "started_at": started.isoformat(),
            "finished_at": utc_now().isoformat(),
            "sites": per_site,
            "fetched": len(raw),
            "discovered": sum(r.discovered for r in results),
            "deduplicated": len(raw) - len(unique),
            "unique_before_filtering": len(unique),
            "filtered_out": len(unique) - len(matched),
            "returned": len(matched),
            "filter_reasons": reasons,
            "partial": any(r.status != "ok" or r.limited for r in results),
        },
    }
