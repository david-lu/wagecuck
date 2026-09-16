import json

import httpx
import pytest

from wagecuck.agent import Mapping, WorkflowAgent
from wagecuck.ats import detect
from wagecuck.captcha import CapSolver, Challenge
from wagecuck.models import ApplicationError, Code, FormField
from wagecuck.store import Store, application_key


def test_canonical_key_preserves_job_parameters():
    assert application_key("https://jobs.lever.co/company/id/apply", "a") == application_key(
        "https://jobs.lever.co/company/id", "a"
    )
    assert application_key(
        "https://jobs.ashbyhq.com/company/id/application", "a"
    ) == application_key("https://jobs.ashbyhq.com/company/id", "a")
    assert application_key("https://x.test/?job=1&utm_campaign=a", "a") == application_key(
        "https://x.test/?job=1", "a"
    )
    assert application_key("https://x.test/?job=1", "a") != application_key(
        "https://x.test/?job=2", "a"
    )
    assert application_key("https://x.test/?job=1", "a") != application_key(
        "https://x.test/?job=1", "b"
    )


def test_claim_is_atomic_and_uncertain_is_durable(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.claim("key", "run1")
    with pytest.raises(ApplicationError) as error:
        Store(tmp_path / "db.sqlite").claim("key", "run2")
    assert error.value.code == Code.RUN_IN_PROGRESS
    store.transition("key", "run1", "submitting")
    with pytest.raises(ApplicationError) as error:
        store.claim("key", "run2")
    assert error.value.code == Code.PRIOR_SUBMISSION_UNCERTAIN


def test_ats_detection_checks_actual_hostname():
    assert detect("https://job-boards.greenhouse.io/company/jobs/123") == "greenhouse"
    assert detect("https://evil.test/greenhouse.io") == "generic"
    assert detect("https://greenhouse.io.evil.test/") == "generic"


async def test_never_infers_legal_answers(profile):
    profile.screening.work_authorization.clear()
    field = FormField(
        id="1",
        frame=0,
        label="Are you authorized to work in the United States?",
        kind="text",
        required=True,
        autocomplete="name",
    )
    actions, unresolved = await WorkflowAgent().plan([field], profile)
    assert not actions and unresolved == [field]


async def test_agent_cannot_invent_values(profile):
    class BadAgent:
        async def map(self, *_):
            return [Mapping(field_id="0:1", fact_key="invented_salary", confidence=1)]

    field = FormField(id="1", frame=0, label="Where can we view your work?", kind="text")
    with pytest.raises(ApplicationError) as error:
        await WorkflowAgent(BadAgent()).plan([field], profile)
    assert error.value.code == Code.AGENT_FAILED


async def test_agent_maps_known_fact_only(profile):
    class Agent:
        async def map(self, *_):
            return [Mapping(field_id="0:1", fact_key="website", confidence=0.99)]

    actions, unresolved = await WorkflowAgent(Agent()).plan(
        [FormField(id="1", frame=0, label="Where can we view your work?", kind="text")], profile
    )
    assert actions[0].value == profile.facts["website"] and not unresolved


@pytest.mark.parametrize(
    "response,expected",
    [
        ({"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE"}, Code.CAPTCHA_SOLVE_FAILED),
        ({"errorId": 0, "status": "ready", "solution": {}}, Code.CAPTCHA_SOLVE_FAILED),
        ({"errorId": 0}, Code.CAPTCHA_SOLVE_FAILED),
        ([], Code.CAPTCHA_SOLVE_FAILED),
        ({"status": "ready", "solution": None}, Code.CAPTCHA_SOLVE_FAILED),
    ],
)
async def test_capsolver_errors(response, expected):
    solver = CapSolver(
        "secret", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response))
    )
    with pytest.raises(ApplicationError) as error:
        await solver.solve(Challenge("recaptcha_v2", "key", 0, "https://example.com"))
    assert error.value.code == expected


async def test_capsolver_timeout():
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"errorId": 0, "taskId": "task", "status": "processing"})
    )
    with pytest.raises(ApplicationError) as error:
        await CapSolver("secret", timeout=0.03, poll_interval=0.01, transport=transport).solve(
            Challenge("turnstile", "key", 0, "https://example.com")
        )
    assert error.value.code == Code.CAPTCHA_TIMEOUT


async def test_missing_capsolver_key_makes_no_provider_request(monkeypatch):
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(500)

    with pytest.raises(ApplicationError) as error:
        await CapSolver(transport=httpx.MockTransport(respond)).solve(
            Challenge("recaptcha_v2", "public-site-key", 0, "https://example.com")
        )
    assert error.value.code == Code.CAPTCHA_KEY_MISSING
    assert calls == []


def test_capsolver_missing_key_and_unsupported(monkeypatch):
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    with pytest.raises(ApplicationError) as error:
        CapSolver().validate(Challenge("turnstile", "key", 0, "https://example.com"))
    assert error.value.code == Code.CAPTCHA_KEY_MISSING
    with pytest.raises(ApplicationError) as error:
        Challenge("hcaptcha", "key", 0, "https://example.com").task()
    assert error.value.code == Code.CAPTCHA_UNSUPPORTED


async def test_capsolver_turnstile_contract():
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200, json={"errorId": 0, "status": "ready", "solution": {"token": "ok"}}
        )

    solver = CapSolver("secret", transport=httpx.MockTransport(respond))
    assert (
        await solver.solve(
            Challenge("turnstile", "site", 0, "https://example.com", action="apply", cdata="data")
        )
        == "ok"
    )
    assert calls[0]["task"]["metadata"] == {"action": "apply", "cdata": "data"}
