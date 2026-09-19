import csv
import json
from dataclasses import asdict

import pytest

from wagecuck_search import csv_cli
from wagecuck_search.cli import main
from wagecuck_search.export import read_jobs, write_table
from wagecuck_search.models import JobPosting


class Decisions:
    def __init__(self):
        self.calls = []

    async def classify(self, rows, prompt):
        self.calls.append((rows, prompt))
        allowed = {
            "web role": {"frontend", "full_stack"},
            "web language": {"TypeScript", "Python"},
            "systems language": {"Go", "C++"},
            "not backend": {"false"},
            "zero": {"0"},
            "cloud": {"AWS"},
            "comment": {"Hello, world"},
            "salary": {"210000.0"},
        }[prompt]
        return [{**row, "canonical_value": row["raw_value"],
                 "matches": row["raw_value"] in allowed, "reason": "Test decision"}
                for row in rows]


def input_csv(tmp_path, rows):
    path = tmp_path / "unrelated-name.csv"
    columns = ["url", "title", "company", *dict.fromkeys(key for row in rows for key in row)]
    columns = list(dict.fromkeys(columns))
    write_table(path, columns, [
        {"url": f"https://example.com/{index}", "title": "Software Engineer", "company": str(index), **row}
        for index, row in enumerate(rows)
    ])
    return path


def raw_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def use_agent(monkeypatch):
    agent = Decisions()
    monkeypatch.setattr(csv_cli, "OpenAIFieldFilterAgent", lambda **kwargs: agent)
    return agent


def test_cli_repeatable_filters_preserve_exact_duplicate_url_row(tmp_path, monkeypatch, capsys):
    source = input_csv(tmp_path, [
        {"url": "https://same.example/job", "role": "backend", "languages": "Go", "note": "first"},
        {"url": "https://same.example/job", "role": "frontend", "languages": "TypeScript,Python", "note": "second"},
        {"role": "full_stack", "languages": "TypeScript,Go"},
        {"role": "frontend", "languages": "TypeScript"},
    ])
    before = source.read_bytes()
    output = tmp_path / "my-output.csv"
    agent = use_agent(monkeypatch)
    assert main(["filter", str(source), "--output", str(output),
                 "--filter", "role", "web role", "--filter-all", "languages", "web language"]) == 0
    columns, rows = raw_rows(source)
    assert raw_rows(output) == (columns, [rows[1], rows[3]])
    assert source.read_bytes() == before
    assert len(agent.calls) == 2
    assert {row["raw_value"] for row in agent.calls[1][0]} == {"TypeScript", "Python", "Go"}
    summary = json.loads(capsys.readouterr().out)
    assert summary["returned"] == 2
    decisions = tmp_path / ".artifacts/my-output.field-values/languages.csv"
    assert decisions.exists()
    # Repeat on the same explicit output reuses prompt-scoped decisions.
    agent.calls.clear()
    assert main(["filter", str(source), "--output", str(output),
                 "--filter", "role", "web role", "--filter-all", "languages", "web language"]) == 0
    assert not agent.calls


@pytest.mark.parametrize("flag,prompt,extra,expected", [
    ("--filter", "web language", [], ["0", "1"]),
    ("--filter-all", "web language", [], ["1"]),
    ("--filter-none", "systems language", [], ["1"]),
    ("--filter-all", "web language", ["--include-unknown"], ["1", "2"]),
])
def test_cli_partial_modes_join_decisions_back_to_rows(tmp_path, monkeypatch, flag, prompt, extra, expected):
    source = input_csv(tmp_path, [{"languages": "TypeScript,Go"}, {"languages": "TypeScript"},
                                 {"languages": ""}])
    output = tmp_path / "result.csv"
    agent = use_agent(monkeypatch)
    assert main(["filter", str(source), "--output", str(output),
                 flag, "languages", prompt, *extra]) == 0
    assert [row["company"] for row in raw_rows(output)[1]] == expected
    assert len(agent.calls[0][0]) == 2


