import json

import httpx
import pytest

from wagecuck import ApplicationRunner
from wagecuck.agent import OllamaMappingAgent, RequirementAssessment, WorkflowAgent
from wagecuck.models import ApplicationError, Code, FormField


def field(**changes):
    return FormField(
        id="license",
        frame=0,
        label="Professional license identifier",
        kind="text",
        context="You must provide this identifier to process your application.",
        **changes,
    )


class Assessor:
    async def assess(self, fields):
        return [
            RequirementAssessment(
                field_id=f"{f.frame}:{f.id}",
                required=True,
                confidence=0.99,
                evidence="You must provide this identifier to process your application.",
            )
            for f in fields
            if f.name == "license" or f.id == "license"
        ]

    async def map(self, fields, keys):
        return []


async def test_agent_detected_requirement_blocks_submission(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner(agent=Assessor()).run(
        f"{base}/agent-required", profile, options
    )
    assert result.code == Code.REQUIRED_ANSWER_MISSING
    assert result.unresolved == ["Professional license identifier"]
    assert not server.submissions
    snap = json.loads((options.artifacts_dir / result.run_id / "step-01.json").read_text())
    license = next(f for f in snap["fields"] if f["name"] == "license")
    assert license["required"] and license["required_evidence"].startswith("agent:")
    assert "context" not in license


@pytest.mark.parametrize(
    "field_id,evidence",
    [("0:invented", "You must provide"), ("0:license", "This is a mandatory identifier")],
)
async def test_agent_cannot_invent_fields_or_evidence(field_id, evidence, profile):
    class Agent(Assessor):
        async def assess(self, fields):
            return [
                RequirementAssessment(
                    field_id=field_id, required=True, confidence=1, evidence=evidence
                )
            ]

    f = field()
    with pytest.raises(ApplicationError) as exc:
        await WorkflowAgent(Agent()).plan([f], profile)
    assert exc.value.code == Code.AGENT_FAILED and not f.required


@pytest.mark.parametrize(
    "required,confidence,evidence",
    [
        (False, 1, "You must provide"),
        (True, 0.5, "You must provide"),
        (True, 1, "Professional license identifier"),
    ],
)
async def test_agent_does_not_promote_weak_claims(required, confidence, evidence, profile):
    class Agent(Assessor):
        async def assess(self, fields):
            return [
                RequirementAssessment(
                    field_id="0:license",
                    required=required,
                    confidence=confidence,
                    evidence=evidence,
                )
            ]

    f = field()
    await WorkflowAgent(Agent()).plan([f], profile)
    assert not f.required
    f.required = True
    await WorkflowAgent(Agent()).plan([f], profile)
    assert f.required


async def test_ollama_requirement_transport_and_no_profile_values(profile):
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        content = (
            {
                "assessments": [
                    {
                        "field_id": "0:license",
                        "required": True,
                        "confidence": 0.99,
                        "evidence": "You must provide this identifier",
                    }
                ]
            }
            if "assessments" in body["format"]["properties"]
            else {"mappings": []}
        )
        return httpx.Response(200, json={"message": {"content": json.dumps(content)}})

    agent = WorkflowAgent(OllamaMappingAgent("test-model", transport=httpx.MockTransport(respond)))
    f = field()
    actions, unresolved = await agent.plan([f], profile)
    assert f.required and not actions and unresolved == [f]
    assert len(calls) == 2
    assert profile.facts["email"] not in json.dumps(calls)
    assert "screening" not in calls[0]["messages"][1]["content"]
