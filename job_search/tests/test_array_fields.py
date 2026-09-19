import asyncio
import csv
import json
from dataclasses import asdict

import httpx
import pytest

from wagecuck_search.array_fields import (
    comma_separated,
    enrich_job,
    extract_page_fields,
    field_values,
    items,
    page_evidence,
    unique_values,
)
from wagecuck_search.ats_apis import AtsApiVerifier
from wagecuck_search.checkpoint import SavedProvider
from wagecuck_search.cli import parser
from wagecuck_search.dedupe import deduplicate
from wagecuck_search.export import read_jobs, write_jobs, write_table
from wagecuck_search.field_filling import OpenAIFieldAgent
from wagecuck_search.field_filter import OpenAIFieldFilterAgent, classify_values
from wagecuck_search.models import JobPosting, SearchCriteria, SiteResult
from wagecuck_search.operations import discover, filter_csv, validate_csv, validate_jobs
from wagecuck_search.postfilter import filter_jobs
from wagecuck_search.profiles import SearchProfile
from wagecuck_search.validation import BrowserValidator, ValidationResult


def job(index=1, **changes):
    return JobPosting(
        url=f"https://jobs.ashbyhq.com/acme/{index}",
        title="Software Engineer", company="Acme", location="Los Angeles, CA",
        source="simplify", **changes
    )


class Decisions:
    def __init__(self):
        self.calls = []

    async def classify(self, rows, prompt):
        self.calls.append((rows, prompt))
        return [{**row, "canonical_value": row["raw_value"].casefold(),
                 "matches": row["raw_value"].casefold() in prompt.casefold(),
                 "reason": "Fixture predicate"} for row in rows]


def test_page_evidence_scopes_descriptions_for_agent_filling():
    html = """<nav>Java and PHP</nav><script>React JavaScript</script>
    <main><h1>Software Engineer</h1><div class=prose-job>
    Develop using Python and Django on Amazon Web Services.
    </div></main>"""
    evidence = page_evidence(html, "Software Engineer")
    assert "Python and Django" in evidence["description"]
    assert "Java" not in evidence["description"]
    assert extract_page_fields(html, "Software Engineer") == {}


def test_schema_extraction_ignores_other_postings_and_retains_geographic_atoms():
    payload = [
        {"@type": "JobPosting", "title": "Software Engineer",
         "description": "Build Python services using FastAPI.",
         "jobLocationType": "TELECOMMUTE",
         "applicantLocationRequirements": {"addressCountry": "Canada"}},
        {"@type": "JobPosting", "title": "Other role", "description": "Java and Spring Boot"},
    ]
    html = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
    assert extract_page_fields(html, "Software Engineer") == {"location": ["Remote; Canada"]}
    assert page_evidence(html, "Software Engineer")["description"] == payload[0]["description"]


def test_comma_lists_round_trip_compound_locations_and_custom_csv_columns(tmp_path):
    values = ["Los Angeles, CA", "Remote; Canada"]
    assert items(comma_separated(values)) == values
    record = asdict(job(array_fields={
        "location": values, "programming_languages": ["Python", "TypeScript"],
        "frameworks": ["React"], "clouds": ["AWS", "GCP"],
    }))
    output = tmp_path / "jobs.csv"
    write_jobs(output, [record])
    with output.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["programming_languages"] == "Python,TypeScript"
    assert row["clouds"] == "AWS,GCP"
    assert read_jobs(output)[0]["array_fields"] == record["array_fields"]
    row["new_attribute"] = "One,Two"
    write_table(output, list(row), [row])
    assert read_jobs(output)[0]["fields"]["new_attribute"] == "One,Two"
    assert read_jobs(output, array_columns=["new_attribute"])[0]["array_fields"]["new_attribute"] == ["One", "Two"]


def test_legacy_location_is_not_split_at_city_state_comma():
    assert field_values({"location": "Los Angeles, CA"}, "location") == ["Los Angeles, CA"]
    rows = unique_values([
        {"array_fields": {"languages": ["Python", "python", "Java"]}},
        {"array_fields": {"languages": "python,Rust"}},
    ], "languages")
    assert len(rows) == 3
    assert next(row for row in rows if row["raw_value"] == "Python")["job_count"] == 2


def test_search_does_not_generate_fields_from_technology_mentions():
    first = job(description="Develop in Python with Django on AWS.")
    second = job(description="Build TypeScript services with Next.js.")
    result = asyncio.run(discover(
        SearchCriteria("software engineer", sites=("simplify",)),
        [SavedProvider(SiteResult("simplify", [first, second]))],
    ))
    fields = result["jobs"][0]["array_fields"]
    assert fields == {"location": ["Los Angeles, CA"]}
    assert len(result["jobs"]) == 1
    assert deduplicate([first, second])[0][0].array_fields == fields


