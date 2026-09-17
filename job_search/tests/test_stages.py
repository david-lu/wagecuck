import asyncio
import json
from pathlib import Path
from uuid import uuid4

from wagecuck_search.export import read_jobs
from wagecuck_search.models import JobPosting, SearchCriteria, SiteResult
from wagecuck_search.stages import run_stages
from wagecuck_search.validation import ValidationResult


class Provider:
    site = "simplify"
    progress = None

    async def fetch(self, query, criteria):
        return SiteResult("simplify", [
            JobPosting("https://simplify.jobs/p/1", "Senior Software Engineer", "Acme",
                       "Irvine, CA, USA", "simplify", workplace="onsite"),
            JobPosting("https://simplify.jobs/p/2", "Staff Software Engineer", "Beta",
                       "New York, NY, USA", "simplify", workplace="hybrid"),
        ], discovered=2)


class Validator:
    async def validate(self, job):
        return ValidationResult(
            url="https://jobs.example/" + job.company.casefold(), kind="employer",
            checked_at="2026-09-16T00:00:00+00:00",
        )


class Agent:
    async def classify(self, rows, prompt):
        return [{**row, "canonical_location": row["raw_location"],
                 "matches": "Irvine" in row["raw_location"], "reason": "fixture"}
                for row in rows]


def test_run_stages_writes_search_validation_filter_and_location_csvs():
    output = Path(__file__).resolve().parents[1] / ".test-output" / str(uuid4())
    summary = asyncio.run(run_stages(
        SearchCriteria("software engineer", sites=("simplify",)),
        output,
        providers=[Provider()],
        validator=Validator(),
        location_prompt="in OC",
        agent=Agent(),
    ))
    search = read_jobs(output / "01-search.csv")
    validated = read_jobs(output / "02-validation.csv")
    filtered = read_jobs(output / "03-filter.csv")
    assert len(search) == 2
    assert len(validated) == 2
    assert [job["company"] for job in filtered] == ["Acme"]
    assert (output / "03-filter.locations.csv").exists()
    for name in ("01-search.json", "02-validation.json", "03-filter.json"):
        payload = json.loads((output / name).read_text(encoding="utf-8"))
        assert payload["summary"]["stage"] in name
    assert summary["search"]["stage"] == "search"
    assert summary["validation"]["stage"] == "validation"
    assert summary["filter"]["agent_location_calls"] == 1
    assert summary["filter"]["raw_unique_locations"] == 2
