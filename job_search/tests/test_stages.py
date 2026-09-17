import asyncio
import csv
import json

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
    def __init__(self):
        self.companies = []

    async def validate(self, job):
        self.companies.append(job.company)
        return ValidationResult(
            url="https://jobs.example/" + job.company.casefold(), kind="employer",
            checked_at="2026-09-16T00:00:00+00:00",
        )


class Agent:
    async def classify(self, rows, prompt):
        return [{**row, "canonical_location": row["raw_location"],
                 "matches": "Irvine" in row["raw_location"], "reason": "fixture"}
                for row in rows]


def test_run_stages_filters_before_validation_and_writes_each_csv(tmp_path):
    output = tmp_path / "stages"
    validator = Validator()
    summary = asyncio.run(run_stages(
        SearchCriteria("software engineer", sites=("simplify",)),
        output,
        providers=[Provider()],
        validator=validator,
        location_prompt="in OC",
        agent=Agent(),
    ))
    search = read_jobs(output / "01-search.csv")
    filtered = read_jobs(output / "02-filter.csv")
    validated = read_jobs(output / "03-validation.csv")
    assert len(search) == 2
    assert [job["company"] for job in filtered] == ["Acme"]
    assert [job["company"] for job in validated] == ["Acme"]
    assert validator.companies == ["Acme"]
    assert (output / ".artifacts" / "02-filter.locations.csv").exists()
    with (output / "01-search.csv").open(encoding="utf-8-sig", newline="") as stream:
        search_reader = csv.DictReader(stream)
        search_columns = search_reader.fieldnames
        search_rows = list(search_reader)
    with (output / "02-filter.csv").open(encoding="utf-8-sig", newline="") as stream:
        filter_reader = csv.DictReader(stream)
        filter_columns = filter_reader.fieldnames
        filter_rows = list(filter_reader)
    assert filter_columns == search_columns
    assert filter_rows == search_rows[:1]
    for name in ("01-search.json", "02-filter.json", "03-validation.json"):
        payload = json.loads((output / name).read_text(encoding="utf-8"))
        assert payload["summary"]["stage"] in name
    assert summary["search"]["stage"] == "search"
    assert summary["validation"]["stage"] == "validation"
    assert "deduplicated" not in summary["filter"]
    assert summary["validation"]["deduplicated"] == 0
    assert summary["filter"]["agent_location_calls"] == 1
    assert summary["filter"]["raw_unique_locations"] == 2
