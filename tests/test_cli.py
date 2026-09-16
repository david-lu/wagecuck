from pathlib import Path

from wagecuck.cli import resolve_profile_path


def test_short_profile_name_resolves_under_profiles():
    assert resolve_profile_path("ryan") == Path("profiles/ryan/profile.json")


def test_explicit_profile_path_is_preserved():
    path = Path("profiles/private/me.json")
    assert resolve_profile_path(path) == path
