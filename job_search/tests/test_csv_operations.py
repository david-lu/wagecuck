import asyncio
import csv
import json
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from wagecuck_search.checkpoint import SavedProvider
from wagecuck_search.cli import main
from wagecuck_search.export import read_jobs, write_jobs, write_table
from wagecuck_search.field_filling import OpenAIFieldAgent, fill_fields, validate_result
from wagecuck_search.job_fields import aggregate, extraction_definitions
from wagecuck_search.models import JobPosting, Salary, SearchCriteria, SiteResult
from wagecuck_search.operations import (
    discover,
    fill_fields_csv,
    filter_csv,
    save_report,
    validate_csv,
)
from wagecuck_search.postfilter import filter_jobs
from wagecuck_search.validation import ValidationResult

DEFINITIONS = {
    "languages": {"type": "array_field", "prompt": "Programming languages required for the job"},
    "frameworks": {"type": "array_field", "prompt": "List the relevant frameworks"},
    "role": {"type": "string_field", "options": ["backend", "frontend", "full_stack"],
             "prompt": "Classify the engineering responsibilities"},
    "is_backend": {"type": "boolean_field", "prompt": "Are backend duties present?"},
}


def job(company="Web", **overrides):
    values = dict(
        url=f"https://example.com/{company}", title="Software Engineer", company=company,
        location="Remote; United States", source="simplify",
        description="Build TypeScript and React user interfaces.", salary=Salary(190000, 210000, "USD", "year"),
    )
    return JobPosting(**(values | overrides))


class FillingAgent:
    def __init__(self):
        self.jobs = []
        self.active = self.maximum = 0

    async def fill(self, record, definitions):
        self.jobs.append(record)
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0.001)
        self.active -= 1
        backend = record["company"] == "Backend"
        values = {
            "languages": ["TypeScript", "Go"] if record["company"] == "Mixed" else ["TypeScript"],
            "frameworks": ["React"], "role": "backend" if backend else "frontend",
            "is_backend": backend,
        }
        return {key: {"value": values[key], "evidence": record.get("description") or "Job title"}
                for key in definitions}


class PredicateAgent:
    def __init__(self):
        self.calls = []

    async def classify(self, rows, prompt):
        self.calls.append((rows, prompt))
        def decision(row):
            value = row["raw_value"]
            return {"role": value in ("frontend", "full_stack"),
                    "languages": value in ("TypeScript", "JavaScript", "Python"),
                    "frameworks": value in ("React", "Django"),
                    "is_backend": value == "false",
                    "title": "Software" in value}.get(row["field_name"], True)
        return [{**row, "canonical_value": row["raw_value"], "matches": decision(row),
                 "reason": "Fixture predicate"} for row in rows]


def test_scalar_and_partial_csv_roundtrip_and_explicit_external_types(tmp_path):
    record = asdict(job(fields={"is_backend": False, "years": 0, "role": "frontend",
                                "comment": "First, second"},
                        array_fields={"languages": ["TypeScript", "Python"]}))
    path = tmp_path / "typed.csv"
    write_jobs(path, [record])
    restored = read_jobs(path)[0]
    assert restored["fields"] == record["fields"]
    assert restored["array_fields"]["languages"] == ["TypeScript", "Python"]
    external = tmp_path / "external.csv"
    write_table(external, ["url", "title", "company", "tags", "comment"], [{
        "url": record["url"], "title": record["title"], "company": record["company"],
        "tags": "Platform,Web", "comment": "A note, with a comma",
    }])
    row = read_jobs(external, array_columns=["tags"])[0]
    assert row["location"] == "Unknown"
    assert row["array_fields"]["tags"] == ["Platform", "Web"]
    assert row["fields"]["comment"] == "A note, with a comma"


