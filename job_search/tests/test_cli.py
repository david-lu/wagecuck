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
    assert len(report["summary"]["sites"]) == 4
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
