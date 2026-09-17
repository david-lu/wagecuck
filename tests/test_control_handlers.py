import pytest
from playwright.async_api import async_playwright

from wagecuck.agent import WorkflowAgent
from wagecuck.browser import (
    combobox_matches,
    fill,
    locator,
    prepared_snapshot,
    snapshot,
    verify_action_results,
    verify_actions,
)
from wagecuck.field_values import FieldValueError, normalize_field_value
from wagecuck.models import Action, ApplicationError, Code, FormField


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        page.set_default_timeout(1000)
        yield page
        await browser.close()


@pytest.mark.parametrize("ownership", ["aria-controls", "aria-owns"])
@pytest.mark.parametrize("random_choice", [False, True])
async def test_dropdown_selection_uses_owned_popup_with_duplicate_options(
    page, ownership, random_choice
):
    await page.set_content(f"""<label>Country<input id="country" role="combobox" readonly
      {ownership}="owned" onclick="document.querySelector('#owned').hidden=false"></label>
      <div id="owned" role="listbox" hidden><div role="option"
      onclick="document.querySelector('#country').value='Canada';this.parentElement.hidden=true">Canada</div></div>
      <div role="listbox"><div role="option" onclick="window.wrong=true">Canada</div></div>""")
    field = (await snapshot(page)).fields[0]
    action = Action(
        field=field, value="Canada", source="facts:country", random_choice=random_choice
    )
    await fill(page, action)
    await verify_actions(page, [action])
    assert await page.locator("#country").input_value() == "Canada"
    assert not await page.evaluate("Boolean(window.wrong)")


async def test_country_dial_code_verification_is_scoped_to_owned_popup(page):
    await page.set_content("""<label>Country<input id="country" role="combobox" readonly aria-controls="owned"
      onclick="document.querySelector('#owned').hidden=false"
      onkeydown="if(event.key==='Escape')document.querySelector('#owned').hidden=true"></label>
      <div id="owned" role="listbox" hidden><div role="option" aria-selected="false"
      onclick="document.querySelector('#country').value='+1';this.setAttribute('aria-selected','true');this.parentElement.hidden=true">Canada +1</div></div>
      <div role="listbox"><div role="option" aria-selected="true">Canada +1</div></div>""")
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value="Canada", source="facts:country")
    await fill(page, action)
    await verify_actions(page, [action])


async def test_range_uses_native_setter_dispatches_events_and_verifies_retention(page):
    await page.set_content("""<label>Experience score<input type="range" min="0" max="10" step="0.5"
      oninput="window.inputEvents=(window.inputEvents||0)+1"
      onchange="window.changeEvents=(window.changeEvents||0)+1"></label>""")
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value="7.50", source="facts:score")
    await fill(page, action)
    assert action.value == "7.5"
    assert await page.locator("input").input_value() == "7.5"
    assert await page.evaluate("[window.inputEvents, window.changeEvents]") == [1, 1]
    await verify_actions(page, [action])
    await page.locator("input").evaluate("e => e.value='8'")
    results = await verify_action_results(page, [action])
    assert results[0].present and not results[0].valid
    assert results[0].code == Code.VALIDATION_FAILED


def test_range_default_limits_and_steps_are_validated_before_filling():
    field = FormField(id="range", frame=0, kind="range", label="Score")
    for value in ("-1", "101", "1.5"):
        with pytest.raises(FieldValueError):
            normalize_field_value(field, value)


async def test_verification_records_every_field_and_missing_controls_separately(page):
    await page.set_content("""<label>First name<input id="first"></label>
      <label>Last name<input id="last"></label><label>Email<input id="email" type="email"></label>""")
    fields = (await snapshot(page)).fields
    actions = [
        Action(field=field, value=value, source="facts:example")
        for field, value in zip(fields, ("Alex", "Morgan", "alex@example.com"), strict=True)
    ]
    for action in actions:
        await fill(page, action)
    await page.evaluate("""() => {
        document.querySelector('#last').value = '';
        document.querySelector('#email').remove();
    }""")
    results = await verify_action_results(page, actions)
    assert [(result.present, result.valid) for result in results] == [
        (True, True),
        (True, False),
        (False, False),
    ]
    assert results[1].code == Code.VALIDATION_FAILED
    assert results[2].code == Code.NO_PROGRESS
    with pytest.raises(ApplicationError) as exc:
        await verify_actions(page, actions)
    assert exc.value.code == Code.VALIDATION_FAILED


async def test_native_choice_and_select_readback_use_the_same_verification(page):
    await page.set_content("""<label>Agree<input type="checkbox" checked></label>
      <label>Level<select><option value="junior">Junior</option><option value="senior">Senior</option></select></label>""")
    fields = (await snapshot(page)).fields
    actions = [
        Action(field=fields[0], value=False, source="facts:agree"),
        Action(field=fields[1], value="Senior", source="facts:level"),
    ]
    for action in actions:
        await fill(page, action)
    assert all(result.valid for result in await verify_action_results(page, actions))
    await page.locator("input").check()
    await page.locator("select").select_option("junior")
    assert all(not result.valid for result in await verify_action_results(page, actions))


async def test_file_verification_checks_expected_filename(page, tmp_path):
    resume = tmp_path / "resume.txt"
    resume.write_text("Example resume")
    other = tmp_path / "other.txt"
    other.write_text("Other document")
    await page.set_content('<label>Resume<input type="file" required></label>')
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value=str(resume), source="document:resume")
    await fill(page, action)
    await verify_actions(page, [action])
    await page.locator("input").set_input_files(str(other))
    assert not (await verify_action_results(page, [action]))[0].valid


@pytest.mark.parametrize("initial", ["", "United States"])
async def test_country_preflight_returns_mismatch_without_waiting_for_selected_option(
    page, initial
):
    await page.set_content(f"""<label>Country<input id="country" role="combobox" readonly
      value="{initial}" aria-controls="owned"
      onclick="window.opens=(window.opens||0)+1;document.querySelector('#owned').hidden=false"
      onkeydown="if(event.key==='Escape')document.querySelector('#owned').hidden=true"></label>
      <div id="owned" role="listbox" hidden><div role="option" aria-selected="false">Canada</div></div>""")
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value="Canada", source="facts:country")
    assert not await combobox_matches(page, locator(page, field), action, "Canada")
    assert await page.evaluate("window.opens || 0") == (1 if initial else 0)
    assert await page.locator("#owned").is_hidden()


async def test_prepared_snapshot_classifies_controls_and_discovers_dynamic_options(page, profile):
    await page.set_content("""<form>
      <label>Veteran status<input id="veteran" role="combobox" readonly
        aria-controls="veteran-options"
        onclick="document.querySelector('#veteran-options').hidden=false"></label>
      <div id="veteran-options" role="listbox" hidden>
        <div role="option">No military service</div>
        <div role="option">Military service</div>
      </div>
      <fieldset><legend>Locations</legend>
        <label>Toronto<input type="checkbox" name="toronto"></label>
        <label>Seattle<input type="checkbox" name="seattle"></label>
      </fieldset>
    </form>""")
    snap = await prepared_snapshot(page)
    veteran, toronto, seattle = snap.fields
    assert veteran.control_type == "dynamic_combobox"
    assert [option.label for option in veteran.options] == [
        "No military service",
        "Military service",
    ]
    assert toronto.control_type == seattle.control_type == "checkbox"
    assert toronto.group_id == seattle.group_id
    actions, unresolved = await WorkflowAgent().plan([veteran], profile)
    assert not unresolved
    assert actions[0].value == "No military service"