def test_prompt_fill_is_independent_preserves_rows_and_caches_by_evidence(tmp_path):
    source, output = tmp_path / "input.csv", tmp_path / "filled.csv"
    original = asdict(job(fields={"custom_value": "keep me"}))
    write_jobs(source, [original])
    agent = FillingAgent()
    report = asyncio.run(fill_fields_csv(source, output, DEFINITIONS, agent=agent))
    filled = read_jobs(output)[0]
    assert len(report["jobs"]) == 1 and report["summary"]["failed"] == 0
    assert filled["fields"] == {"custom_value": "keep me", "role": "frontend", "is_backend": False}
    assert filled["array_fields"]["languages"] == ["TypeScript"]
    assert filled["enrichment"]["evidence"]["role"]
    assert read_jobs(source)[0]["fields"] == original["fields"]
    asyncio.run(fill_fields_csv(source, output, DEFINITIONS, agent=agent))
    assert len(agent.jobs) == 1
    changed = {**DEFINITIONS, "role": {**DEFINITIONS["role"], "prompt": "A different prompt"}}
    asyncio.run(fill_fields_csv(source, output, changed, agent=agent))
    assert len(agent.jobs) == 2


def test_filling_uses_bounded_workers():
    agent = FillingAgent()
    records = [asdict(job(str(i))) for i in range(12)]
    report = asyncio.run(fill_fields(records, DEFINITIONS, agent=agent, workers=3))
    assert len(report["jobs"]) == 12
    assert agent.maximum == 3


@pytest.mark.parametrize("kind,value,options", [
    ("boolean", "false", None), ("number", True, None),
    ("enum", "mobile", ["frontend", "backend"]), ("partial", "Python,Go", None),
])
def test_generated_values_must_match_declared_type(kind, value, options):
    definition = {"type": kind, "prompt": "Extract this field"}
    if options:
        definition["options"] = options
    with pytest.raises(ValueError):
        validate_result({"test": {"value": value, "evidence": "Some evidence"}}, {"test": definition})


def test_generation_failure_is_not_success_and_is_not_cached(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("fixture", request=request)
    agent = OpenAIFieldAgent(api_key="test", transport=httpx.MockTransport(timeout))
    path = tmp_path / "cache.jsonl"
    report = asyncio.run(fill_fields([asdict(job())], DEFINITIONS, agent=agent, cache_path=path))
    assert report["summary"]["failed"] == 1
    assert report["jobs"][0]["enrichment"]["error"] == "MODEL_TIMEOUT"
    assert report["jobs"][0]["fields"]["role"] is None
    assert report["jobs"][0]["array_fields"]["languages"] == []
    assert not path.exists()


def test_real_agent_contract_uses_requested_prompts_and_nullable_typed_schema():
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        payload = json.loads(body["input"])
        assert payload["field_definitions"] == DEFINITIONS
        schema = body["text"]["format"]["schema"]["properties"]
        assert schema["is_backend"]["properties"]["value"]["type"] == ["boolean", "null"]
        assert schema["role"]["properties"]["value"]["enum"] == ["backend", "frontend", "full_stack", None]
        assert schema["languages"]["properties"]["value"]["type"] == "array"
        values = {"role": "frontend", "is_backend": False, "languages": ["TypeScript"],
                  "frameworks": ["React"]}
        output = {key: {"value": value, "evidence": "Build TypeScript and React user interfaces."}
                  for key, value in values.items()}
        return httpx.Response(200, json={"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}],
        }]})
    agent = OpenAIFieldAgent(api_key="test", transport=httpx.MockTransport(respond))
    result = asyncio.run(fill_fields([asdict(job())], DEFINITIONS, agent=agent))
    assert len(requests) == 1 and result["summary"]["failed"] == 0
    assert result["jobs"][0]["fields"]["is_backend"] is False


@pytest.mark.parametrize("values,mode,expected", [
    ([True, False], "any", True), ([True, False], "all", False),
    ([False, False], "none", True), ([True, False], "none", False),
    ([], "none", None), ([True, None], "all", None), ([False, None], "none", None),
    ([True, None], "any", True),
])
def test_filter_aggregation_handles_unknowns(values, mode, expected):
    assert aggregate(values, mode) is expected


