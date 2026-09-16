import json

import pytest

from wagecuck import ApplicationRunner
from wagecuck.agent import Mapping, WorkflowAgent
from wagecuck.demo import generate_profile
from wagecuck.models import ApplicationError, Code, FormField, Option


@pytest.mark.parametrize(
    "label,kind,key",
    [
        ("When would you be available to start? ✱", "text", "availability"),
        ("When are you looking to start? ✱", "textarea", "availability"),
        ("Date Available *", "text", "availability"),
        (
            "What is your availability to start this role, and do you have a notice period?*Required",
            "text",
            "availability",
        ),
        ("What is your current notice period?*", "text", "notice_period"),
        (
            "What is your desired annual salary (CAD)?",
            "number",
            "application.compensation.annual_target",
        ),
        ("What salary range are you targeting? ✱", "textarea", "compensation_expectations"),
        ("What are your salary expectations?*Required", "text", "compensation_expectations"),
        ("Desired Pay *", "text", "compensation_expectations"),
        ("Work History*", "textarea", "work_history"),
        (
            "Personal Summary This section is optional. Use it to tell us a little more about yourself.",
            "textarea",
            "application.personal_summary",
        ),
        (
            "What is your preferred name (if different from your full name)?",
            "textarea",
            "application.preferred_name",
        ),
        (
            "How do you use AI in your professional or personal life?",
            "textarea",
            "application.ai_usage",
        ),
        (
            "How many years of experience do you have working with Golang? *",
            "text",
            "experience.golang.years",
        ),
        (
            "How many years of professional Python development experience do you have?*Required",
            "number",
            "experience.python.years",
        ),
        ("Linkedin link profile *", "text", "linkedin"),
        ("GitHub URL (if any)", "textarea", "github"),
        ("Where are you presently located?", "text", "location"),
    ],
)
async def test_real_unmapped_questions_use_named_values(profile, label, kind, key):
    field = FormField(id="question", frame=0, label=label, kind=kind)
    actions, unresolved = await WorkflowAgent().plan([field], profile)
    assert not unresolved and len(actions) == 1
    assert actions[0].value == profile.values()[key]
    assert actions[0].source == f"facts:{key}"
    assert not profile.answers


@pytest.mark.parametrize(
    "label",
    [
        "What is your desired annual salary (USD)?",
        "What is your current salary?",
        "What is your desired hourly pay?",
        "Whats your current CTC, please mentioned fixed and variable part.*Required",
        "How many years of hands-on Kubernetes experience do you have?*Required",
        "Please confirm that you have read and agree to Canonical's Recruitment Privacy Notice and Privacy Policy.*",
        "Are you a citizen of Canada?",
        "What specifically at Relay interests you?",
    ],
)
async def test_no_substitution_for_missing_or_different_declarations(profile, label):
    actions, unresolved = await WorkflowAgent().plan(
        [FormField(id="q", frame=0, label=label, kind="text", required=True)], profile
    )
    assert not actions and len(unresolved) == 1


async def test_named_values_are_live_profile_data_not_hardcoded_answers(profile):
    profile.application.notice_period_days = 30
    profile.application.availability_summary = None
    profile.application.compensation.annual_target = 123456
    profile.application.compensation.expectations = None
    profile.screening.work_authorization["CA"].authorized = False
    profile.consents.application_processing = False
    fields = [
        FormField(id=str(i), frame=0, label=label, kind="text")
        for i, label in enumerate(
            [
                "What is your current notice period?",
                "What are your salary expectations?",
                "Are you legally authorized to work in Canada?",
                "I consent to processing my application data",
            ]
        )
    ]
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not unresolved
    assert [a.value for a in actions] == ["30 days", "CAD 123456 per year", False, False]
    assert profile.values()["authorized_canada"] is False


async def test_agent_can_select_named_authorization_declaration(profile):
    class Agent:
        async def map(self, fields, keys):
            assert "authorized_canada" in keys
            assert "screening.work_authorization.CA.authorized" in keys
            return [Mapping(field_id="0:q", fact_key="authorized_canada", confidence=1)]

    actions, unresolved = await WorkflowAgent(Agent()).plan(
        [
            FormField(
                id="q",
                frame=0,
                kind="select",
                label="Do you have permission for employment in Canada?",
                options=[
                    Option(label="Yes", value="permit_yes"),
                    Option(label="No", value="permit_no"),
                ],
            )
        ],
        profile,
    )
    assert not unresolved
    assert actions[0].value == "permit_yes" and actions[0].source == "agent:authorized_canada"


@pytest.mark.parametrize(
    "key", ["authorized_us", "screening.work_authorization.US.authorized", "first_name"]
)
async def test_agent_cannot_substitute_different_country_or_identity(profile, key):
    class Agent:
        async def map(self, fields, keys):
            return [Mapping(field_id="0:q", fact_key=key, confidence=1)]

    # Explicit sensitive wording prevents an arbitrary contact field answering this question.
    with pytest.raises(ApplicationError) as exc:
        await WorkflowAgent(Agent()).plan(
            [
                FormField(
                    id="q",
                    frame=0,
                    kind="select",
                    label="Do you have permission for employment in Canada?",
                    options=[Option(label="Not a known option", value="x")],
                )
            ],
            profile,
        )
    assert exc.value.code == Code.AGENT_FAILED


async def test_named_radio_group_uses_actual_option_and_false_is_valid(profile):
    profile.experience["git_platform_apis"].has_experience = False
    fields = [
        FormField(
            id=label,
            frame=0,
            kind="radio",
            label=label,
            name="experience",
            group="Do you have hands-on experience with GitHub or GitLab APIs for workflow automation?* Required",
        )
        for label in ("Yes", "No")
    ]
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not unresolved and len(actions) == 1
    assert actions[0].field.label == "No" and actions[0].value is True


def test_demo_has_no_question_keyed_answers(tmp_path):
    data = json.loads(generate_profile(tmp_path / "demo").read_text())
    assert "answers" not in data
    assert data["screening"]["work_authorization"]["CA"]["authorized"] is True
    assert data["application"]["notice_period_days"] == 14


async def test_named_profile_fields_submit_through_browser(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner().run(f"{base}/named-profile", profile, options)
    assert result.success, result
    body = server.submissions[0][1]
    for value in (b"100000", b"14 days", b"Example Systems", b"authorize_canada_yes"):
        assert value in body


async def test_radio_option_label_is_not_a_request_for_linkedin_url(profile):
    fields = [
        FormField(
            id=label,
            frame=0,
            label=label,
            kind="radio",
            name="source",
            group="Which channel led you to apply?",
        )
        for label in ("LinkedIn", "Other")
    ]
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not actions and len(unresolved) == 2
    profile.facts["source"] = "LinkedIn"
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not unresolved and len(actions) == 1
    assert actions[0].value is True and actions[0].source == "facts:source"
