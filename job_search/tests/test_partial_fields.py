import asyncio
import csv
import json
from dataclasses import asdict

import httpx
import pytest

from wagecuck_search.ats_apis import AtsApiVerifier
from wagecuck_search.checkpoint import SavedProvider
from wagecuck_search.cli import parser
from wagecuck_search.dedupe import deduplicate
from wagecuck_search.export import read_jobs, write_jobs, write_table
from wagecuck_search.models import JobPosting, SearchCriteria, SiteResult
from wagecuck_search.partial_fields import (
    comma_separated,
    enrich_job,
    extract_page_fields,
    field_values,
    items,
    unique_values,
)
from wagecuck_search.partial_filter import OpenAIPartialFieldAgent, classify_values
from wagecuck_search.postfilter import filter_jobs
from wagecuck_search.profiles import SearchProfile
from wagecuck_search.stages import discover, filter_csv, validate_jobs
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


def test_extraction_scopes_descriptions_and_supports_custom_fields():
    html = """<nav>Java and PHP</nav><script>React JavaScript</script>
    <main><h1>Software Engineer</h1><div class=prose-job>
    Develop using Python, Python, TypeScript, C++, C#, React.js, Next.js and Django.
    Deploy to Amazon Web Services.
    </div><span class=team>Platform, Developer Experience</span></main>"""
    fields = extract_page_fields(html, "Software Engineer", {
        "clouds": {"terms": {"AWS": ["Amazon Web Services"]}},
        "teams": {"selectors": [".team"]},
    })
    assert fields["programming_languages"] == ["Python", "TypeScript", "C++", "C#"]
    assert {"React", "Next.js", "Django"} <= set(fields["frameworks"])
    assert fields["clouds"] == ["AWS"]
    assert fields["teams"] == ["Platform", "Developer Experience"]
    assert "Java" not in fields["programming_languages"]


def test_schema_extraction_ignores_other_postings_and_retains_geographic_atoms():
    payload = [
        {"@type": "JobPosting", "title": "Software Engineer",
         "description": "Build Python services using FastAPI.",
         "jobLocationType": "TELECOMMUTE",
         "applicantLocationRequirements": {"addressCountry": "Canada"}},
        {"@type": "JobPosting", "title": "Other role", "description": "Java and Spring Boot"},
    ]
    html = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
    fields = extract_page_fields(html, "Software Engineer")
    assert fields["programming_languages"] == ["Python"]
    assert fields["frameworks"] == ["FastAPI"]
    assert fields["location"] == ["Remote; Canada"]


def test_comma_lists_round_trip_compound_locations_and_custom_csv_columns(tmp_path):
    values = ["Los Angeles, CA", "Remote; Canada"]
    assert items(comma_separated(values)) == values
    record = asdict(job(partial_fields={
        "location": values, "programming_languages": ["Python", "TypeScript"],
        "frameworks": ["React"], "clouds": ["AWS", "GCP"],
    }))
    output = tmp_path / "jobs.csv"
    write_jobs(output, [record])
    with output.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["programming_languages"] == "Python,TypeScript"
    assert row["clouds"] == "AWS,GCP"
    assert read_jobs(output)[0]["partial_fields"] == record["partial_fields"]
    row["new_attribute"] = "One,Two"
    write_table(output, list(row), [row])
    assert read_jobs(output)[0]["partial_fields"]["new_attribute"] == ["One", "Two"]


def test_legacy_location_is_not_split_at_city_state_comma():
    assert field_values({"location": "Los Angeles, CA"}, "location") == ["Los Angeles, CA"]
    rows = unique_values([
        {"partial_fields": {"languages": ["Python", "python", "Java"]}},
        {"partial_fields": {"languages": "python,Rust"}},
    ], "languages")
    assert len(rows) == 3
    assert next(row for row in rows if row["raw_value"] == "Python")["job_count"] == 2


def test_search_enriches_all_providers_and_merges_fields_on_deduplication():
    first = job(description="Develop in Python with Django on AWS.")
    second = job(description="Build TypeScript services with Next.js.")
    criteria = SearchCriteria("software engineer", sites=("simplify",),
                              partial_fields={"clouds": {"terms": {"AWS": []}}})
    result = asyncio.run(discover(criteria, [
        SavedProvider(SiteResult("simplify", [first, second]))
    ]))
    fields = result["jobs"][0]["partial_fields"]
    assert fields["programming_languages"] == ["Python", "TypeScript"]
    assert fields["frameworks"] == ["Django", "Next.js"]
    assert fields["clouds"] == ["AWS"]
    assert len(result["jobs"]) == 1
    assert deduplicate([first, second])[0][0].partial_fields == fields


