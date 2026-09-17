import json

import pytest

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
