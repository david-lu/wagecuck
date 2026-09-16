import pytest
from playwright.async_api import async_playwright

from wagecuck.agent import WorkflowAgent
from wagecuck.browser import fill, snapshot, verify_actions
from wagecuck.models import FormField, Option


@pytest.mark.parametrize("kind", ["select", "radio"])
@pytest.mark.parametrize("chosen", [0, 2])
async def test_random_source_uses_any_actual_option_once(profile, monkeypatch, kind, chosen):
    calls = []

    def choose(options):
        calls.append(options)
        return options[chosen]

    monkeypatch.setattr("random.choice", choose)
    labels = ["Employee referral", "Company careers page", "Other"]
    fields = [
        FormField(
            id=str(i),
            frame=0,
            name="source",
            kind=kind,
            label=label if kind == "radio" else "How did you hear about us?",
            group="How did you hear about us?" if kind == "radio" else "",
            options=[Option(label=l, value=str(n)) for n, l in enumerate(labels)],
        )
        for i, label in enumerate(labels if kind == "radio" else [""])
    ]
    actions, missing = await WorkflowAgent().plan(fields, profile)
    assert not missing and len(actions) == 1 and len(calls) == 1
    assert actions[0].source == "random:source"
    assert actions[0].value == (True if kind == "radio" else str(chosen))
    if kind == "radio":
        assert actions[0].field.label == labels[chosen]


async def test_random_source_can_be_disabled(profile):
    profile.application.randomize_source = False
    profile.facts["source"] = "Other"
    actions, missing = await WorkflowAgent().plan(
        [
            FormField(
                id="s",
                frame=0,
                label="Where did you find this job?",
                kind="select",
                options=[Option(label="Other", value="other")],
            )
        ],
        profile,
    )
    assert not missing and actions[0].value == "other"
    assert actions[0].source == "facts:source"


async def test_random_browser_selection_and_verification(profile, monkeypatch):
    monkeypatch.setattr("random.choice", lambda options: options[-1])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content("""<form>
        <label>How did you hear about us?<select name="native"><option value="prompt">Please select</option><option value="li">LinkedIn</option><option value="ref">Employee referral</option><option value="off" disabled>Disabled</option></select></label>
        <fieldset><legend>Which channel led you to apply?</legend><label>LinkedIn<input type="radio" name="radio" value="li"></label><label>Other<input type="radio" name="radio" value="other"></label></fieldset>
        <label>Where did you find this job?<input name="custom" role="combobox" aria-controls="choices" onclick="document.getElementById('choices').hidden=false"></label>
        <div id="choices" role="listbox" hidden><div role="option">Please select</div><div role="option" onclick="document.querySelector('[name=custom]').value='Meetup';this.parentElement.hidden=true">Meetup</div><div role="option" aria-disabled="true">Disabled</div></div>
        <div role="listbox"><div role="option">Unrelated option</div></div>
        </form>""")
        actions, missing = await WorkflowAgent().plan((await snapshot(page)).fields, profile)
        assert not missing and len(actions) == 3
        for action in actions:
            await fill(page, action)
        await verify_actions(page, actions)
        assert await page.locator("[name=native]").input_value() == "ref"
        assert await page.locator("[name=radio]:checked").input_value() == "other"
        assert await page.locator("[name=custom]").input_value() == "Meetup"
        assert all(
            row["route"] == "random_choice"
            for row in WorkflowAgent.describe([action.field for action in actions], actions, [])
        )
        await browser.close()
