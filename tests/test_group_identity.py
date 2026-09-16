from playwright.async_api import async_playwright

from wagecuck.agent import WorkflowAgent
from wagecuck.browser import fill, snapshot
from wagecuck.field_semantics import fields_metadata
from wagecuck.inference import FieldAnswer, apply_inferred_answers
from wagecuck.logical_fields import logical_field_results, logical_groups, logical_key
from wagecuck.models import FormField


def choice(id, kind="radio", **kwargs):
    return FormField(id=id, frame=0, kind=kind, label=kwargs.pop("label", id), **kwargs)


async def test_native_groups_follow_form_owner_and_name_and_survive_rerender():
    html = """<form><fieldset><legend>Repeated heading</legend>
      <label>Yes<input type=radio name=one></label><label>No<input type=radio name=one></label>
      <label>Yes<input type=radio name=two></label><label>No<input type=radio name=two></label>
    </fieldset></form>
    <form><fieldset><legend>Repeated heading</legend>
      <label>Yes<input type=radio name=one></label><label>No<input type=radio name=one></label>
    </fieldset></form>"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html)
        before = (await snapshot(page)).fields
        await page.set_content(html)
        after = (await snapshot(page)).fields
        assert [logical_key(field) for field in before] == [logical_key(field) for field in after]
        assert [len(group) for group in logical_groups(after)] == [2, 2, 2]
        assert (
            len(
                {
                    tuple(option["field_id"] for option in row["group_options"])
                    for row in fields_metadata(after)
                }
            )
            == 3
        )
        await browser.close()


async def test_repeated_checkbox_headings_are_separate_questions():
    html = """<form>
      <div class=application-question><h3>Skills</h3>
        <label>Python<input type=checkbox name=skills[]></label>
        <label>Go<input type=checkbox name=skills[]></label></div>
      <div class=application-question><h3>Skills</h3>
        <label>Python<input type=checkbox name=skills[]></label>
        <label>Go<input type=checkbox name=skills[]></label></div>
    </form>"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html)
        groups = logical_groups((await snapshot(page)).fields)
        assert [len(group) for group in groups] == [2, 2]
        assert logical_key(groups[0][0]) != logical_key(groups[1][0])
        await browser.close()


def test_group_identity_is_frame_scoped_and_used_by_inference():
    fields = [
        choice("a-yes", name="same", group="Same", group_id="form-a", label="Yes"),
        choice("a-no", name="same", group="Same", group_id="form-a", label="No"),
        choice("b-yes", name="same", group="Same", group_id="form-b", label="Yes"),
        choice("b-no", name="same", group="Same", group_id="form-b", label="No"),
    ]
    assert logical_key(fields[0]) != logical_key(fields[0].model_copy(update={"frame": 1}))
    answers = [
        FieldAnswer(
            field_id="0:a-yes", value=True, basis="made_up", fact_keys=[], reason="Example choice"
        )
    ]
    actions, resolved, warnings = apply_inferred_answers(fields, answers, {})
    assert len(actions) == 1 and not warnings
    assert resolved == {"0:a-yes", "0:a-no"}


async def test_screening_clears_stale_checks_and_selects_every_requested_race(profile):
    profile.screening.demographics.race_ethnicity = ["asian", "white"]
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        await page.set_content("""<fieldset><legend>Race or ethnicity</legend>
          <label>Asian<input type=checkbox name=asian></label>
          <label>White<input type=checkbox name=white></label>
          <label>Prefer not to say<input type=checkbox name=decline checked></label>
        </fieldset>""")
        fields = (await snapshot(page)).fields
        actions, unresolved = await WorkflowAgent().plan(fields, profile)
        assert not unresolved
        assert {action.field.name: action.value for action in actions} == {
            "asian": True,
            "white": True,
            "decline": False,
        }
        for action in actions:
            await fill(page, action)
        assert await page.locator("input:checked").evaluate_all(
            "els => els.map(el => el.name)"
        ) == ["asian", "white"]
        await browser.close()


async def test_missing_requested_checkbox_option_stays_unresolved(profile):
    profile.screening.demographics.race_ethnicity = ["asian", "white"]
    fields = [
        choice("Asian", "checkbox", group="Race or ethnicity", group_id="race", required=True)
    ]
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not actions and unresolved == fields


def test_partial_group_outcome_never_masks_unresolved_members():
    fields = [
        choice("Asian", "checkbox", group="Race", required=True),
        choice("White", "checkbox", group="Race", required=True),
    ]
    rows = logical_field_results(
        fields,
        [{"field_id": "0:Asian", "code": "FILLED", "selected": True}],
        [{"field_id": "0:White", "route": "unresolved"}],
    )
    assert rows[0]["code"] == "UNRESOLVED"


async def test_boolean_checkbox_group_selects_one_option_and_clears_other(profile):
    fields = [
        choice("Yes", "checkbox", group="Are you Hispanic or Latino?", filled=True),
        choice("No", "checkbox", group="Are you Hispanic or Latino?"),
    ]
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not unresolved
    assert {action.field.id: action.value for action in actions} == {"Yes": False, "No": True}


async def test_multiple_requested_races_do_not_collapse_to_one_radio(profile):
    profile.screening.demographics.race_ethnicity = ["asian", "white"]
    fields = [
        choice("Asian", group="Race or ethnicity", name="race"),
        choice("White", group="Race or ethnicity", name="race"),
    ]
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not actions and unresolved == fields


async def test_fallback_sees_and_clears_prefilled_checkbox_peer(profile):
    fields = [
        choice("Chess", "checkbox", group="Hobbies", filled=True),
        choice("Hiking", "checkbox", group="Hobbies"),
    ]

    class Fallback:
        async def map(self, fields, keys):
            return []

        async def infer(self, candidates, facts):
            assert {field.id for field in candidates} == {"Chess", "Hiking"}
            return [
                FieldAnswer(
                    field_id=f"0:{field.id}",
                    value=field.id == "Hiking",
                    basis="made_up",
                    fact_keys=[],
                    reason="Example preference",
                )
                for field in candidates
            ]

    actions, unresolved = await WorkflowAgent(Fallback()).plan(fields, profile, agent_fill=True)
    assert not unresolved
    assert {action.field.id: action.value for action in actions} == {"Chess": False, "Hiking": True}


def test_partial_inferred_checkbox_selection_stays_unresolved():
    fields = [
        choice("Chess", "checkbox", group="Hobbies"),
        choice("Hiking", "checkbox", group="Hobbies"),
    ]
    answers = [
        FieldAnswer(
            field_id="0:Hiking",
            value=True,
            basis="made_up",
            fact_keys=[],
            reason="Example preference",
        )
    ]
    actions, resolved, warnings = apply_inferred_answers(fields, answers, {})
    assert not actions and not resolved and warnings
