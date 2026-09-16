import json

import httpx
import pytest

from wagecuck import ApplicationRunner
from wagecuck.agent import Draft, Mapping, OllamaMappingAgent, RequirementAssessment, WorkflowAgent
from wagecuck.models import ApplicationError, Code, FormField


def field(id, label, kind="text", **kwargs):
    return FormField(id=id, frame=0, label=label, kind=kind, **kwargs)


async def test_parser_then_choice_mapping_then_opt_in_drafting(profile):
    class Agent:
        def __init__(self):
            self.calls = []

        async def map(self, fields, keys):
            self.calls.append(("map", [f.id for f in fields]))
            assert "first_name" in keys and "last_name" in keys
            return [
                Mapping(
                    field_id="0:composite",
                    fact_keys=["first_name", "last_name"],
                    confidence=0.99,
                    evidence="Applicant identity",
                )
            ]

        async def draft(self, fields, facts):
            self.calls.append(("draft", [f.id for f in fields]))
            assert "skills" in facts and "email" not in facts
            return [
                Draft(
                    field_id="0:essay",
                    text="My skills include Python and Playwright.",
                    fact_keys=["skills"],
                    confidence=0.99,
                )
            ]

    fields = [
        field("first", "First name", required=True),
        field("composite", "Applicant identity"),
        field("essay", "Describe your technical strengths", "textarea"),
        field("visa", "Do you require H-1B sponsorship?", required=True),
    ]
    agent = Agent()
    planner = WorkflowAgent(agent)
    actions, unresolved = await planner.plan(fields, profile, agent_fill=True)
    assert agent.calls == [("map", ["composite", "essay"]), ("draft", ["essay"])]
    assert [a.source for a in actions] == [
        "facts:first_name",
        "agent:first_name+last_name",
        "agent_fill:skills",
    ]
    assert actions[1].value == "Alex Morgan"
    assert [f.id for f in unresolved] == ["visa"]
    analysis = planner.describe(fields, actions, unresolved)
    assert [f["route"] for f in analysis] == [
        "deterministic",
        "agent_mapping",
        "agent_fill",
        "unresolved",
    ]
    assert all("value" not in f for f in analysis)


async def test_drafting_is_disabled_by_default(profile):
    class Agent:
        async def map(self, fields, keys):
            return []

        async def draft(self, fields, facts):
            pytest.fail("Drafting is opt-in")

    f = field("essay", "Describe your technical strengths", "textarea")
    actions, unresolved = await WorkflowAgent(Agent()).plan([f], profile)
    assert not actions and unresolved == [f]


async def test_missing_known_address_component_cannot_be_replaced_by_another_fact(profile):
    class Agent:
        async def map(self, fields, keys):
            pytest.fail("A recognized missing address line is not an ambiguous field")

        async def draft(self, fields, facts):
            pytest.fail("A missing address line must not be drafted")

    profile.address.line2 = ""
    profile.facts.pop("address_line2", None)
    f = field("line2", "Address Line 2")
    actions, unresolved = await WorkflowAgent(Agent()).plan([f], profile, agent_fill=True)
    assert not actions
    assert unresolved == [f]


@pytest.mark.parametrize(
    "keys,text",
    [(["invented_degree"], "I have a degree."), (["skills"], "I have 99 years of experience.")],
)
async def test_drafting_rejects_unsupported_sources_and_numbers(profile, keys, text):
    class Agent:
        async def map(self, fields, keys):
            return []

        async def draft(self, fields, facts):
            return [Draft(field_id="0:essay", text=text, fact_keys=keys, confidence=1)]

    with pytest.raises(ApplicationError) as exc:
        await WorkflowAgent(Agent()).plan(
            [field("essay", "Describe your experience", "textarea")], profile, agent_fill=True
        )
    assert exc.value.code == Code.AGENT_FAILED


async def test_invalid_document_mapping_isolated_from_valid_field_mapping(profile):
    class Agent:
        async def map(self, fields, keys):
            return [
                Mapping(field_id="0:good", fact_key="website", confidence=1),
                Mapping(
                    field_id="0:x",
                    fact_keys=["first_name", "documents.resume"],
                    confidence=1,
                ),
            ]

    planner = WorkflowAgent(Agent())
    fields = [field("good", "Public work sample"), field("x", "Some unknown question")]
    actions, unresolved = await planner.plan(fields, profile)
    assert [action.source for action in actions] == ["agent:website"]
    assert unresolved == [fields[1]]
    assert planner.warnings == [
        {
            "field_id": "0:x",
            "stage": "mapping",
            "message": "Document mappings must target upload fields only.",
            "code": Code.AGENT_FAILED,
        }
    ]


async def test_optional_and_unknown_requirements_are_distinguished(profile):
    class Agent:
        async def assess(self, fields):
            return [
                RequirementAssessment(
                    field_id="0:o",
                    required=False,
                    evidence="This field is optional",
                    confidence=0.99,
                )
            ]

        async def map(self, fields, keys):
            return []

    fields = [
        field("o", "Portfolio commentary", context="This field is optional"),
        field("u", "Other comments"),
    ]
    await WorkflowAgent(Agent()).plan(fields, profile)
    assert fields[0].requirement_status == "optional"
    assert fields[1].requirement_status == "unknown"