def test_generic_filter_handles_regular_boolean_core_and_array_fields(tmp_path):
    records = [
        asdict(job("Web", fields={"role": "frontend", "is_backend": False},
                   array_fields={"languages": ["TypeScript"]})),
        asdict(job("Mixed", fields={"role": "full_stack", "is_backend": True},
                   array_fields={"languages": ["TypeScript", "Go"]})),
    ]
    agent = PredicateAgent()
    criteria = SearchCriteria("jobs", field_filters={
        "role": "Frontend or full-stack?", "languages": {"prompt": "Web languages?", "mode": "all"},
        "title": "Software engineer?", "is_backend": "Is this false?",
    })
    result = asyncio.run(filter_jobs(records, criteria, filter_agent=agent, field_map_dir=tmp_path))
    assert [row["company"] for row in result["jobs"]] == ["Web"]
    assert len(agent.calls) == 4
    assert len(next(rows for rows, _ in agent.calls if rows[0]["field_name"] == "title")) == 1


def test_validation_and_field_filling_can_be_composed_on_any_job_csv(tmp_path):
    source, validated, filled = [tmp_path / name for name in ("input.csv", "valid.csv", "filled.csv")]
    write_jobs(source, [asdict(job(description="", fields={"external_tag": "keep"}))])
    class Validator:
        async def validate(self, record):
            return ValidationResult("https://jobs.ashbyhq.com/acme/current", "ats",
                                    description="Actual page: Build TypeScript and React user interfaces.")
    report = asyncio.run(validate_csv(source, validated, validator=Validator()))
    assert report["jobs"][0]["description"].startswith("Actual page:")
    assert report["jobs"][0]["fields"]["external_tag"] == "keep"
    agent = FillingAgent()
    asyncio.run(fill_fields_csv(validated, filled, DEFINITIONS, agent=agent))
    assert agent.jobs[0].get("description", "") == ""
    combined_agent = FillingAgent()
    combined = asyncio.run(validate_csv(
        source, tmp_path / "combined.csv", validator=Validator(),
        field_definitions=DEFINITIONS, field_agent=combined_agent,
    ))
    assert combined["jobs"][0]["fields"]["role"] == "frontend"
    assert combined_agent.jobs[0]["description"].startswith("Actual page:")


def test_independent_operations_can_filter_before_and_after_enrichment(tmp_path):
    class Provider:
        site = "simplify"
        async def fetch(self, query, criteria):
            assert query == "software engineer"
            return SiteResult("simplify", [
                job("Web"), job("Mixed"), job("Backend", location="Los Angeles, CA"),
                job("Low", salary=Salary(120000, 150000, "USD", "year")),
                job("NewYork", location="New York, NY", workplace="onsite"),
                job("UnknownSalary", salary=None),
            ])
    class LocationAgent:
        async def classify(self, rows, prompt):
            return [{**row, "canonical_location": row["raw_location"],
                     "matches": "Remote" in row["raw_location"] or "Los Angeles" in row["raw_location"],
                     "reason": "Fixture location"} for row in rows]
    class Validator:
        def __init__(self):
            self.companies = []
        async def validate(self, record):
            self.companies.append(record.company)
            return ValidationResult(
                f"https://jobs.ashbyhq.com/acme/{record.company}", "ats",
                description=f"Native page for {record.company}: TypeScript and React.",
            )
    validator, filler = Validator(), FillingAgent()
    async def run():
        criteria = SearchCriteria("software engineer", sites=("simplify",), min_salary=180000,
                                  include_unknown=True)
        discovered = await discover(criteria, providers=[Provider()])
        save_report(tmp_path / "anything.csv", discovered)
        preliminary = await filter_csv(
            tmp_path / "anything.csv", tmp_path / "salary-location.csv", criteria,
            location_prompt="Remote or Los Angeles", agent=LocationAgent(),
        )
        await validate_csv(
            tmp_path / "salary-location.csv", tmp_path / "enriched.csv",
            validator=validator, field_definitions=DEFINITIONS, field_agent=filler,
        )
        final = await filter_csv(
            tmp_path / "enriched.csv", tmp_path / "chosen.csv",
            SearchCriteria("software engineer", field_filters={
                "role": "Frontend or full-stack",
                "languages": {"prompt": "Web languages", "mode": "all"},
            }), filter_agent=PredicateAgent(),
        )
        return preliminary, final
    preliminary, final = asyncio.run(run())
    assert validator.companies == ["Web", "Mixed", "Backend", "UnknownSalary"]
    assert [record["company"] for record in read_jobs(tmp_path / "chosen.csv")] == ["Web", "UnknownSalary"]
    assert preliminary["summary"]["returned"] == 4 and final["summary"]["returned"] == 2
    assert (tmp_path / ".artifacts/chosen.field-values/role.csv").exists()


