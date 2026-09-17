import asyncio
import json
from dataclasses import asdict
from uuid import UUID

import pytest

from wagecuck_search.checkpoint import filters, restore_discovery
from wagecuck_search.models import JobPosting, Salary, SearchCriteria, SiteResult
from wagecuck_search.pipeline import search
from wagecuck_search.simplify import SimplifyProvider
from wagecuck_search.validation import ValidationPlan, ValidationResult
from wagecuck_search.workers import map_bounded


@pytest.mark.parametrize("workers", [8, 32, 64])
def test_thousands_of_results_survive_pipeline_with_bounded_validation_workers(workers):
    class Provider:
        site = "simplify"

        async def fetch(self, query, criteria):
            jobs = [
                JobPosting(
                    url=f"https://simplify.jobs/p/{i}",
                    title="Senior Software Engineer",
                    company=f"Company {i}",
                    location="Canada",
                    source=self.site,
                )
                for i in range(5000)
            ]
            return SiteResult(self.site, jobs, discovered=5000)

    class Validator:
        active = 0
        peak = 0

        async def validate(self, job):
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return ValidationResult(
                "https://jobs.lever.co/acme/" + job.url.rsplit("/", 1)[1], "ats"
            )

    validator = Validator()
    events = []
    criteria = SearchCriteria(
        "software engineer",
        sites=("simplify",),
        seniority=("senior", "staff"),
        validation_workers=workers,
    )
    report = asyncio.run(search(criteria, [Provider()], validator, progress=events.append))
    assert report["summary"]["returned"] == 5000
    assert report["summary"]["validation_attempted"] == 5000
    assert validator.peak == workers
    assert events[-1]["completed"] == 5000
    assert all(job["application_url_type"] == "ats" for job in report["jobs"])


def test_http_phase_finishes_before_bounded_browser_phase_and_preserves_result_order():
    class Provider:
        site = "simplify"

        async def fetch(self, query, criteria):
            return SiteResult(self.site, [
                JobPosting(f"https://simplify.jobs/p/{i}", "Senior Software Engineer",
                           f"Company {i}", "Canada", self.site)
                for i in range(20)
            ], discovered=20)

    class Validator:
        prepared = 0
        active = 0
        peak = 0

        async def prepare(self, job):
            await asyncio.sleep(0)
            self.prepared += 1
            index = int(job.url.rsplit("/", 1)[1])
            if index % 2 == 0:
                return ValidationResult(f"https://jobs.lever.co/acme/{index}", "ats")
            return ValidationPlan(job, [(job.url, 0)], [], 60)

        async def finish(self, plan):
            assert self.prepared == 20
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            index = int(plan.job.url.rsplit("/", 1)[1])
            if index == 19:
                return ValidationResult(reason="Application is closed or missing")
            return ValidationResult(f"https://jobs.lever.co/acme/{index}", "ats")

    validator = Validator()
    report = asyncio.run(search(
        SearchCriteria("software engineer", sites=("simplify",), validation_workers=32),
        [Provider()], validator,
    ))
    assert validator.peak == 8
    assert report["summary"]["validation_attempted"] == 20
    assert report["summary"]["validation_rejected"] == 1
    assert [p["company"] for p in report["jobs"]] == [f"Company {i}" for i in range(19)]


