import json
from pathlib import Path

from wagecuck import cli
from wagecuck.cli import resolve_profile_path


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