def test_example_definitions_and_filters_are_reusable_configuration():
    base = Path(__file__).parents[1] / "examples"
    definitions = extraction_definitions(json.loads((base / "validation-fields.json").read_text()))
    criteria = SearchCriteria("engineer", field_filters=json.loads((base / "web-filters.json").read_text()))
    assert definitions["role"]["options"] == ["backend", "frontend", "full_stack"]
    assert definitions["languages"] == {
        "type": "array_field", "prompt": "Programming languages required for the job",
    }
    assert "minimum_experience" not in definitions
    assert criteria.field_filters["languages"]["mode"] == "all"


def test_empty_csv_still_exports_requested_field_columns(tmp_path):
    source, output = tmp_path / "empty.csv", tmp_path / "filled.csv"
    write_jobs(source, [])
    report = asyncio.run(fill_fields_csv(source, output, DEFINITIONS))
    assert report["summary"]["returned"] == 0
    with output.open(encoding="utf-8-sig", newline="") as stream:
        assert set(DEFINITIONS) <= set(csv.DictReader(stream).fieldnames)


@pytest.mark.parametrize("action", ["validate", "fill-fields", "filter"])
def test_csv_commands_are_exposed_by_main_cli(action):
    with pytest.raises(SystemExit) as exc:
        main([action, "--help"])
    assert exc.value.code == 0


@pytest.mark.parametrize("definition", [
    {"role": {"type": "string_field", "prompt": "Classify", "options": []}},
    {"url": {"type": "string_field", "prompt": "Overwrite identity"}},
    {"test": {"type": "boolean_field", "prompt": ""}},
    {"test": {"type": "array_field"}},
    {"test": {"type": "string_field", "prompt": "  "}},
    {"test": {"terms": {"Python": []}}},
    {"test": {"selectors": [".skills"]}},
])
def test_bad_extraction_definitions_fail_before_navigation(definition):
    with pytest.raises(ValueError):
        extraction_definitions(definition)


def test_explicit_scalar_types_override_builtin_csv_column_defaults(tmp_path):
    path = tmp_path / "scalar.csv"
    write_jobs(path, [asdict(job(fields={"languages": "Python, TypeScript", "frameworks": "React"}))])
    restored = read_jobs(path)[0]
    assert restored["fields"]["languages"] == "Python, TypeScript"
    assert restored["fields"]["frameworks"] == "React"
    assert "frameworks" not in restored["array_fields"]


def test_validation_cache_is_scoped_to_job_identity():
    from wagecuck_search.validation_cache import CachedValidator
    first = job()
    changed = job(title="Different Role")
    assert CachedValidator.key(first) != CachedValidator.key(changed)


def test_salary_cutoff_can_use_minimum_or_maximum():
    from wagecuck_search.matching import matches
    record = job(salary=Salary(150000, 210000, "USD", "year"))
    assert matches(record, SearchCriteria("software engineer", min_salary=180000))[0]
    assert not matches(record, SearchCriteria(
        "software engineer", min_salary=180000, salary_basis="minimum",
    ))[0]


def test_revalidating_native_csv_url_does_not_require_original_board_to_be_online():
    from wagecuck_search.ats_apis import AtsApiVerifier
    from wagecuck_search.validation import BrowserValidator

    async def run():
        seen = []
        def respond(request):
            seen.append(str(request.url))
            return httpx.Response(200, json={
                "title": "Software Engineer", "company_name": "Web",
                "content": "<p>Build TypeScript and React applications.</p>",
            })
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            validator = BrowserValidator(None, SearchCriteria("software engineer"))
            validator.http_client = client
            validator.ats_verifier = AtsApiVerifier(client)
            record = job(url="https://boards.greenhouse.io/web/jobs/123",
                         sources=[{"site": "linkedin", "url": "https://linkedin.com/jobs/view/old"}])
            result = await validator.prepare(record)
            assert isinstance(result, ValidationResult)
            assert result.url == record.url
            assert len(seen) == 1 and "greenhouse" in seen[0]
    asyncio.run(run())