@pytest.mark.parametrize("fail_recycle", [False, True])
def test_browser_recycling_waits_for_batches_and_preserves_completed_results(fail_recycle):
    class Provider:
        site = "simplify"

        async def fetch(self, query, criteria):
            return SiteResult(self.site, [
                JobPosting(f"https://simplify.jobs/p/{i}", "Senior Software Engineer",
                           f"Company {i}", "Canada", self.site)
                for i in range(205)
            ], discovered=205)

    class Validator:
        active = 0
        completed = 0

        def __init__(self):
            self.recycles = []

        async def prepare(self, job):
            return ValidationPlan(job, [(job.url, 0)], [], 60)

        async def recycle(self):
            assert self.active == 0
            self.recycles.append(self.completed)
            if fail_recycle and self.completed == 100:
                raise RuntimeError("Browser launch failed")

        async def finish(self, plan):
            self.active += 1
            await asyncio.sleep(0)
            self.active -= 1
            self.completed += 1
            return ValidationResult(
                "https://jobs.lever.co/acme/" + plan.job.url.rsplit("/", 1)[1], "ats"
            )

    validator = Validator()
    report = asyncio.run(search(
        SearchCriteria("software engineer", sites=("simplify",), validation_workers=32),
        [Provider()], validator,
    ))
    assert validator.recycles == (list(range(0, 101, 25)) if fail_recycle else list(range(0, 205, 25)))
    assert report["summary"]["returned"] == (100 if fail_recycle else 205)
    assert report["summary"]["validation_attempted"] == 205
    assert report["summary"]["validation_rejected"] == (105 if fail_recycle else 0)


def test_bulk_pagination_collects_5000_without_a_detail_visit(monkeypatch):
    async def no_delay(value):
        pass

    monkeypatch.setattr("wagecuck_search.simplify.asyncio.sleep", no_delay)
    disposed = []
    requests = []

    class Request:
        async def post(self, url, headers, data, timeout):
            params = data["searches"][0]
            requests.append(params)
            start = (params["page"] - 1) * params["per_page"]

            class Response:
                status = 200

                async def json(self):
                    return {
                        "results": [
                            {
                                "found": 5000,
                                "hits": [
                                    {
                                        "document": {
                                            "posting_id": str(UUID(int=i + 1)),
                                            "title": "Senior Software Engineer",
                                            "company_name": f"C{i}",
                                            "locations": ["Canada"],
                                        }
                                    }
                                    for i in range(start, min(start + params["per_page"], 5000))
                                ],
                            }
                        ]
                    }

                async def dispose(self):
                    disposed.append(params["page"])

            return Response()

    provider = SimplifyProvider("simplify", None)
    result = SiteResult("simplify")
    asyncio.run(
        provider._collect(
            Request(),
            "https://example.test",
            {},
            {"q": "software engineer", "collection": "jobs"},
            SearchCriteria("software engineer", max_per_site=5000),
            result,
        )
    )
    assert len(result.jobs) == result.discovered == 5000
    assert result.pages == 20 and result.detail_visits == 0
    assert not result.limited
    assert len(disposed) == 20
    assert all(p["q"] == "software engineer" and p["per_page"] == 250 for p in requests)


def test_checkpoint_roundtrip_preserves_evidence_and_rejects_different_filters():
    criteria = SearchCriteria("software engineer", sites=("simplify",))
    job = JobPosting(
        "https://simplify.jobs/p/1",
        "Senior Software Engineer",
        "Acme",
        "Canada",
        "simplify",
        salary=Salary(100000, 200000, "CAD", "year"),
        application_urls=["https://acme.com/careers/1"],
        employer_urls=["https://acme.com"],
    )
    payload = json.loads(
        json.dumps(
            {
                "version": 1,
                "filters": filters(criteria),
                "sites": [asdict(SiteResult("simplify", [job]))],
            }
        )
    )
    restored = restore_discovery(payload, criteria)
    assert restored[0].result.jobs[0].salary.currency == "CAD"
    assert restored[0].result.jobs[0].application_urls == job.application_urls
    criteria.validation_workers = 12
    assert restore_discovery(payload, criteria)
    criteria.seniority = ("staff",)
    with pytest.raises(ValueError):
        restore_discovery(payload, criteria)


def test_worker_error_cancels_and_joins_other_workers():
    active = set()

    async def run():
        async def task(index):
            active.add(index)
            try:
                await asyncio.sleep(0)
                if index == 0:
                    raise ValueError("failure")
                await asyncio.sleep(60)
            finally:
                active.remove(index)

        with pytest.raises(ValueError):
            await map_bounded(list(range(1000)), task, 4)
        assert not active

    asyncio.run(run())
