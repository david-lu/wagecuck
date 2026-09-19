import asyncio
import csv
import json
from dataclasses import asdict

import pytest

from wagecuck_search import csv_cli
from wagecuck_search.cli import main
from wagecuck_search.export import read_jobs, write_jobs, write_table
from wagecuck_search.field_filling import field_schema, fill_fields, validate_result
from wagecuck_search.job_fields import extraction_definitions, filter_definitions, matches_number
from wagecuck_search.models import JobPosting, SearchCriteria
from wagecuck_search.operations import filter_csv


def source_csv(tmp_path, values, *, types=None):
    source = tmp_path / "input.csv"
    columns = ["url", "title", "company", "score", "field_types_json"] if types else [
        "url", "title", "company", "score"]
    write_table(source, columns, [
        {"url": f"https://example.com/{index}", "title": "Engineer", "company": f"company-{index}",
         "score": value, "field_types_json": json.dumps(types or {})}
        for index, value in enumerate(values)
    ])
    return source


def deny_model(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Numeric filtering must never instantiate or call a model")
    monkeypatch.setattr(csv_cli, "OpenAIFieldFilterAgent", unexpected)
    monkeypatch.setattr(csv_cli, "OpenAILocationAgent", unexpected)


@pytest.mark.parametrize("condition,expected", [
    (">200", [201, 400, 401]), (">=200", [200, 201, 400, 401]),
    ("< 200", [-5, 0]), ("<=400", [-5, 0, 200, 201, 400]),
    ("==0", [0]), ("!=200", [-5, 0, 201, 400, 401]),
    ("= 400", [400]), (">= -5e0", [-5, 0, 200, 201, 400, 401]),
])
def test_numeric_cli_comparisons_are_local_and_preserve_boundaries(tmp_path, monkeypatch, capsys, condition, expected):
    source = source_csv(tmp_path, [-5, 0, 200, 201, 400, 401, ""])
    output = tmp_path / "selected.csv"
    deny_model(monkeypatch)
    assert main(["filter", str(source), "--output", str(output), "--filter", "score", condition]) == 0
    assert [job["fields"]["score"] for job in read_jobs(output)] == expected
    report = json.loads(capsys.readouterr().out)
    assert report["field_filters"]["score"]["type"] == "number_field"
    assert not report["agent_field_calls"]
    assert report["field_filter_model"] is None


def test_repeat_numeric_filter_creates_range_and_can_retain_unknown(tmp_path, monkeypatch):
    source = source_csv(tmp_path, [200, 201, 400, 401, ""])
    output = tmp_path / "range.csv"
    before = source.read_bytes()
    deny_model(monkeypatch)
    assert main(["filter", str(source), "--output", str(output), "--filter", "score", ">200",
                 "--filter", "score", "<=400", "--include-unknown"]) == 0
    assert [job["fields"]["score"] for job in read_jobs(output)] == [201, 400, None]
    assert source.read_bytes() == before
    with (tmp_path / ".artifacts/range.field-values/score.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["filter_prompt"] for row in rows} == {">200 and <=400"}
    assert {row["field_type"] for row in rows} == {"number_field"}


def test_saved_numeric_filters_and_decimal_precision(tmp_path, monkeypatch):
    source = source_csv(tmp_path, ["0.1", "0.2", "0.3", "0.30001"])
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"score": [">0.1", "<=0.3"]}))
    output = tmp_path / "selected.csv"
    deny_model(monkeypatch)
    assert main(["filter", str(source), "--output", str(output), "--filters", str(rules)]) == 0
    assert [job["fields"]["score"] for job in read_jobs(output)] == [0.2, 0.3]
    predicates = filter_definitions({"score": "==9007199254740993"})
    assert matches_number(9007199254740993, predicates["score"]["comparisons"]) is True
    assert matches_number(9007199254740992, predicates["score"]["comparisons"]) is False