def test_unique_boolean_decisions_join_any_within_field_and_all_across_fields(tmp_path):
    rows = [
        asdict(job(1, partial_fields={"programming_languages": ["Python", "Java"], "frameworks": ["React"]})),
        asdict(job(2, partial_fields={"programming_languages": ["python"], "frameworks": ["Django"]})),
        asdict(job(3, partial_fields={"programming_languages": ["Java"], "frameworks": ["React"]})),
    ]
    source, output = tmp_path / "03-validation.csv", tmp_path / "04-filter.csv"
    write_jobs(source, rows)
    agent = Decisions()
    criteria = SearchCriteria("software engineer", partial_filters={
        "programming_languages": "Python", "frameworks": "React",
    })
    report = asyncio.run(filter_csv(source, output, criteria, partial_agent=agent))
    assert len(agent.calls) == 2
    assert sorted(len(call[0]) for call in agent.calls) == [2, 2]
    assert [record["url"] for record in report["jobs"]] == [rows[0]["url"]]
    assert report["summary"]["filter_reasons"] == {"frameworks": 1, "programming_languages": 1}
    with source.open(encoding="utf-8-sig", newline="") as stream:
        original = list(csv.DictReader(stream))
    with output.open(encoding="utf-8-sig", newline="") as stream:
        accepted = list(csv.DictReader(stream))
    assert accepted == original[:1]
    assert (tmp_path / ".artifacts/04-filter.partial-fields/frameworks.csv").exists()

    again = Decisions()
    asyncio.run(filter_csv(source, output, criteria, partial_agent=again))
    assert not again.calls
    changed = SearchCriteria("software engineer", partial_filters={
        "programming_languages": "Java", "frameworks": "React",
    })
    asyncio.run(filter_csv(source, output, changed, partial_agent=again))
    assert len(again.calls) == 1
    assert again.calls[0][1] == "Java"


def test_missing_values_follow_include_unknown_without_inventing_items():
    rows = [asdict(job())]
    strict = SearchCriteria("software engineer", partial_filters={"frameworks": "React"})
    permissive = SearchCriteria("software engineer", partial_filters={"frameworks": "React"},
                                 include_unknown=True)
    agent = Decisions()
    assert not asyncio.run(filter_jobs(rows, strict, partial_agent=agent))["jobs"]
    assert asyncio.run(filter_jobs(rows, permissive, partial_agent=agent))["jobs"] == rows
    assert not agent.calls


def test_generic_openai_contract_classifies_unique_values_not_jobs():
    rows = unique_values([{"partial_fields": {"frameworks": ["React", "React", "Django"]}}], "frameworks")
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

    agent = OpenAIPartialFieldAgent(api_key="test", transport=httpx.MockTransport(respond))
    result = asyncio.run(agent.classify(rows, "Frontend JavaScript frameworks"))
    assert len(seen) == 1 and len(seen[0]["values"]) == 2
    assert [row["raw_value"] for row in result if row["matches"]] == ["React"]


def test_validation_enrichment_survives_native_url_deduplication():
    class Validator:
        async def validate(self, posting):
            return ValidationResult("https://jobs.ashbyhq.com/acme/current", "ats",
                                    partial_fields={"frameworks": ["FastAPI"], "clouds": ["AWS"]})

    rows = [asdict(job(index, partial_fields={"programming_languages": [language]}))
            for index, language in [(1, "Python"), (2, "TypeScript")]]
    report = asyncio.run(validate_jobs(rows, SearchCriteria("software engineer"), Validator()))
    assert len(report["jobs"]) == 1
    assert report["jobs"][0]["partial_fields"]["programming_languages"] == ["Python", "TypeScript"]
    assert report["jobs"][0]["partial_fields"]["frameworks"] == ["FastAPI"]
    assert report["jobs"][0]["partial_fields"]["clouds"] == ["AWS"]


def test_ats_fast_path_keeps_description_for_validation_enrichment():
    async def run():
        native = "https://boards.greenhouse.io/acme/jobs/123"
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json={"title": "Software Engineer", "company_name": "Acme",
                       "content": "<p>Develop Python services using FastAPI on AWS.</p>"}
        ))) as client:
            validator = BrowserValidator(None, SearchCriteria(
                "software engineer", partial_fields={"clouds": {"terms": {"AWS": []}}}
            ))
            validator.ats_verifier = AtsApiVerifier(client)
            validator.http_client = client
            record = job()
            record.url = native
            record.sources = [{"site": "simplify", "url": native}]
            result = await validator.prepare(record)
            assert isinstance(result, ValidationResult)
            assert result.url == native and result.kind == "ats"
            fields = result.partial_fields
            assert fields["programming_languages"] == ["Python"]
            assert fields["frameworks"] == ["FastAPI"] and fields["clouds"] == ["AWS"]
    asyncio.run(run())