def test_unique_boolean_decisions_join_any_within_field_and_all_across_fields(tmp_path):
    rows = [
        asdict(job(1, array_fields={"programming_languages": ["Python", "Java"], "frameworks": ["React"]})),
        asdict(job(2, array_fields={"programming_languages": ["python"], "frameworks": ["Django"]})),
        asdict(job(3, array_fields={"programming_languages": ["Java"], "frameworks": ["React"]})),
    ]
    source, output = tmp_path / "03-validation.csv", tmp_path / "04-filter.csv"
    write_jobs(source, rows)
    agent = Decisions()
    criteria = SearchCriteria("software engineer", array_filters={
        "programming_languages": "Python", "frameworks": "React",
    })
    report = asyncio.run(filter_csv(source, output, criteria, filter_agent=agent))
    assert len(agent.calls) == 2
    assert sorted(len(call[0]) for call in agent.calls) == [2, 2]
    assert [record["url"] for record in report["jobs"]] == [rows[0]["url"]]
    assert report["summary"]["filter_reasons"] == {"frameworks": 1, "programming_languages": 1}
    with source.open(encoding="utf-8-sig", newline="") as stream:
        original = list(csv.DictReader(stream))
    with output.open(encoding="utf-8-sig", newline="") as stream:
        accepted = list(csv.DictReader(stream))
    assert accepted == original[:1]
    assert (tmp_path / ".artifacts/04-filter.field-values/frameworks.csv").exists()

    again = Decisions()
    asyncio.run(filter_csv(source, output, criteria, filter_agent=again))
    assert not again.calls
    changed = SearchCriteria("software engineer", array_filters={
        "programming_languages": "Java", "frameworks": "React",
    })
    asyncio.run(filter_csv(source, output, changed, filter_agent=again))
    assert len(again.calls) == 1
    assert again.calls[0][1] == "Java"


def test_missing_values_follow_include_unknown_without_inventing_items():
    rows = [asdict(job())]
    strict = SearchCriteria("software engineer", array_filters={"frameworks": "React"})
    permissive = SearchCriteria("software engineer", array_filters={"frameworks": "React"},
                                 include_unknown=True)
    agent = Decisions()
    assert not asyncio.run(filter_jobs(rows, strict, filter_agent=agent))["jobs"]
    assert asyncio.run(filter_jobs(rows, permissive, filter_agent=agent))["jobs"] == rows
    assert not agent.calls


def test_generic_openai_contract_classifies_unique_values_not_jobs():
    rows = unique_values([{"array_fields": {"frameworks": ["React", "React", "Django"]}}], "frameworks")
    seen = []

    def respond(request):
        body = json.loads(request.content)
        payload = json.loads(body["input"])
        seen.append(payload)
        assert payload["field_name"] == "frameworks"
        assert "jobs" not in payload
        result = {"values": [{
            "value_id": row["value_id"], "canonical_value": row["value"],
            "matches": row["value"] == "React", "reason": "Frontend framework",
        } for row in payload["values"]]}
        return httpx.Response(200, json={"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]
        }]})

    agent = OpenAIFieldFilterAgent(api_key="test", transport=httpx.MockTransport(respond))
    result = asyncio.run(agent.classify(rows, "Frontend JavaScript frameworks"))
    assert len(seen) == 1 and len(seen[0]["values"]) == 2
    assert [row["raw_value"] for row in result if row["matches"]] == ["React"]


def test_validation_enrichment_survives_native_url_deduplication():
    class Validator:
        async def validate(self, posting):
            return ValidationResult("https://jobs.ashbyhq.com/acme/current", "ats",
                                    array_fields={"frameworks": ["FastAPI"], "clouds": ["AWS"]})

    rows = [asdict(job(index, array_fields={"programming_languages": [language]}))
            for index, language in [(1, "Python"), (2, "TypeScript")]]
    report = asyncio.run(validate_jobs(rows, SearchCriteria("software engineer"), Validator()))
    assert len(report["jobs"]) == 1
    assert report["jobs"][0]["array_fields"]["programming_languages"] == ["Python", "TypeScript"]
    assert report["jobs"][0]["array_fields"]["frameworks"] == ["FastAPI"]
    assert report["jobs"][0]["array_fields"]["clouds"] == ["AWS"]


