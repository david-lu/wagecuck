import pytest
from playwright.async_api import async_playwright

from wagecuck.agent import Mapping, WorkflowAgent
from wagecuck.browser import prepared_snapshot, snapshot
from wagecuck.execution import execute_actions
from wagecuck.inference import FieldAnswer
from wagecuck.models import ApplicationError, Code, FormField, Option


def field(id, label, kind="select", **kwargs):
    return FormField(id=id, frame=0, label=label, kind=kind, **kwargs)


def answer(field, value, basis="inferred", keys=None):
    return FieldAnswer(
        field_id=f"{field.frame}:{field.id}",
        value=value,
        basis=basis,
        fact_keys=keys or ["state"],
        reason="The profile location is outside the listed regions.",
    )


class Agent:
    def __init__(self, respond=lambda fields, facts: []):
        self.respond = respond
        self.calls = []

    async def map(self, fields, keys):
        self.calls.append(("map", [f.id for f in fields]))
        return []

    async def infer(self, fields, facts):
        self.calls.append(("infer", [f.id for f in fields]))
        return self.respond(fields, facts)


@pytest.mark.parametrize("kind", ["select", "combobox"])
@pytest.mark.parametrize("filled", [False, True])
async def test_unavailable_profile_choice_reaches_agent_even_with_prefilled_default(
    profile, kind, filled
):
    state = field(
        "state",
        "State",
        kind,
        filled=filled,
        required=True,
        options=[
            Option(label="Not Applicable", value="na"),
            Option(label="Alaska", value="AK"),
        ],
    )

    def infer(fields, facts):
        assert fields == [state]
        assert facts["state"] == "Ontario"
        assert [o.value for o in fields[0].options] == ["na", "AK"]
        return [answer(state, "Not Applicable")]

    agent = Agent(infer)
    planner = WorkflowAgent(agent)
    actions, unresolved = await planner.plan([state], profile, agent_fill=True)
    assert not unresolved and len(actions) == 1
    assert agent.calls == [("infer", ["state"])]
    assert actions[0].value == ("na" if kind == "select" else "Not Applicable")
    assert actions[0].source == "agent_fill:state"
    assert actions[0].answer_basis == "inferred"
    assert not actions[0].made_up
    assert planner.warnings[0]["stage"] == "value_contract"


async def test_model_mapping_with_unavailable_option_also_reaches_inference(profile):
    class MappedAgent(Agent):
        async def map(self, fields, keys):
            return [
                Mapping(field_id="0:region", fact_key="state", confidence=1, evidence="Home region")
            ]

    region = field("region", "Home region", options=[Option(label="Other", value="other")])
    agent = MappedAgent(lambda fields, facts: [answer(region, "Other")])
    actions, unresolved = await WorkflowAgent(agent).plan([region], profile, agent_fill=True)
    assert not unresolved and actions[0].value == "other"
    assert agent.calls == [("infer", ["region"])]


@pytest.mark.parametrize("kind", ["select", "combobox"])
@pytest.mark.parametrize("invalid_answer", [False, True])
async def test_agent_attempt_precedes_random_fallback_and_excludes_placeholders(
    profile, monkeypatch, kind, invalid_answer
):
    state = field(
        "state",
        "State",
        kind,
        options=[
            Option(label="Please select a state", value="prompt"),
            Option(label="Choose an option", value="choose"),
            Option(label="Alaska", value="AK"),
            Option(label="Texas", value="TX"),
            Option(label="", value="blank"),
        ],
    )
    agent = Agent(lambda fields, facts: [answer(state, "Ontario")] if invalid_answer else [])

    def choose(options):
        assert agent.calls[-1] == ("infer", ["state"])
        assert [o.label for o in options] == ["Alaska", "Texas"]
        return options[-1]

    monkeypatch.setattr("random.choice", choose)
    planner = WorkflowAgent(agent)
    actions, unresolved = await planner.plan([state], profile, agent_fill=True)
    assert not unresolved
    assert actions[0].value == ("TX" if kind == "select" else "Texas")
    assert actions[0].source == "random:unmatched_choice"
    assert actions[0].made_up and actions[0].answer_basis == "made_up"
    assert "Agent inference" in actions[0].inference_reason
    assert planner.describe([state], actions, [])[0]["made_up"]