async def test_mapping_schema_constrains_choices_and_sends_adjacent_metadata():
    def respond(request):
        body = json.loads(request.content)
        props = body["format"]["$defs"]["Mapping"]["properties"]
        assert props["fact_key"]["enum"] == ["", "first_name", "last_name"]
        assert props["fact_keys"]["items"]["enum"] == ["first_name", "last_name"]
        payload = json.loads(body["messages"][1]["content"])
        assert payload["fields"][0]["context"] == "Enter your complete name"
        return httpx.Response(200, json={"message": {"content": json.dumps({"mappings": []})}})

    agent = OllamaMappingAgent("test", transport=httpx.MockTransport(respond))
    assert (
        await agent.map(
            [field("x", "Applicant", context="Enter your complete name")],
            ["first_name", "last_name"],
        )
        == []
    )


async def test_malformed_mapping_item_does_not_discard_valid_sibling(profile):
    def respond(request):
        body = json.loads(request.content)
        if body["format"]["title"] == "Requirements":
            content = {"assessments": []}
        else:
            content = {
                "mappings": [
                    {
                        "field_id": "0:good",
                        "fact_key": "website",
                        "fact_keys": [],
                        "separator": " ",
                        "evidence": "Public work sample",
                        "confidence": 1,
                    },
                    {
                        "field_id": "0:bad",
                        "fact_key": "first_name",
                        "fact_keys": [],
                        "separator": " ",
                        "evidence": "Other value",
                    },
                ]
            }
        return httpx.Response(200, json={"message": {"content": json.dumps(content)}})

    planner = WorkflowAgent(OllamaMappingAgent("test", transport=httpx.MockTransport(respond)))
    fields = [field("good", "Public work sample"), field("bad", "Other value")]
    actions, unresolved = await planner.plan(fields, profile)
    assert [action.source for action in actions] == ["agent:website"]
    assert unresolved == [fields[1]]
    assert planner.warnings[0]["field_id"] == "0:bad"
    assert "malformed mapping metadata" in planner.warnings[0]["message"]


async def test_ollama_draft_transport():
    def respond(request):
        body = json.loads(request.content)
        assert body["format"]["$defs"]["Draft"]["properties"]["fact_keys"]["items"]["enum"] == [
            "skills"
        ]
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "drafts": [
                                {
                                    "field_id": "0:essay",
                                    "text": "I use Python.",
                                    "fact_keys": ["skills"],
                                    "confidence": 0.99,
                                }
                            ]
                        }
                    )
                }
            },
        )

    agent = OllamaMappingAgent("test", transport=httpx.MockTransport(respond))
    result = await agent.draft([field("essay", "Describe your strengths")], {"skills": "Python"})
    assert result[0].text == "I use Python."


@pytest.mark.parametrize("mode", ["inspect", "submit"])
async def test_pipeline_through_browser_and_field_report(portal, profile, options, mode):
    class Agent:
        async def map(self, fields, keys):
            return [
                Mapping(
                    field_id=f"{f.frame}:{f.id}",
                    fact_keys=["first_name", "last_name"] if f.name == "identity" else ["email"],
                    confidence=1,
                    evidence=f.label,
                )
                for f in fields
                if f.name in ("identity", "email")
            ]

        async def draft(self, fields, facts):
            assert mode == "submit"
            return [
                Draft(
                    field_id=f"{f.frame}:{f.id}",
                    text="My skills include Python and Playwright.",
                    fact_keys=["skills"],
                    confidence=1,
                )
                for f in fields
                if f.name == "essay"
            ]

    options.mode = mode
    options.agent_fill = True
    base, server = portal
    result = await ApplicationRunner(agent=Agent()).run(f"{base}/agent-pipeline", profile, options)
    assert result.code == (Code.OK if mode == "submit" else Code.INSPECTED), result
    rows = json.loads((options.artifacts_dir / result.run_id / "analysis-01.json").read_text())
    assert len(rows) == 7
    identity = next(r for r in rows if r["label"] == "Applicant identity")
    assert identity["route"] == "agent_mapping"
    email = next(r for r in rows if "Reply destination" in r["label"])
    assert email["route"] == "agent_mapping" and email["source"] == "agent:email"
    essay = next(r for r in rows if r["kind"] == "textarea" and r["required"])
    assert essay["route"] == ("agent_fill" if mode == "submit" else "unresolved")
    optional = next(r for r in rows if "optional" in r["label"])
    assert not optional["required"] and optional["requirement_status"] == "optional"
    if mode == "submit":
        assert len(server.submissions) == 1
        assert b"Alex Morgan" in server.submissions[0][1]
        assert b"My skills include Python and Playwright." in server.submissions[0][1]
    else:
        assert not server.submissions and result.fields_filled == 0


async def test_parser_optional_wording_and_adjacent_context():
    from playwright.async_api import async_playwright

    from wagecuck.agent import fact_key
    from wagecuck.browser import snapshot

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content("""<form>
          <label>Email (not required)<input name="email"></label>
          <p>License is mandatory</p><input name="other">
          <div class="form-field"><label for="city">City (optional)</label>
            <p>Use your current city</p><input id="city" aria-describedby="help"></div>
          <p id="help">Please enter a city name</p>
        </form>""")
        fields = (await snapshot(page)).fields
        assert not fields[0].required and fields[0].requirement_status == "optional"
        assert fact_key(fields[0]) == "email"
        assert "License is mandatory" not in fields[1].context
        assert "Use your current city" in fields[2].context
        assert "Please enter a city name" in fields[2].context
        assert fact_key(fields[2]) == "city"
        await browser.close()