@pytest.mark.parametrize("condition", [
    ">NaN", "<inf", ">1e999", ">200 or True", ">__import__('os')", ">=200;1",
    ">=200 USD", "=>200", ">=", "<= 2,000",
])
def test_invalid_numeric_expressions_rejected_before_model(tmp_path, monkeypatch, condition):
    source = source_csv(tmp_path, [200])
    output = tmp_path / "selected.csv"
    deny_model(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        main(["filter", str(source), "--output", str(output), "--filter", "score", condition])
    assert exc.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("flag,condition", [
    ("--filter", "Is this greater than 200?"),
    ("--filter-all", ">200"),
    ("--filter-none", "Is this expensive?"),
])
def test_number_field_rejects_prompts_and_array_modes(tmp_path, monkeypatch, capsys, flag, condition):
    source = source_csv(tmp_path, [200])
    deny_model(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        main(["filter", str(source), "--output", str(tmp_path / "out.csv"), flag, "score", condition])
    assert exc.value.code == 2
    assert "number_field" in capsys.readouterr().err


def test_declared_string_number_is_text_and_cannot_be_compared(tmp_path, monkeypatch, capsys):
    source = source_csv(tmp_path, ["001", "002"], types={"score": "string_field"})
    assert read_jobs(source)[0]["fields"]["score"] == "001"
    deny_model(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        main(["filter", str(source), "--output", str(tmp_path / "out.csv"), "--filter", "score", ">0"])
    assert exc.value.code == 2
    assert "string_field" in capsys.readouterr().err


def test_null_number_keeps_declared_type_and_unknown_policy(tmp_path, monkeypatch):
    source = source_csv(tmp_path, ["", ""], types={"score": "number_field"})
    output = tmp_path / "selected.csv"
    deny_model(monkeypatch)
    assert main(["filter", str(source), "--output", str(output), "--filter", "score", ">200"]) == 0
    assert read_jobs(output) == []
    assert main(["filter", str(source), "--output", str(output), "--filter", "score", ">200",
                 "--include-unknown"]) == 0
    assert len(read_jobs(output)) == 2


def test_imported_empty_number_can_be_declared_on_cli(tmp_path, monkeypatch):
    source = source_csv(tmp_path, [""])
    deny_model(monkeypatch)
    assert main(["filter", str(source), "--output", str(tmp_path / "out.csv"),
                 "--field-type", "score", "number_field", "--filter", "score", ">200"]) == 0


def test_bad_declared_number_and_boolean_are_not_coerced_into_numbers(tmp_path, monkeypatch):
    deny_model(monkeypatch)
    for value in ["not a number", "true", "NaN", "Infinity"]:
        source = source_csv(tmp_path, [value], types={"score": "number_field"})
        with pytest.raises(SystemExit) as exc:
            main(["filter", str(source), "--output", str(tmp_path / "out.csv"), "--filter", "score", ">0"])
        assert exc.value.code == 2
    definitions = {"years": {"type": "number_field", "prompt": "Required years"}}
    with pytest.raises(ValueError, match="number_field"):
        validate_result({"years": {"value": True, "evidence": "true"}}, definitions)


def test_legacy_array_csv_migrates_to_array_type_and_canonical_exports(tmp_path):
    source = tmp_path / "legacy.csv"
    write_table(source, ["url", "title", "company", "languages", "partial_fields_json"], [{
        "url": "https://example.com/job", "title": "Engineer", "company": "Acme",
        "languages": "TypeScript,Python",
        "partial_fields_json": json.dumps({"languages": ["TypeScript", "Python"]}),
    }])
    jobs = read_jobs(source)
    assert jobs[0]["array_fields"]["languages"] == ["TypeScript", "Python"]
    output = tmp_path / "new.csv"
    write_jobs(output, jobs)
    with output.open(encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        assert "partial_fields_json" not in reader.fieldnames
        row = next(reader)
    assert json.loads(row["field_types_json"])["languages"] == "array_field"


def test_generation_types_survive_csv_roundtrip_and_enforce_numeric_filter(tmp_path):
    definitions = {
        "years": {"type": "number_field", "prompt": "Required years"},
        "languages": {"type": "array_field", "prompt": "Relevant languages"},
        "role": {"type": "string_field", "options": ["frontend", "backend"], "prompt": "Role"},
    }
    class Agent:
        async def fill(self, job, definitions):
            values = {"years": 0, "languages": ["TypeScript"], "role": "frontend"}
            return {name: {"value": values[name], "evidence": "Job description"} for name in definitions}
    job = JobPosting("https://example.com/job", "Engineer", "Acme", "Remote", "csv")
    filled = asyncio.run(fill_fields([asdict(job)], definitions, agent=Agent()))
    path = tmp_path / "generated.csv"
    write_jobs(path, filled["jobs"])
    restored = read_jobs(path)[0]
    assert restored["fields"]["years"] == 0
    assert restored["field_types"]["years"] == "number_field"
    assert restored["array_fields"]["languages"] == ["TypeScript"]
    result = asyncio.run(filter_csv(path, tmp_path / "selected.csv", SearchCriteria(
        "engineer", field_filters={"years": "==0"},
    )))
    assert len(result["jobs"]) == 1
    assert field_schema(definitions["years"]) == {"type": ["number", "null"]}
    assert field_schema(definitions["languages"]) == {"type": "array", "items": {"type": "string"}}
    assert field_schema(definitions["role"])["enum"] == ["frontend", "backend", None]


def test_legacy_generation_type_names_are_normalized():
    old = {"languages": {"type": "partial", "prompt": "Languages"},
           "years": {"type": "number", "prompt": "Years"},
           "role": {"type": "enum", "options": ["frontend"], "prompt": "Role"}}
    normalized = extraction_definitions(old)
    assert {name: spec["type"] for name, spec in normalized.items()} == {
        "languages": "array_field", "years": "number_field", "role": "string_field",
    }


def test_inferred_numeric_type_survives_blank_cells_and_csv_reexport(tmp_path):
    source = source_csv(tmp_path, [200, "", 400])
    output = tmp_path / "exported.csv"
    write_jobs(output, read_jobs(source))
    jobs = read_jobs(output)
    assert [job["field_types"]["score"] for job in jobs] == ["number_field"] * 3
    report = asyncio.run(filter_csv(output, tmp_path / "selected.csv", SearchCriteria(
        "engineer", field_filters={"score": ">200"}, include_unknown=True,
    )))
    assert [job["fields"]["score"] for job in report["jobs"]] == [None, 400]


def test_unknown_value_does_not_force_in_memory_number_column_to_string():
    from wagecuck_search.postfilter import filter_jobs
    jobs = [asdict(JobPosting(f"https://example.com/{index}", "Engineer", str(index), "Remote",
                              "csv", fields={"score": score})) for index, score in enumerate([None, 300])]
    result = asyncio.run(filter_jobs(jobs, SearchCriteria("engineer", field_filters={"score": ">200"})))
    assert [job["fields"]["score"] for job in result["jobs"]] == [300]


def test_legacy_plain_location_column_is_an_array_field(tmp_path):
    from wagecuck_search.job_fields import field_type
    source = source_csv(tmp_path, [200])
    assert field_type(read_jobs(source), "location") == "array_field"