def test_ats_fast_path_keeps_description_without_generating_unrequested_fields():
    async def run():
        native = "https://boards.greenhouse.io/acme/jobs/123"
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json={"title": "Software Engineer", "company_name": "Acme",
                       "content": "<p>Develop Python services using FastAPI on AWS.</p>"}
        ))) as client:
            validator = BrowserValidator(None, SearchCriteria("software engineer"))
            validator.ats_verifier = AtsApiVerifier(client)
            validator.http_client = client
            record = job()
            record.url = native
            record.sources = [{"site": "simplify", "url": native}]
            result = await validator.prepare(record)
            assert isinstance(result, ValidationResult)
            assert result.url == native and result.kind == "ats"
            assert result.description == "Develop Python services using FastAPI on AWS."
            assert result.array_fields == {}
    asyncio.run(run())


def test_profile_filters_work_and_keyword_extraction_requires_migration(tmp_path):
    payload = {
        "name": "example", "job_titles": ["software engineer"],
        "search_queries": ["software engineer"], "output_directory": "results/example",
        "filters": {"array_fields": {"frameworks": "React or Next.js"}},
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    profile = SearchProfile.load(path)
    assert profile.filter_criteria().array_filters == {"frameworks": "React or Next.js"}
    args = parser().parse_args(["--array-filter", "clouds=AWS"])
    assert args.array_filter == ["clouds=AWS"]
    payload["array_fields"] = {"clouds": {"terms": {"AWS": []}}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="type and prompt"):
        SearchProfile.load(path)


@pytest.mark.parametrize("name", ["url", "../outside", "Salary", "salary_minimum", "source_urls"])
def test_array_fields_cannot_overwrite_job_columns_or_escape_cache_directory(name):
    with pytest.raises(ValueError):
        SearchCriteria("software engineer", array_fields={name: {"terms": {"X": []}}})
    with pytest.raises(ValueError):
        SearchCriteria("software engineer", array_filters={name: "anything"})


def test_extraction_does_not_change_existing_explicit_fields():
    original = job(description="Develop using TypeScript and React.",
                   array_fields={"clouds": ["AWS"], "programming_languages": ["Python"]})
    enriched = enrich_job(original)
    assert enriched.array_fields["clouds"] == ["AWS"]
    assert enriched.array_fields["programming_languages"] == ["Python"]


def test_keyword_extraction_cannot_bypass_prompt_based_generation():
    with pytest.raises(ValueError, match="type and prompt"):
        SearchCriteria("engineer", array_fields={"frameworks": {"terms": {"React": []}}})


def test_schema_extraction_uses_same_title_normalization_as_validation():
    payload = {
        "@type": ["Thing", "JobPosting"], "title": "Sr. Front-End Engineer",
        "description": "Build with TypeScript and React.",
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "Canada"},
    }
    html = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
    assert extract_page_fields(html, "Senior Frontend Engineer") == {"location": ["Remote; Canada"]}
    assert page_evidence(html, "Senior Frontend Engineer")["description"] == payload["description"]


def test_generic_location_filter_preserves_country_and_city_context(tmp_path):
    rows = [
        asdict(job(1, array_fields={"location": ["Los Angeles, CA", "Remote; Canada"]})),
        asdict(job(2, array_fields={"location": ["Vancouver, Canada"]})),
    ]
    agent = Decisions()
    criteria = SearchCriteria("software engineer", array_filters={"location": "Los Angeles, CA"})
    result = asyncio.run(filter_jobs(rows, criteria, filter_agent=agent, field_map_dir=tmp_path))
    assert len(result["jobs"]) == 1
    assert {row["raw_value"] for row in agent.calls[0][0]} == {
        "Los Angeles, CA", "Remote; Canada", "Vancouver, Canada",
    }


@pytest.mark.parametrize("problem", ["missing", "duplicate", "unknown", "nonboolean", "no_evidence"])
def test_partial_classifier_rejects_invalid_decision_join(problem):
    class BrokenAgent(Decisions):
        async def classify(self, rows, prompt):
            result = await super().classify(rows, prompt)
            if problem == "missing":
                return []
            if problem == "duplicate":
                return result + result
            if problem == "unknown":
                result[0]["value_id"] = "unknown"
            if problem == "nonboolean":
                result[0]["matches"] = "false"
            if problem == "no_evidence":
                del result[0]["canonical_value"]
            return result

    with pytest.raises(ValueError):
        asyncio.run(classify_values(
            [{"array_fields": {"frameworks": ["React"]}}], "frameworks", "React",
            agent=BrokenAgent(),
        ))


