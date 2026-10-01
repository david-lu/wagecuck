import json
from pathlib import Path

import pytest

from wagecuck_search import csv_cli
from wagecuck_search.profiles import SearchProfile


def write_profile(tmp_path, **changes):
    payload = {
        "name": "la-or-remote",
        "job_titles": ["software engineer", "frontend engineer"],
        "search_queries": ["software engineer"],
        "output_directory": "results/la-or-remote",
        "filters": {"location_prompt": "Los Angeles metropolitan area or remote"},
    } | changes
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_search_profile_owns_titles_output_and_location_filter(tmp_path):
    profile = SearchProfile.load(write_profile(tmp_path))
    assert profile.job_titles == ("software engineer", "frontend engineer")
    assert profile.search_queries == ("software engineer",)
    assert profile.output_directory.as_posix() == "results/la-or-remote"
    assert profile.location_prompt == "Los Angeles metropolitan area or remote"
    assert profile.filter_criteria().job_title == "software engineer"


def test_bundled_preliminary_presets_require_us_remote_eligibility():
    from scripts.run_preliminary_filter import LOCATION_PROMPT

    profile_path = Path(__file__).parents[2] / "profiles" / "dummy" / "search.json"
    profile = SearchProfile.load(profile_path)
    for prompt in (LOCATION_PROMPT, profile.location_prompt):
        assert "United States" in prompt
        assert "limited to another country or region" in prompt
        assert "without evidence that U.S.-based workers are eligible" in prompt


def test_named_shared_profile_drives_search_and_filter(tmp_path, monkeypatch):
    search_options = {}
    filter_options = {}

    async def fake_discover(criteria, **_kwargs):
        search_options["criteria"] = criteria
        return {"jobs": [], "summary": {"partial": False}}

    async def fake_filter(source, output, criteria, **kwargs):
        filter_options.update(criteria=criteria, location_prompt=kwargs["location_prompt"])
        return {"summary": {"partial": False}}

    monkeypatch.setattr(csv_cli, "discover", fake_discover)
    monkeypatch.setattr(csv_cli, "save_report", lambda *_args: None)
    monkeypatch.setattr(csv_cli, "read_filter_jobs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(csv_cli, "filter_csv", fake_filter)
    monkeypatch.setattr(csv_cli, "OpenAILocationAgent", lambda **_kwargs: object())

    source = tmp_path / "jobs.csv"
    source.write_text("url,title,company\n", encoding="utf-8")
    output = tmp_path / "selected.csv"
    assert csv_cli.main("search", ["--profile", "dummy", "--output", str(source)]) == 0
    assert search_options["criteria"].job_title == "software engineer"
    assert "a16z" in search_options["criteria"].sites

    assert csv_cli.main(
        "filter", [str(source), "--profile", "dummy", "--min-salary", "200000",
                   "--output", str(output)]
    ) == 0
    assert filter_options["criteria"].min_salary == 200000
    assert filter_options["criteria"].salary_currency == "USD"
    assert "United States" in filter_options["location_prompt"]


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"job_titles": []}, "job_titles"),
        ({"output_directory": "../outside"}, "output_directory"),
        ({"filters": {"unknown": True}}, "unsupported profile filters"),
    ],
)
def test_invalid_search_profiles_are_rejected(tmp_path, changes, message):
    with pytest.raises(ValueError, match=message):
        SearchProfile.load(write_profile(tmp_path, **changes))
