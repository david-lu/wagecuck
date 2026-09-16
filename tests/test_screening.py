import json

import pytest

from wagecuck import ApplicationRunner
from wagecuck.agent import WorkflowAgent
from wagecuck.demo import generate_profile
from wagecuck.models import FormField, Option, Profile
from wagecuck.screening import answer_for


@pytest.mark.parametrize(
    "question,expected",
    [
        ("Are you legally authorized to work in the United States?", True),
        ("Are you eligible for employment in the U.S.?", True),
        ("Do you have the right to work in the USA?", True),
        ("What is your current visa type?", "TN"),
        ("Will you now or in the future require visa sponsorship?", False),
        ("Do you require sponsorship for employment in the United States?", False),
        ("Do you have a disability?", False),
        ("Are you disabled?", False),
        ("Disability status", False),
        ("Veteran status", False),
        ("Are you a protected veteran?", False),
        ("Which gender do you identify as?", "Non-binary"),
        ("What is your sexual orientation?", "Bisexual"),
        ("Do you identify as transgender?", False),
        ("Please indicate your race or ethnicity", "White"),
        ("Are you Hispanic or Latino?", False),
    ],
)
def test_explicit_profile_declarations(question, expected, profile):
    assert answer_for(question, profile.screening).value == expected


@pytest.mark.parametrize(
    "question",
    [
        "Are you authorized to work in Germany?",
        "Are you authorized to work in the UK?",
        "Are you authorized to work in the US without sponsorship?",
        "Are you a US citizen or permanent resident?",
        "Do you require H-1B sponsorship?",
        "Will you require a reasonable accommodation?",
        "Are you a disabled veteran?",
        "Please describe your disability",
        "I certify that I have a disability",
    ],
)
def test_does_not_infer_other_legal_or_medical_answers(question, profile):
    assert answer_for(question, profile.screening) is None


async def test_expanded_negative_disability_option_needs_history_fact(profile):
    field = FormField(
        id="d",
        frame=0,
        kind="select",
        label="Disability status",
        required=True,
        options=[
            Option(
                label="No, I do not have a disability and have not had one in the past", value="2"
            ),
            Option(label="I do not wish to answer", value="3"),
        ],
    )
    profile.screening.disability_history = None
    actions, unresolved = await WorkflowAgent().plan([field], profile)
    assert not actions and unresolved == [field]
    profile.screening.disability_history = False
    actions, unresolved = await WorkflowAgent().plan([field], profile)
    assert actions[0].value == "2" and not unresolved


async def test_exact_answer_overrides_semantic_declaration(profile):
    profile.answers["Disability status"] = "I do not wish to answer"
    field = FormField(id="d", frame=0, kind="select", label="Disability status")
    actions, _ = await WorkflowAgent().plan([field], profile)
    assert actions[0].value == "I do not wish to answer"


async def test_demographic_declarations_fill_choice_controls(profile):
    fields = [
        *[
            FormField(
                id=f"gender-{value}",
                frame=0,
                kind="radio",
                name="gender",
                group="Gender identity",
                label=label,
                required=True,
            )
            for value, label in (("man", "Man"), ("nonbinary", "Non-binary"), ("decline", "Prefer not to say"))
        ],
        FormField(
            id="orientation",
            frame=0,
            kind="select",
            name="orientation",
            label="Sexual orientation",
            required=True,
            options=[
                Option(label="Straight", value="straight-value"),
                Option(label="Bisexual", value="bisexual-value"),
            ],
        ),
        *[
            FormField(
                id=f"race-{value}",
                frame=0,
                kind="checkbox",
                name=f"race_{value}",
                group="Race or ethnicity",
                label=label,
                required=True,
            )
            for value, label in (("asian", "Asian"), ("white", "White"))
        ],
        *[
            FormField(
                id=f"pronouns-{value}",
                frame=0,
                kind="radio",
                name="pronouns",
                group="Preferred pronouns",
                label=label,
                required=True,
            )
            for value, label in (("she", "She/Her"), ("they", "They/Them"))
        ],
    ]
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not unresolved
    assert {(action.field.id, action.value) for action in actions} == {
        ("gender-nonbinary", True),
        ("orientation", "bisexual-value"),
        ("race-white", True),
        ("pronouns-they", True),
    }
    assert all(action.source.startswith(("screening:", "facts:")) for action in actions)


async def test_demographic_radio_can_use_shared_local_context_without_a_group(profile):
    context = "Gender identity: Man, Non-binary, or Prefer not to say"
    fields = [
        FormField(
            id=value,
            frame=0,
            kind="radio",
            name="",
            group="",
            context=context,
            label=label,
            required=True,
        )
        for value, label in (("man", "Man"), ("nonbinary", "Non-binary"), ("decline", "Prefer not to say"))
    ]
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not unresolved
    assert [(action.field.id, action.value) for action in actions] == [("nonbinary", True)]


def test_tn_does_not_infer_sponsorship(profile):
    profile.screening.work_authorization["US"].requires_sponsorship = None
    assert answer_for("Do you require visa sponsorship?", profile.screening) is None
    assert answer_for("What is your visa status?", profile.screening).value == "TN"


async def test_screening_complete_browser_submission(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner().run(f"{base}/screening", profile, options)
    assert result.success, result.model_dump()
    assert len(server.submissions) == 1
    body = server.submissions[0][1]
    for value in (
        b"tn",
        b"not-protected",
        b"No, I do not have a disability and have not had one in the past",
        b'name="pronouns"\r\n\r\nthey',
        b'name="gender"\r\n\r\nnonbinary',
        b'name="orientation"\r\n\r\nbisexual',
        b'name="transgender"\r\n\r\nno',
        b'name="race_white"\r\n\r\nwhite',
        b'name="hispanic"\r\n\r\nno',
    ):
        assert value in body
    sources = [
        json.loads(line).get("source", "")
        for line in (options.artifacts_dir / result.run_id / "events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert "screening:work_authorization.US.authorized" in sources
    assert "screening:work_authorization.US.requires_sponsorship" in sources
    assert "screening:disability_status" in sources


def test_demo_has_requested_declarations(tmp_path):
    profile = Profile.load(generate_profile(tmp_path / "demo"))
    assert profile.synthetic
    assert profile.screening.work_authorization["US"].authorized is True
    assert profile.screening.work_authorization["US"].visa_type == "TN"
    assert profile.screening.veteran_status == "not_a_veteran"
    assert profile.screening.disability_status == "no_disability"
    assert profile.application.pronouns == "they/them"
    assert profile.screening.demographics.gender_identity == "non_binary"
    assert profile.screening.demographics.sexual_orientation == "bisexual"
    assert profile.screening.demographics.transgender_status is False
    assert profile.screening.demographics.race_ethnicity == ["white"]
    assert profile.screening.demographics.hispanic_latino is False
