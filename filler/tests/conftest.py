import threading

import pytest

from wagecuck.demo import generate_profile
from wagecuck.fixtures import FixtureServer
from wagecuck.models import Profile, RunOptions


@pytest.fixture
def portal():
    server = FixtureServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", server
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def profile(tmp_path):
    profile = Profile.load(generate_profile(tmp_path / "profile"))
    # Local workflow fixtures explicitly exercise a no-sponsorship answer.
    profile.screening.work_authorization["US"].requires_sponsorship = False
    return profile


@pytest.fixture
def options(tmp_path):
    return RunOptions(
        mode="submit",
        artifacts_dir=tmp_path / "runs",
        database=tmp_path / "state.sqlite",
        confirmation_timeout_seconds=1,
        action_timeout_ms=2000,
        timeout_seconds=45,
    )
