import asyncio
import csv
import json

from wagecuck_search.export import read_jobs
from wagecuck_search.models import JobPosting, SearchCriteria, SiteResult
from wagecuck_search.operations import discover, filter_csv, save_report, validate_csv
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


def test_file_operations_preserve_rows_and_validate_only_supplied_input(tmp_path):
    output = tmp_path
    validator = Validator()
    async def run():
        criteria = SearchCriteria("software engineer", sites=("simplify",))
        discovered = await discover(criteria, providers=[Provider()])
        save_report(output / "search.csv", discovered)
        filtered = await filter_csv(
            output / "search.csv", output / "filter.csv", criteria,
            location_prompt="in OC", agent=Agent(),
        )
        validated = await validate_csv(
            output / "filter.csv", output / "validation.csv", validator=validator,
        )
        return {"search": discovered["summary"], "filter": filtered["summary"],
                "validation": validated["summary"]}
    summary = asyncio.run(run())
    search = read_jobs(output / "search.csv")
    filtered = read_jobs(output / "filter.csv")
    validated = read_jobs(output / "validation.csv")
    assert len(search) == 2
    assert [job["company"] for job in filtered] == ["Acme"]
    assert [job["company"] for job in validated] == ["Acme"]
    assert validator.companies == ["Acme"]
    assert (output / ".artifacts" / "filter.locations.csv").exists()
    with (output / "search.csv").open(encoding="utf-8-sig", newline="") as stream:
        search_reader = csv.DictReader(stream)
        search_columns = search_reader.fieldnames
        search_rows = list(search_reader)
    with (output / "filter.csv").open(encoding="utf-8-sig", newline="") as stream:
        filter_reader = csv.DictReader(stream)
        filter_columns = filter_reader.fieldnames
        filter_rows = list(filter_reader)
    assert filter_columns == search_columns
    assert filter_rows == search_rows[:1]
    for name in ("search.json", "filter.json", "validation.json"):
        payload = json.loads((output / name).read_text(encoding="utf-8"))
        assert payload["summary"]["stage"] in name
    assert summary["search"]["stage"] == "search"
    assert summary["validation"]["stage"] == "validation"
    assert "deduplicated" not in summary["filter"]
    assert summary["validation"]["deduplicated"] == 0
    assert summary["filter"]["agent_location_calls"] == 1
    assert summary["filter"]["raw_unique_locations"] == 2