def test_cli_custom_partial_scalar_boolean_zero_and_saved_predicates(tmp_path, monkeypatch):
    source = input_csv(tmp_path, [
        {"is_backend": "false", "years": "0", "clouds": "AWS,GCP", "comment": "Hello, world"},
        {"is_backend": "true", "years": "3", "clouds": "Azure", "comment": "Other"},
    ])
    predicates = tmp_path / "rules.json"
    predicates.write_text(json.dumps({"is_backend": "not backend", "years": "==0"}))
    output = tmp_path / "selected.csv"
    agent = use_agent(monkeypatch)
    assert main(["filter", str(source), "--output", str(output), "--filters", str(predicates),
                 "--array-column", "clouds", "--filter", "clouds", "cloud",
                 "--filter", "comment", "comment"]) == 0
    assert len(read_jobs(output, array_columns=["clouds"])) == 1
    values = {prompt: {row["raw_value"] for row in rows} for rows, prompt in agent.calls}
    assert values["not backend"] == {"false", "true"}
    assert "zero" not in values
    assert values["comment"] == {"Hello, world", "Other"}
    assert values["cloud"] == {"AWS", "GCP", "Azure"}


@pytest.mark.parametrize("field", ["salary_maximum", "salary.maximum"])
def test_cli_can_target_flat_or_nested_salary_field(tmp_path, monkeypatch, field):
    source = input_csv(tmp_path, [{"salary_maximum": "210000"}, {"salary_maximum": "160000"}])
    output = tmp_path / "selected.csv"
    use_agent(monkeypatch)
    assert main(["filter", str(source), "--output", str(output), "--filter", field, ">=200000"]) == 0
    assert [row["company"] for row in raw_rows(output)[1]] == ["0"]


@pytest.mark.parametrize("arguments,message", [
    (["--filter", "typo", "web role"], "Unknown filter field"),
    (["--filter", "role", ""], "nonempty"),
    (["--filter", "role", "web role", "--filter-all", "role", "web role"], "Duplicate filter"),
    (["--filter", "../role", "web role"], "identifiers"),
    ([], "Provide --filter"),
])
def test_invalid_filter_rejected_before_agent_or_output(tmp_path, monkeypatch, capsys, arguments, message):
    source = input_csv(tmp_path, [{"role": "frontend"}])
    output = tmp_path / "selected.csv"
    def unexpected(**kwargs):
        raise AssertionError("Invalid input reached the model client")
    monkeypatch.setattr(csv_cli, "OpenAIFieldFilterAgent", unexpected)
    with pytest.raises(SystemExit) as exc:
        main(["filter", str(source), "--output", str(output), *arguments])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert not output.exists()


def test_search_command_only_discovers_to_user_supplied_path(tmp_path, monkeypatch, capsys):
    async def discover(criteria, **kwargs):
        assert criteria.job_title == "software engineer"
        assert criteria.sites == ("a16z",)
        return {"jobs": [asdict(JobPosting("https://example.com/job", "Software Engineer", "Acme",
                                          "Remote", "a16z", description="Build TypeScript interfaces"))],
                "summary": {"returned": 1, "partial": False}}
    async def unexpected(*args, **kwargs):
        raise AssertionError("Search invoked another operation")
    monkeypatch.setattr(csv_cli, "discover", discover)
    monkeypatch.setattr(csv_cli, "validate_csv", unexpected)
    monkeypatch.setattr(csv_cli, "filter_csv", unexpected)
    output = tmp_path / "my" / "chosen-file.csv"
    assert main(["search", "--query", "software engineer", "--sites", "a16z",
                 "--output", str(output)]) == 0
    assert read_jobs(output)[0]["description"] == "Build TypeScript interfaces"
    assert json.loads(capsys.readouterr().out)["returned"] == 1
    assert not (tmp_path / "01-search.csv").exists()


def test_fixed_stage_flags_are_removed(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--stages-output-dir", "result"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_partial_classification_sets_nonzero_exit_but_exports_results(tmp_path, monkeypatch, capsys):
    source = input_csv(tmp_path, [{"languages": "TypeScript"}])
    output = tmp_path / "result.csv"
    class Unknown:
        async def classify(self, rows, prompt):
            return [{**row, "canonical_value": row["raw_value"], "matches": True,
                     "reason": "Unclassified: timeout"} for row in rows]
    monkeypatch.setattr(csv_cli, "OpenAIFieldFilterAgent", lambda **kwargs: Unknown())
    assert main(["filter", str(source), "--output", str(output),
                 "--filter", "languages", "web language"]) == 1
    assert raw_rows(output)[1] == []
    assert json.loads(capsys.readouterr().out)["partial"] is True