def test_timeouts_are_visible_and_retried_on_resume(tmp_path):
    rows = [{"array_fields": {"frameworks": ["React"]}}]
    path = tmp_path / "frameworks.csv"

    def timeout(request):
        raise httpx.ReadTimeout("fixture timeout", request=request)

    agent = OpenAIFieldFilterAgent(api_key="test", transport=httpx.MockTransport(timeout))
    first = asyncio.run(classify_values(rows, "frameworks", "React", path=path, agent=agent))
    assert first[0]["reason"].startswith("Unclassified: field agent timed out")
    assert first[0]["matches"] is True
    retry = Decisions()
    second = asyncio.run(classify_values(rows * 2, "frameworks", "React", path=path, agent=retry))
    assert len(retry.calls) == 1
    assert second[0]["job_count"] == 2
    cached = Decisions()
    third = asyncio.run(classify_values(rows, "frameworks", "React", path=path, agent=cached))
    assert not cached.calls
    assert third[0]["job_count"] == 1


def test_keyword_extraction_validation_cache_is_not_reused(tmp_path):
    from wagecuck_search.validation_cache import CachedValidator

    record = job()
    path = tmp_path / "validation.jsonl"
    path.write_text(json.dumps({
        "key": CachedValidator.key(record), "array_field_fingerprint": "old-keyword-extraction",
        "result": asdict(ValidationResult(
            record.url, "ats", array_fields={"programming_languages": ["Wrong"]},
        )),
    }) + "\n", encoding="utf-8")
    validator = BrowserValidator(None, SearchCriteria("engineer"))
    assert CachedValidator(validator, path).cached == {}


def test_direct_search_does_not_silently_ignore_array_filters():
    from wagecuck_search.pipeline import search

    with pytest.raises(ValueError, match="filter_csv"):
        asyncio.run(search(SearchCriteria("engineer", array_filters={"frameworks": "React"})))



@pytest.mark.parametrize("stale_fields", [
    {},
    {"languages": ["TypeScript"], "frameworks": ["React"]},
])
@pytest.mark.parametrize("page_description,expected_languages,expected_frameworks", [
    ("Python and FastAPI required. Java preferred; React is not required.", ["Python"], ["FastAPI"]),
    ("Design reliable software systems.", [], []),
])
def test_native_page_and_field_prompts_reach_agent_and_replace_stale_values(
    tmp_path, stale_fields, page_description, expected_languages, expected_frameworks
):
    definitions = {
        "languages": {"type": "array_field", "prompt": "Programming languages required for the job"},
        "frameworks": {"type": "array_field", "prompt": "Software frameworks required for the job"},
    }
    calls = []
    def respond(request):
        body = json.loads(request.content)
        payload = json.loads(body["input"])
        calls.append(payload)
        assert payload["field_definitions"] == definitions
        assert payload["job_evidence"]["description"] == page_description
        assert not set(definitions) & set(payload["job_evidence"]["array_fields"])
        result = {
            "languages": {"value": expected_languages, "evidence": page_description},
            "frameworks": {"value": expected_frameworks, "evidence": page_description},
        }
        return httpx.Response(200, json={"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}],
        }]})

    async def run():
        native = "https://boards.greenhouse.io/acme/jobs/123"
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json={"title": "Software Engineer", "company_name": "Acme",
                       "content": f"<p>{page_description}</p>"}
        ))) as client:
            criteria = SearchCriteria("software engineer")
            validator = BrowserValidator(None, criteria)
            validator.ats_verifier = AtsApiVerifier(client)
            validator.http_client = client
            record = job(description="Build TypeScript and React applications.",
                         array_fields=stale_fields)
            record.url = native
            record.sources = [{"site": "simplify", "url": native}]
            source, output = tmp_path / "source.csv", tmp_path / "validated.csv"
            write_jobs(source, [asdict(record)])
            agent = OpenAIFieldAgent(api_key="test", transport=httpx.MockTransport(respond))
            report = await validate_csv(
                source, output, criteria, validator=validator,
                field_definitions=definitions, field_agent=agent,
            )
            assert len(calls) == 1
            assert report["summary"]["field_filling"]["failed"] == 0
            validated = read_jobs(output)[0]
            assert validated["array_fields"]["languages"] == expected_languages
            assert validated["array_fields"]["frameworks"] == expected_frameworks
            assert "programming_languages" not in validated["array_fields"]
            assert validated["enrichment"]["field_definitions"] == definitions
    asyncio.run(run())
