import json
from pathlib import Path

import httpx
import pytest

from wagecuck import ApplicationRunner
from wagecuck.captcha import CapSolver
from wagecuck.models import Code


@pytest.mark.parametrize(
    "route", ["single", "entry", "iframe", "multi", "controls", "radio", "conditional", "shadow"]
)
async def test_confirmed_application(portal, profile, options, route):
    base, server = portal
    result = await ApplicationRunner().run(f"{base}/{route}", profile, options)
    assert result.success, result.model_dump()
    assert result.code == Code.OK
    assert result.submission_attempted and result.submitted
    assert len(server.submissions) == 1
    if route == "single":
        body = server.submissions[0][1]
        assert b"alex.morgan@example.com" in body
        assert b"%PDF-" in body
    artifacts = Path(result.artifact_dir)
    assert json.loads((artifacts / "result.json").read_text())["success"]
    assert "alex.morgan@example.com" not in (artifacts / "events.jsonl").read_text()


async def test_dry_run_never_submits(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner().run(
        f"{base}/single", profile, options.model_copy(update={"mode": "fill"})
    )
    assert result.code == Code.READY, result.model_dump()
    assert not result.success and not result.submission_attempted
    assert not server.submissions


async def test_inspect_does_not_fill(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner().run(
        f"{base}/single", profile, options.model_copy(update={"mode": "inspect"})
    )
    assert result.code == Code.INSPECTED
    assert result.fields_filled == 0
    assert not server.submissions


@pytest.mark.parametrize(
    "route,code",
    [
        ("closed", Code.JOB_CLOSED),
        ("missing", Code.JOB_CLOSED),
        ("auth", Code.AUTH_REQUIRED),
        ("unknown", Code.REQUIRED_ANSWER_MISSING),
        ("validation", Code.VALIDATION_FAILED),
        ("captcha", Code.CAPTCHA_REQUIRED),
    ],
)
async def test_failures_do_not_submit(portal, profile, options, route, code):
    base, server = portal
    result = await ApplicationRunner().run(
        f"{base}/{route}", profile, options.model_copy(update={"mode": "fill"})
    )
    assert result.code == code, result.model_dump()
    assert not server.submissions


async def test_duplicate_submission_fence(portal, profile, options):
    base, server = portal
    runner = ApplicationRunner()
    first = await runner.run(f"{base}/single", profile, options)
    assert first.success
    second = await runner.run(f"{base}/single?utm_source=another", profile, options)
    assert second.code == Code.ALREADY_SUBMITTED
    assert len(server.submissions) == 1


async def test_uncertain_submission_is_not_retried(portal, profile, options):
    base, server = portal
    runner = ApplicationRunner()
    first = await runner.run(f"{base}/uncertain", profile, options)
    assert first.status == "unknown" and first.code == Code.SUBMISSION_UNCONFIRMED
    assert first.submission_attempted and not first.retryable
    second = await runner.run(f"{base}/uncertain", profile, options)
    assert second.code == Code.PRIOR_SUBMISSION_UNCERTAIN
    assert len(server.submissions) == 1


async def test_synthetic_remote_submission_blocked(profile, options):
    result = await ApplicationRunner().run("https://jobs.example.com/job/1", profile, options)
    assert result.code == Code.SYNTHETIC_PROFILE_BLOCKED


async def test_capsolver_integrated_before_submit(portal, profile, options):
    base, server = portal
    requests = []

    def respond(request):
        data = json.loads(request.content)
        requests.append((request.url.path, data))
        if request.url.path == "/createTask":
            return httpx.Response(200, json={"errorId": 0, "taskId": "test-task"})
        return httpx.Response(
            200,
            json={
                "errorId": 0,
                "status": "ready",
                "solution": {"gRecaptchaResponse": "fixture-token"},
            },
        )

    solver = CapSolver("test-secret", poll_interval=0, transport=httpx.MockTransport(respond))
    result = await ApplicationRunner(captcha=solver).run(f"{base}/captcha-form", profile, options)
    assert result.success, result.model_dump()
    assert requests[0][1]["task"]["type"] == "ReCaptchaV2TaskProxyLess"
    assert b"fixture-token" in server.submissions[0][1]
    for path in Path(result.artifact_dir).glob("*.json*"):
        assert "test-secret" not in path.read_text()
        assert "fixture-token" not in path.read_text()