def test_validation_retains_description_when_board_title_omits_native_qualifier():
    from wagecuck_search.array_fields import extract_page_fields, page_evidence
    from wagecuck_search.validation import destination_problem
    record = {
        "@type": "JobPosting", "title": "Software Engineer, Agent Auth Experience",
        "hiringOrganization": {"name": "Web"}, "directApply": True,
        "description": "Build React interfaces and TypeScript authentication services.",
    }
    html = '<title>Web</title><h1>' + record["title"] + '</h1><script type="application/ld+json">' + json.dumps(record) + '</script>'
    assert destination_problem(job(), html, "https://jobs.ashbyhq.com/web/1", 200, [])[0] is None
    assert "authentication services" in page_evidence(html, "Software Engineer")["description"]
    assert extract_page_fields(html, "Software Engineer") == {}


def test_javascript_boot_notice_is_excluded_from_agent_evidence():
    from wagecuck_search.array_fields import page_evidence
    html = "<noscript>You need to enable JavaScript to run this app.</noscript><h1>Engineer</h1>"
    assert "JavaScript" not in page_evidence(html, "Engineer")["description"]


def test_ambiguous_qualified_titles_do_not_combine_other_jobs():
    from wagecuck_search.array_fields import page_evidence
    records = [
        {"@type": "JobPosting", "title": "Software Engineer, Backend", "description": "Python"},
        {"@type": "JobPosting", "title": "Software Engineer, Frontend", "description": "TypeScript"},
    ]
    html = '<script type="application/ld+json">' + json.dumps(records) + '</script>'
    assert page_evidence(html, "Software Engineer")["description"] == ""


def test_unclassified_generic_filter_values_do_not_satisfy_all_or_none():
    class TimeoutDecisions:
        async def classify(self, rows, prompt):
            return [{**row, "canonical_value": row["raw_value"], "matches": True,
                     "reason": "Unclassified: fixture timeout"} for row in rows]
    records = [asdict(job(array_fields={"languages": ["TypeScript"]}))]
    for mode in ("any", "all", "none"):
        strict = SearchCriteria("jobs", field_filters={"languages": {"prompt": "Web?", "mode": mode}})
        assert not asyncio.run(filter_jobs(records, strict, filter_agent=TimeoutDecisions()))["jobs"]



def test_search_and_preliminary_do_not_add_technology_fields(tmp_path):
    search_path = tmp_path / "search.csv"
    preliminary_path = tmp_path / "preliminary.csv"
    report = asyncio.run(discover(
        SearchCriteria("software engineer", sites=("simplify",)),
        [SavedProvider(SiteResult("simplify", [job()]))],
    ))
    save_report(search_path, report)
    filtered = asyncio.run(filter_csv(
        search_path, preliminary_path,
        SearchCriteria("software engineer", field_filters={
            "salary.minimum": ">=180000",
        }),
    ))
    assert len(filtered["jobs"]) == 1
    technology_fields = {"languages", "programming_languages", "frameworks"}
    for path in (search_path, preliminary_path):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            assert technology_fields.isdisjoint(csv.DictReader(stream).fieldnames)
        saved = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        for record in [*saved["jobs"], *read_jobs(path)]:
            for container in ("array_fields", "fields", "field_types"):
                assert technology_fields.isdisjoint(record.get(container, {}))
            assert "description" not in record
    assert "TypeScript" in report["jobs"][0]["description"]


