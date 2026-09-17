import asyncio
import csv
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from wagecuck_search.export import read_jobs, write_jobs
from wagecuck_search.location_agent import OpenAILocationAgent, unique_locations
from wagecuck_search.models import SearchCriteria
from wagecuck_search.postfilter import filter_jobs


def job(url, location, workplace="onsite", salary=None):
    value = {
        "url": url,
        "title": "Senior Software Engineer",
        "company": "Acme",
        "location": location,
        "source": "simplify",
        "workplace": workplace,
        "experience_levels": ["senior"],
        "sources": [{"site": "simplify", "url": "https://simplify.jobs/p/source"}],
        "description": "A complete description, retained between stages.",
    }
    if salary:
        value["salary"] = salary
    return value


class FakeLocationAgent:
    def __init__(self, matching):
        self.matching = matching
        self.calls = []

    async def classify(self, rows, prompt):
        self.calls.append((rows, prompt))
        return [{**row, "canonical_location": "San Francisco, CA, USA"
                 if "Francisco" in row["raw_location"] or row["raw_location"] == "SF CA"
                 else row["raw_location"],
                 "matches": row["raw_location"] in self.matching,
                 "reason": "fixture decision"} for row in rows]


def output_dir():
    path = Path(__file__).resolve().parents[1] / ".test-output" / str(uuid4())
    path.mkdir(parents=True)
    return path


def test_unique_locations_counts_duplicates_and_preserves_workplace():
    rows = unique_locations([
        job("https://a.example/1", "SF CA"),
        job("https://a.example/2", "SF CA"),
        job("https://a.example/3", "SF CA", "remote"),
    ])
    assert len(rows) == 2
    assert sorted(row["job_count"] for row in rows) == [1, 2]
    assert len({row["location_id"] for row in rows}) == 2


def test_agent_fills_location_csv_and_filter_joins_to_every_job():
    jobs = [
        job("https://a.example/1", "SF CA"),
        job("https://a.example/2", "San Francisco, California"),
        job("https://a.example/3", "New York, NY, USA"),
    ]
    agent = FakeLocationAgent({"SF CA", "San Francisco, California"})
    directory = output_dir()
    output = directory / "filtered.csv"
    locations = directory / "filtered.locations.csv"
    report = asyncio.run(filter_jobs(
        jobs, SearchCriteria("software engineer"),
        location_prompt="in the San Francisco area",
        location_map_path=locations,
        agent=agent,
    ))
    write_jobs(output, report["jobs"])
    assert len(agent.calls) == 1
    assert len(agent.calls[0][0]) == 3
    assert [row["url"] for row in report["jobs"]] == [
        "https://a.example/1", "https://a.example/2"
    ]
    with locations.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3
    assert {row["matches"] for row in rows} == {"True", "False"}
    assert {row["filter_prompt"] for row in rows} == {"in the San Francisco area"}
    assert len(read_jobs(output)) == 2
    assert report["summary"]["raw_unique_locations"] == 3
    assert report["summary"]["unique_locations"] == 2


def test_salary_filter_is_local_and_can_keep_unknowns():
    jobs = [
        job("https://a.example/1", "Remote", "remote", {
            "minimum": 190000, "maximum": 200000, "currency": "USD", "period": "year"
        }),
        job("https://a.example/2", "Remote", "remote"),
    ]
    strict = asyncio.run(filter_jobs(
        jobs, SearchCriteria("software engineer", min_salary=175000), max_salary=180000
    ))
    assert strict["jobs"] == []
    permissive = asyncio.run(filter_jobs(
        jobs, SearchCriteria("software engineer", min_salary=175000, include_unknown=True),
        max_salary=210000,
    ))
    assert len(permissive["jobs"]) == 2
    assert "Unverified filter: salary" in permissive["jobs"][1]["note"]
    with pytest.raises(ValueError, match="at least min_salary"):
        asyncio.run(filter_jobs(
            jobs, SearchCriteria("software engineer", min_salary=200000), max_salary=199999
        ))


def test_openai_agent_sends_one_request_and_checks_all_ids():
    source = unique_locations([
        job("https://a.example/1", "SF CA"),
        job("https://a.example/2", "San Francisco, California"),
    ])
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        supplied = json.loads(body["input"])["locations"]
        result = {"locations": [{
            "location_id": row["location_id"],
            "canonical_location": "San Francisco, CA, USA",
            "matches": True,
            "reason": "Same metro area",
        } for row in supplied]}
        return httpx.Response(200, json={"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]
        }]})

    agent = OpenAILocationAgent(api_key="test", transport=httpx.MockTransport(handler))
    result = asyncio.run(agent.classify(source, "in the SF area"))
    assert len(requests) == 1
    assert len(result) == 2
    assert {row["canonical_location"] for row in result} == {"San Francisco, CA, USA"}


def test_stage_csv_round_trip_preserves_validation_and_private_stage_fields():
    source = output_dir() / "stage.csv"
    value = job("https://ats.example/1", "Los Angeles, CA, USA") | {
        "url_validated_at": "2026-09-16T00:00:00+00:00",
        "application_url_type": "ats",
        "application_urls": ["https://ats.example/1/apply"],
        "employer_urls": ["https://acme.example/jobs"],
    }
    write_jobs(source, [value])
    restored = read_jobs(source)[0]
    assert restored["description"] == value["description"]
    assert restored["url_validated_at"] == value["url_validated_at"]
    assert restored["application_urls"] == value["application_urls"]
    assert restored["sources"] == value["sources"]
