import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wagecuck import cli
from wagecuck.cli import resolve_profile_path
from wagecuck.demo import generate_profile


def test_short_profile_name_resolves_under_profiles():
    assert resolve_profile_path("ryan") == Path("profiles/ryan/profile.json")


def test_explicit_profile_path_is_preserved():
    path = Path("profiles/private/me.json")
    assert resolve_profile_path(path) == path


def test_run_writes_explicit_output(tmp_path, monkeypatch, capsys):
    output = tmp_path / "nested" / "result.json"

    class Result:
        status = "succeeded"

        def model_dump(self, *, mode):
            assert mode == "json"
            return {
                "success": True,
                "status": self.status,
                "code": "APPLICATION_SUBMITTED",
            }

    class Runner:
        def __init__(self, agent):
            assert agent is None

        async def run(self, url, profile, options):
            assert url == "https://example.test/apply"
            assert options.mode == "submit"
            return Result()

    monkeypatch.setattr(cli, "create_agent", lambda args: None)
    monkeypatch.setattr(cli.Profile, "load", lambda path: object())
    monkeypatch.setattr(cli, "ApplicationRunner", Runner)

    exit_code = cli.main(
        [
            "run",
            "https://example.test/apply",
            "--profile",
            "test",
            "--mode",
            "submit",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["code"] == "APPLICATION_SUBMITTED"
    assert str(output.resolve()) in capsys.readouterr().out


def test_dry_run_uses_live_fill_pipeline_with_synthetic_profile(monkeypatch):
    seen = {}

    class Result:
        status = "ready"

        def model_dump(self, *, mode):
            return {"status": self.status, "code": "READY", "mode": "fill"}

    class Runner:
        def __init__(self, agent):
            pass

        async def run(self, url, profile, options):
            seen.update(url=url, profile=profile, options=options)
            return Result()

    profile = SimpleNamespace(synthetic=True)
    monkeypatch.setattr(cli, "create_agent", lambda args: None)
    monkeypatch.setattr(cli.Profile, "load", lambda path: profile)
    monkeypatch.setattr(cli, "ApplicationRunner", Runner)

    assert cli.main(["run", "https://example.test/apply", "--dry-run"]) == 0
    assert seen["profile"] is profile
    assert seen["options"].mode == "fill"


def test_dry_run_rejects_real_profile_data(monkeypatch):
    monkeypatch.setattr(cli, "create_agent", lambda args: None)
    monkeypatch.setattr(
        cli.Profile, "load", lambda path: SimpleNamespace(synthetic=False)
    )

    with pytest.raises(SystemExit):
        cli.main(["run", "https://example.test/apply", "--dry-run"])


def test_dry_run_fills_live_local_form_without_submitting(portal, tmp_path, monkeypatch):
    base, server = portal
    profile_path = generate_profile(tmp_path / "profile")
    output = tmp_path / "result.json"
    monkeypatch.setattr(cli, "create_agent", lambda args: None)

    exit_code = cli.main(
        [
            "run",
            f"{base}/single",
            "--profile",
            str(profile_path),
            "--dry-run",
            "--output",
            str(output),
        ]
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert result["status"] == "ready"
    assert result["mode"] == "dry-run"
    assert result["execution_mode"] == "fill"
    assert result["network_disabled_before_filling"] is False
    assert result["final_submission_enabled"] is False
    assert result["fields_filled"] > 0
    assert result["submission_attempted"] is False
    assert result["submitted"] is False
    assert server.submissions == []