@pytest.mark.parametrize("kind", ["checkbox", "radio"])
async def test_unmatched_group_is_sent_together_then_randomly_selects_one(
    profile, monkeypatch, kind
):
    profile.screening.demographics.race_ethnicity = ["white"]
    fields = [
        field("asian", "Asian", kind, group="Race or ethnicity", filled=True),
        field("black", "Black", kind, group="Race or ethnicity"),
    ]
    agent = Agent()
    monkeypatch.setattr("random.choice", lambda peers: peers[-1])
    actions, unresolved = await WorkflowAgent(agent).plan(fields, profile, agent_fill=True)
    assert not unresolved
    assert set(agent.calls[-1][1]) == {"black", "asian"}
    assert agent.calls[-1][0] == "infer"
    assert sum(a.value is True for a in actions) == 1
    assert next(a for a in actions if a.value is True).field.id == agent.calls[-1][1][-1]
    assert len(actions) == (2 if kind == "checkbox" else 1)
    assert all(a.made_up for a in actions)


async def test_matching_profile_choice_does_not_call_inference(profile):
    def unexpected(*args):
        pytest.fail("A matching profile choice needs no invented fallback")

    state = field("s", "State", options=[Option(label="Ontario", value="ON")])
    actions, unresolved = await WorkflowAgent(Agent(unexpected)).plan(
        [state], profile, agent_fill=True
    )
    assert not unresolved and actions[0].source == "facts:state"


async def test_country_combobox_dial_label_remains_deterministic(profile):
    country = field(
        "country", "Country", "combobox", options=[Option(label="Canada +1", value="CA")]
    )
    actions, unresolved = await WorkflowAgent().plan([country], profile)
    assert not unresolved and actions[0].source == "facts:country"
    assert actions[0].choice_labels == ["Canada +1"]


async def test_invention_stays_opt_in_and_model_errors_do_not_silently_randomize(profile):
    state = field("state", "State", options=[Option(label="Alaska", value="AK")])
    agent = Agent()
    actions, unresolved = await WorkflowAgent(agent).plan([state], profile)
    assert not actions and unresolved == [state] and not agent.calls

    class FailedAgent(Agent):
        async def infer(self, fields, facts):
            raise ApplicationError(Code.AGENT_FAILED, "Model unavailable")

    with pytest.raises(ApplicationError, match="Model unavailable"):
        await WorkflowAgent(FailedAgent()).plan([state], profile, agent_fill=True)


@pytest.mark.parametrize("kind", ["select", "combobox"])
async def test_empty_selector_does_not_fabricate_an_option(profile, kind):
    f = field("unknown", "Unknown choice", kind)
    agent = Agent()
    actions, unresolved = await WorkflowAgent(agent).plan([f], profile, agent_fill=True)
    assert not actions and unresolved == [f]
    assert agent.calls[-1][0] == "infer"


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        page.set_default_timeout(1000)
        yield page
        await browser.close()


async def test_browser_agent_fallback_fills_native_dynamic_and_group_choices(
    page, profile, monkeypatch
):
    await page.set_content("""<form>
    <label>State<select name="state" required>
      <option value="prompt">Please select</option>
      <option value="AK">Alaska</option><option value="TX">Texas</option>
      <optgroup disabled><option value="disabled">Disabled group</option></optgroup>
      <option value="hidden" hidden>Hidden</option>
    </select></label>
    <fieldset disabled><legend>Disabled choices</legend>
      <label>Bad<input type="checkbox"></label>
    </fieldset>
    <label>Office<input name="office" role="combobox" readonly aria-controls="offices"
      onclick="document.querySelector('#offices').hidden=false"
      onkeydown="if(event.key==='Escape')document.querySelector('#offices').hidden=true"></label>
    <div id="offices" role="listbox" hidden>
      <div role="option">Please select</div>
      <div role="option" onclick="document.querySelector('[name=office]').value='Remote';this.parentElement.hidden=true">Remote</div>
      <div role="option" aria-disabled="true">Disabled</div>
    </div>
    <fieldset><legend>Hobbies *</legend>
      <label>Chess<input name="chess" type="checkbox" checked></label>
      <label>Hiking<input name="hiking" type="checkbox"></label>
    </fieldset>
    <button>Submit</button></form>""")
    snap = await prepared_snapshot(page)
    assert len(snap.fields) == 4
    assert [o.value for o in snap.fields[0].options] == ["prompt", "AK", "TX"]
    agent = Agent()
    monkeypatch.setattr("random.choice", lambda choices: choices[-1])
    planner = WorkflowAgent(agent)
    actions, unresolved = await planner.plan(snap.fields, profile, agent_fill=True)
    assert not unresolved and agent.calls[-1][0] == "infer"
    result = await execute_actions(page, actions, assessed_fields=snap.fields, settle_polls=1)
    assert result.retained, result.fields
    assert await page.locator("[name=state]").input_value() == "TX"
    assert await page.locator("[name=office]").input_value() == "Remote"
    assert await page.locator("input[type=checkbox]:checked").count() == 1
    assert all(row.report()["made_up"] for row in result.fields)


