import ast
import json
from pathlib import Path

import pytest

from wagecuck_search import cli
from wagecuck_search.models import SiteResult, utc_now
from wagecuck_search.pipeline import build_report


def test_cli_requires_title():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_cli_outputs_machine_json_and_human_summary_even_for_blocked_sites(monkeypatch, capsys):
    async def fake_search(criteria, **kwargs):
        assert criteria.seniority == ("senior", "staff")
        assert criteria.internship is False
        return build_report(
            criteria,
            "software engineer",
            [
                SiteResult(site, status="blocked", errors=["Sign-in required"])
                for site in criteria.sites
            ],
            utc_now(),
        )

    monkeypatch.setattr(cli, "search", fake_search)
    assert (
        cli.main(
            [
                "--job-title",
                "software engineer",
                "--seniority",
                "senior",
                "staff",
                "--no-internship",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert len(report["summary"]["sites"]) == 10
    assert "Total: 0 jobs; 0 duplicates removed" in captured.err


def test_search_package_never_imports_application_code():
    package = Path(__file__).resolve().parents[1] / "src" / "wagecuck_search"
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert all(name != "wagecuck" and not name.startswith("wagecuck.") for name in names)


def test_post_filter_mode_does_not_require_job_title(monkeypatch, capsys):
    async def fake_filter(source, output, criteria, **kwargs):
        assert source == Path("validated.csv")
        assert output == Path("filtered.csv")
        assert criteria.job_title == "generated jobs"
        return {"summary": {"stage": "filter", "input": 10, "returned": 3}}

    monkeypatch.setattr(cli, "filter_csv", fake_filter)
    assert cli.main([
        "--post-filter-input", "validated.csv",
        "--post-filter-output", "filtered.csv",
        "--min-salary", "180000",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["returned"] == 3


def test_post_filter_requires_distinct_input_and_output_flags():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--post-filter-input", "validated.csv"])
    assert exc.value.code == 2