def test_profile_and_cli_expose_arbitrary_extraction_and_predicates(tmp_path):
    config = {"clouds": {"terms": {"AWS": ["Amazon Web Services"]}}}
    payload = {
        "name": "example", "job_titles": ["software engineer"],
        "search_queries": ["software engineer"], "output_directory": "results/example",
        "partial_fields": config,
        "filters": {"partial_fields": {"frameworks": "React or Next.js"}},
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    profile = SearchProfile.load(path)
    assert profile.filter_criteria().partial_fields == config
    assert profile.filter_criteria().partial_filters == {"frameworks": "React or Next.js"}
    args = parser().parse_args(["--partial-fields", str(path), "--partial-filter", "clouds=AWS"])
    assert args.partial_filter == ["clouds=AWS"]


@pytest.mark.parametrize("name", ["url", "../outside", "Salary", "salary_minimum", "source_urls"])
def test_partial_fields_cannot_overwrite_job_columns_or_escape_cache_directory(name):
    with pytest.raises(ValueError):
        SearchCriteria("software engineer", partial_fields={name: {"terms": {"X": []}}})
    with pytest.raises(ValueError):
        SearchCriteria("software engineer", partial_filters={name: "anything"})


def test_extraction_does_not_change_existing_explicit_fields():
    original = job(description="Develop using TypeScript and React.",
                   partial_fields={"clouds": ["AWS"], "programming_languages": ["Python"]})
    enriched = enrich_job(original)
    assert enriched.partial_fields["clouds"] == ["AWS"]
    assert enriched.partial_fields["programming_languages"] == ["Python", "TypeScript"]


def test_language_aliases_do_not_match_parts_of_framework_names():
    from wagecuck_search.partial_fields import extract_fields

    assert extract_fields("Build with Next.js, Node.js and Objective-C.")["programming_languages"] == [
        "Objective-C"
    ]
    assert extract_fields("Programming skills: JS, C, C++, and R.")["programming_languages"] == [
        "JavaScript", "C++", "R", "C"
    ]


def test_schema_extraction_uses_same_title_normalization_as_validation():
    payload = {
        "@type": ["Thing", "JobPosting"], "title": "Sr. Front-End Engineer",
        "description": "Build with TypeScript and React.",
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "Canada"},
    }
    html = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
    fields = extract_page_fields(html, "Senior Frontend Engineer")
    assert fields["programming_languages"] == ["TypeScript"]
    assert fields["location"] == ["Remote; Canada"]


def test_generic_location_filter_preserves_country_and_city_context(tmp_path):
    rows = [
        asdict(job(1, partial_fields={"location": ["Los Angeles, CA", "Remote; Canada"]})),
        asdict(job(2, partial_fields={"location": ["Vancouver, Canada"]})),
    ]
    agent = Decisions()
    criteria = SearchCriteria("software engineer", partial_filters={"location": "Los Angeles, CA"})
    result = asyncio.run(filter_jobs(rows, criteria, partial_agent=agent, partial_map_dir=tmp_path))
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
            [{"partial_fields": {"frameworks": ["React"]}}], "frameworks", "React",
            agent=BrokenAgent(),
        ))


def test_timeouts_are_visible_and_retried_on_resume(tmp_path):
    rows = [{"partial_fields": {"frameworks": ["React"]}}]
    path = tmp_path / "frameworks.csv"

    def timeout(request):
        raise httpx.ReadTimeout("fixture timeout", request=request)

    agent = OpenAIPartialFieldAgent(api_key="test", transport=httpx.MockTransport(timeout))
    first = asyncio.run(classify_values(rows, "frameworks", "React", path=path, agent=agent))
    assert first[0]["reason"].startswith("Unclassified: partial-field agent timed out")
    assert first[0]["matches"] is True
    retry = Decisions()
    second = asyncio.run(classify_values(rows * 2, "frameworks", "React", path=path, agent=retry))
    assert len(retry.calls) == 1
    assert second[0]["job_count"] == 2
    cached = Decisions()
    third = asyncio.run(classify_values(rows, "frameworks", "React", path=path, agent=cached))
    assert not cached.calls
    assert third[0]["job_count"] == 1


def test_changed_extraction_rules_change_validation_cache_fingerprint():
    from wagecuck_search.partial_fields import extraction_fingerprint

    assert extraction_fingerprint() == extraction_fingerprint({})
    assert extraction_fingerprint() != extraction_fingerprint({"clouds": {"terms": {"AWS": []}}})


def test_direct_search_does_not_silently_ignore_partial_filters():
    from wagecuck_search.pipeline import search

    with pytest.raises(ValueError, match="run_stages"):
        asyncio.run(search(SearchCriteria("engineer", partial_filters={"frameworks": "React"})))