def test_validation_does_not_use_board_snippet_when_page_text_is_missing(tmp_path):
    class Validator:
        async def validate(self, record):
            return ValidationResult("https://jobs.ashbyhq.com/acme/1", "ats")

    source, output = tmp_path / "source.csv", tmp_path / "validated.csv"
    write_jobs(source, [asdict(job())])
    agent = FillingAgent()
    report = asyncio.run(validate_csv(
        source, output, validator=Validator(),
        field_definitions=DEFINITIONS, field_agent=agent,
    ))
    assert report["jobs"][0]["description"] == ""
    assert agent.jobs[0]["description"] == ""
    assert read_jobs(output)[0].get("description", "") == ""


def test_description_is_internal_agent_evidence_not_an_output_field(tmp_path):
    source, output = tmp_path / "source.csv", tmp_path / "validated.csv"
    write_table(source, ["url", "title", "company", "description"], [{
        "url": "https://example.com/job", "title": "Frontend Engineer", "company": "Acme",
        "description": "Build TypeScript interfaces with React.",
    }])
    agent = FillingAgent()
    asyncio.run(fill_fields_csv(source, output, DEFINITIONS, agent=agent))
    assert agent.jobs[0]["description"] == "Build TypeScript interfaces with React."
    with output.open(encoding="utf-8-sig", newline="") as stream:
        assert "description" not in csv.DictReader(stream).fieldnames
    saved = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert "description" not in saved["jobs"][0]
    assert set(saved["jobs"][0]["fields"]) == {"role", "is_backend"}
    assert set(saved["jobs"][0]["array_fields"]) >= {"languages", "frameworks"}



@pytest.mark.parametrize("definition", [
    {"languages": {"type": "array_field"}},
    {"languages": {"type": "array_field", "prompt": " "}},
    {"languages": {"terms": {"Python": []}}},
])
def test_validation_cli_rejects_unprompted_fields_before_agent_or_browser(
    tmp_path, monkeypatch, capsys, definition
):
    from wagecuck_search import csv_cli

    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid definitions must fail before constructing an agent or navigating")
    monkeypatch.setattr(csv_cli, "OpenAIFieldAgent", unexpected)
    monkeypatch.setattr(csv_cli, "validate_csv", unexpected)
    source, output, definitions = tmp_path / "source.csv", tmp_path / "out.csv", tmp_path / "fields.json"
    write_jobs(source, [asdict(job())])
    definitions.write_text(json.dumps(definition), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["validate", str(source), "--output", str(output), "--fields", str(definitions)])
    assert exc.value.code == 2
    assert "prompt" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("flag", ["--fields", "--array-fields"])
def test_cli_field_definitions_reach_agent_and_are_saved(tmp_path, monkeypatch, flag):
    from wagecuck_search import csv_cli

    class Agent(FillingAgent):
        async def fill(self, record, definitions):
            assert definitions == DEFINITIONS
            return await super().fill(record, definitions)
    agent = Agent()
    monkeypatch.setattr(csv_cli, "OpenAIFieldAgent", lambda **kwargs: agent)
    source, output, definitions = tmp_path / "source.csv", tmp_path / "out.csv", tmp_path / "fields.json"
    write_jobs(source, [asdict(job())])
    definitions.write_text(json.dumps(DEFINITIONS), encoding="utf-8")
    assert main(["fill-fields", str(source), "--output", str(output), flag, str(definitions)]) == 0
    assert len(agent.jobs) == 1
    assert read_jobs(output)[0]["enrichment"]["field_definitions"] == DEFINITIONS


def test_adding_more_fields_preserves_existing_prompts_and_evidence():
    async def run():
        first = await fill_fields(
            [asdict(job())], {"languages": DEFINITIONS["languages"]}, agent=FillingAgent(),
        )
        second = await fill_fields(
            first["jobs"], {"role": DEFINITIONS["role"]}, agent=FillingAgent(),
        )
        enriched = second["jobs"][0]["enrichment"]
        assert enriched["field_definitions"] == {
            "languages": DEFINITIONS["languages"], "role": DEFINITIONS["role"],
        }
        assert enriched["evidence"]["languages"] == first["jobs"][0]["enrichment"]["evidence"]["languages"]
        assert set(enriched["evidence"]) == {"languages", "role"}
    asyncio.run(run())
