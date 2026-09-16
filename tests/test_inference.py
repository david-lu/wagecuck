import json

import pytest

from wagecuck.agent import Mapping, WorkflowAgent
from wagecuck.inference import FieldAnswer, apply_inferred_answers
from wagecuck.models import ApplicationError, Code, FormField, Option


def field(id, label, kind="text", **kwargs):
    return FormField(id=id, frame=0, label=label, kind=kind, **kwargs)


def answer(id, value, basis="made_up", keys=()):
    return FieldAnswer(
        field_id=f"0:{id}",
        value=value,
        basis=basis,
        fact_keys=list(keys),
        reason="Test answer provenance.",
    )


async def test_inference_sees_all_unmapped_controls_and_full_profile(profile):
    class Agent:
        async def map(self, fields, keys):
            return []

        async def infer(self, fields, facts):
            assert {f.id for f in fields} == {"count", "language", "consent", "date", "line2"}
            assert facts["email"] == profile.facts["email"]
            assert facts["consents.application_processing"] is True
            assert "education.0.end_date" in facts and "employment.0.company" in facts
            return [
                answer("count", "1", "inferred", ["employment.0.company"]),
                answer("language", "English"),
                answer("consent", True, "profile", ["consents.application_processing"]),
                answer("date", "2026-10-01"),
                answer("line2", "Unit 3"),
            ]

    fields = [
        field("first", "First name"),
        field("count", "Number of employers since graduation", "number"),
        field(
            "language",
            "Communication language",
            "select",
            options=[Option(label="English", value="en"), Option(label="French", value="fr")],
        ),
        field(
            "consent",
            "(Required) Allow us to process your personal information.",
            "checkbox",
            required=True,
        ),
        field("date", "Interview day", "date"),
        field("line2", "Address Line 2"),
    ]
    planner = WorkflowAgent(Agent())
    actions, unresolved = await planner.plan(fields, profile, agent_fill=True)
    assert not unresolved
    by_id = {a.field.id: a for a in actions}
    assert by_id["language"].value == "en"
    assert by_id["consent"].made_up is False
    assert by_id["count"].answer_basis == "inferred"
    assert by_id["line2"].made_up is True
    rows = planner.describe(fields, actions, unresolved)
    assert {r["field_id"] for r in rows if r["made_up"]} == {"0:language", "0:date", "0:line2"}


async def test_invalid_mapping_falls_through_to_inference_without_losing_parser_actions(profile):
    class Agent:
        async def map(self, fields, keys):
            return [Mapping(field_id="0:x", fact_key="not_a_profile_field", confidence=1)]

        async def infer(self, fields, facts):
            assert [f.id for f in fields] == ["x"]
            return [answer("x", "Board games")]

    planner = WorkflowAgent(Agent())
    actions, unresolved = await planner.plan(
        [field("first", "First name"), field("x", "Favourite pastime")], profile, agent_fill=True
    )
    assert not unresolved and len(actions) == 2
    assert actions[0].source == "facts:first_name"
    assert actions[1].made_up
    assert planner.warnings[0]["stage"] == "mapping"


async def test_requirement_failure_still_allows_answer_inference(profile):
    class Agent:
        async def assess(self, fields):
            raise ApplicationError(Code.AGENT_FAILED, "Assessment unavailable")

        async def map(self, fields, keys):
            return []

        async def infer(self, fields, facts):
            return [answer("x", "2026-10-01")]

    planner = WorkflowAgent(Agent())
    actions, unresolved = await planner.plan(
        [field("x", "Preferred interview day", "date", required=True)], profile, agent_fill=True
    )
    assert not unresolved and actions[0].field.required
    assert planner.warnings[0]["stage"] == "requirements"


def test_radio_selection_resolves_group_and_preserves_made_up_marker():
    fields = [
        field("yes", "Yes", "radio", name="membership", group="Membership"),
        field("no", "No", "radio", name="membership", group="Membership"),
    ]
    actions, resolved, warnings = apply_inferred_answers(
        fields, [answer("yes", True), answer("no", False)], {}
    )
    assert not warnings and resolved == {"0:yes", "0:no"}
    assert len(actions) == 1 and actions[0].made_up
    actions, resolved, warnings = apply_inferred_answers(
        fields, [answer("yes", True), answer("no", True)], {}
    )
    assert not actions and not resolved and len(warnings) == 2


@pytest.mark.parametrize(
    "kind,value",
    [
        ("number", "several"),
        ("date", "2026-99-99"),
        ("checkbox", "true"),
        ("select", "not an option"),
    ],
)
def test_invalid_control_answer_stays_unresolved(kind, value):
    f = field("x", "Something", kind, options=[Option(label="English", value="en")])
    actions, resolved, warnings = apply_inferred_answers([f], [answer("x", value)], {})
    assert not actions and not resolved and warnings


def test_uncited_answer_is_always_marked_made_up():
    actions, _, _ = apply_inferred_answers(
        [field("x", "Membership")], [answer("x", "Yes", "inferred")], {}
    )
    assert actions[0].made_up and actions[0].answer_basis == "made_up"


def test_notice_period_does_not_establish_an_interview_preference():
    actions, _, _ = apply_inferred_answers(
        [field("x", "Preferred interview date", "date")],
        [answer("x", "2026-10-01", "inferred", ["application.notice_period_days"])],
        {"application.notice_period_days": "14"},
    )
    assert actions[0].made_up


def test_partially_supported_answer_with_admitted_assumption_is_made_up():
    proposed = answer("x", "Canada - English", "inferred", ["country"])
    proposed.reason = "Canada is supplied, though language fluency is not explicitly stated."
    actions, _, _ = apply_inferred_answers(
        [
            field(
                "x",
                "Location and language",
                "select",
                options=[Option(label="Canada - English", value="ca-en")],
            )
        ],
        [proposed],
        {"country": "Canada"},
    )
    assert actions[0].value == "ca-en" and actions[0].made_up


async def test_runner_logs_made_up_answers_after_browser_failure(
    portal, profile, options, monkeypatch
):
    from wagecuck import ApplicationRunner, execution

    original_fill = execution.fill

    async def fail_generated_control(page, action):
        if action.made_up:
            raise ApplicationError(Code.FIELD_FILL_FAILED, "Simulated control failure")
        await original_fill(page, action)

    monkeypatch.setattr(execution, "fill", fail_generated_control)

    class Agent:
        async def map(self, fields, keys):
            return []

        async def infer(self, fields, facts):
            return [answer(f.id, "Invented test answer") for f in fields]

    base, server = portal
    # Existing fixture has required unknown prose; it exercises the real executor and log writer.
    result = await ApplicationRunner(Agent()).run(
        f"{base}/agent-pipeline",
        profile,
        options.model_copy(update={"mode": "fill", "agent_fill": True}),
    )
    log = json.loads((options.artifacts_dir / result.run_id / "result.json").read_text())
    generated = [r for r in log["answer_log"] if r["source"].startswith("agent_fill:")]
    assert result.code == Code.FIELD_FILL_FAILED
    assert generated and all(r["made_up"] for r in generated)
    assert generated[-1]["status"] == "failed"
    assert all(r["inference_reason"] for r in generated)
    assert not server.submissions