async def test_random_checkbox_replanning_preserves_set_until_chosen_option_disappears(
    page, profile, monkeypatch
):
    await page.set_content("""<fieldset><legend>Hobbies *</legend>
      <label>Chess<input name="chess" type="checkbox"></label>
      <label>Hiking<input name="hiking" type="checkbox"></label>
    </fieldset>""")
    monkeypatch.setattr("random.choice", lambda choices: choices[0])
    planner = WorkflowAgent(Agent())
    snap = await snapshot(page)
    first, _ = await planner.plan(snap.fields, profile, agent_fill=True)
    result = await execute_actions(page, first, assessed_fields=snap.fields, settle_polls=1)
    assert result.retained
    assert await page.locator("[name=chess]").is_checked()

    await page.locator("fieldset").evaluate(
        "e => e.insertAdjacentHTML('beforeend', '<label>Reading<input name=reading type=checkbox></label>')"
    )
    monkeypatch.setattr("random.choice", lambda choices: choices[-1])
    snap = await snapshot(page)
    second, _ = await planner.plan(snap.fields, profile, agent_fill=True)
    result = await execute_actions(
        page, second, previous_actions=first, assessed_fields=snap.fields, settle_polls=1
    )
    assert result.retained
    assert await page.locator("input:checked").evaluate_all("els => els.map(e => e.name)") == [
        "chess"
    ]

    await page.locator("[name=chess]").evaluate("e => e.parentElement.remove()")
    snap = await snapshot(page)
    third, _ = await planner.plan(snap.fields, profile, agent_fill=True)
    result = await execute_actions(
        page, third, previous_actions=second, assessed_fields=snap.fields, settle_polls=1
    )
    assert result.retained
    assert await page.locator("input:checked").evaluate_all("els => els.map(e => e.name)") == [
        "reading"
    ]


async def test_fully_prefilled_unmatched_checkbox_group_still_reaches_model(profile):
    profile.screening.demographics.race_ethnicity = ["white"]
    fields = [
        field("asian", "Asian", "checkbox", group="Race or ethnicity", filled=True),
        field("black", "Black", "checkbox", group="Race or ethnicity", filled=True),
    ]
    agent = Agent()
    actions, unresolved = await WorkflowAgent(agent).plan(fields, profile, agent_fill=True)
    assert not unresolved and len(actions) == 2
    assert agent.calls[-1] == ("infer", ["asian", "black"])
    assert sum(action.value is True for action in actions) == 1


async def test_empty_known_native_selector_reaches_model_but_remains_unresolved(profile):
    state = field("state", "State")
    agent = Agent()
    actions, unresolved = await WorkflowAgent(agent).plan([state], profile, agent_fill=True)
    assert not actions and unresolved == [state]
    assert agent.calls[-1] == ("infer", ["state"])


@pytest.mark.parametrize("model_finds_equivalent", [True, False])
async def test_real_openai_client_receives_unavailable_choices_and_profile(
    profile, monkeypatch, model_finds_equivalent
):
    import json

    import httpx

    from wagecuck.agent_config import OpenAIMappingAgent

    seen = []
    state = field(
        "state",
        "State",
        options=[Option(label="Not Applicable", value="na"), Option(label="Alaska", value="AK")],
    )

    def respond(request):
        assert str(request.url).endswith("/responses")
        body = json.loads(request.content)
        schema = body["text"]["format"]["schema"]
        if schema["title"] == "Requirements":
            payload = {"assessments": []}
        else:
            assert schema["title"] == "FieldAnswers"
            metadata = json.loads(body["input"][1]["content"])
            assert metadata["facts"]["state"] == "Ontario"
            assert metadata["fields"][0]["options"] == [
                {"label": "Not Applicable", "value": "na"},
                {"label": "Alaska", "value": "AK"},
            ]
            seen.append("inference")
            payload = {
                "answers": (
                    [answer(state, "Not Applicable").model_dump()] if model_finds_equivalent else []
                )
            }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(payload)}],
                    }
                ],
            },
        )

    monkeypatch.setattr("random.choice", lambda options: options[-1])
    agent = OpenAIMappingAgent(api_key="test-only", transport=httpx.MockTransport(respond))
    actions, unresolved = await WorkflowAgent(agent).plan([state], profile, agent_fill=True)
    assert not unresolved and seen == ["inference"]
    assert actions[0].value == ("na" if model_finds_equivalent else "AK")
    assert actions[0].made_up is not model_finds_equivalent
