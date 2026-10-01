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


async def test_combobox_reads_committed_value_past_empty_data_value_wrapper(page):
    await page.set_content("""<label id="answer-label">Answer*
      <div><div class="select__value-container"><div class="select__single-value">Yes</div>
      <div data-value=""><input role="combobox" aria-labelledby="answer-label"></div>
      </div></div></label>""")
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value="Yes", source="facts:answer", choice_labels=["Yes"])
    assert await combobox_matches(page, locator(page, field), action, "Yes")


async def test_country_combobox_accepts_committed_dial_code_after_exact_selection(page):
    await page.set_content("""<label id="country-label">Country*
      <div><div class="select__value-container"><div class="select__single-value">+1</div>
      <div data-value=""><input role="combobox" aria-labelledby="country-label"></div>
      </div></div></label>""")
    field = (await snapshot(page)).fields[0]
    action = Action(
        field=field,
        value="Canada +1",
        source="facts:country",
        choice_labels=["Canada +1"],
    )
    assert await combobox_matches(page, locator(page, field), action, "Canada +1")


async def test_multiselect_combobox_verifies_one_committed_chip(page):
    await page.set_content("""<label id="gender-label">Gender*
      <div><div class="select__value-container select__value-container--is-multi">
      <div class="select__multi-value"><span>Male</span></div>
      <div class="select__multi-value"><span>Non-binary</span></div>
      <div data-value=""><input role="combobox" aria-labelledby="gender-label"></div>
      </div></div></label>""")
    field = (await snapshot(page)).fields[0]
    assert field.filled
    action = Action(field=field, value="Male", source="facts:gender", choice_labels=["Male"])
    assert await combobox_matches(page, locator(page, field), action, "Male")


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
    # Readback uses owned selected-state metadata without opening the popup.
    assert await page.evaluate("window.opens || 0") == 0
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


async def test_combobox_reacquires_node_replaced_when_option_commits(page):
    await page.set_content("""<form><label>Country<input name="country" role="combobox"
      aria-controls="countries" onclick="document.querySelector('#countries').hidden=false"></label>
      <div id="countries" role="listbox" hidden><div role="option" onclick="
        const old=document.querySelector('[name=country]');
        const next=old.cloneNode(); next.value='Canada';
        next.setAttribute('aria-expanded','false'); next.removeAttribute('data-wagecuck-id');
        old.replaceWith(next); this.parentElement.hidden=true">Canada</div></div></form>""")
    field = (await prepared_snapshot(page)).fields[0]
    old_id = field.id
    action = Action(field=field, value="Canada", source="facts:country")
    await fill(page, action)
    assert action.field.id == old_id
    assert await locator(page, action.field).count() == 1
    assert (await verify_action_results(page, [action]))[0].valid


async def test_combobox_reacquires_unnamed_node_replaced_while_typing(page):
    await page.set_content("""<form><label id="country-label">Country*
      <input role="combobox" aria-labelledby="country-label" aria-controls="countries"
        oninput="if(!window.replaced){window.replaced=true;const next=this.cloneNode();
          next.value=this.value;next.removeAttribute('data-wagecuck-id');this.replaceWith(next)}"
        onclick="document.querySelector('#countries').hidden=false"></label>
      <div id="countries" role="listbox" hidden><div role="option" onclick="
        const input=document.querySelector('[role=combobox]');input.value='Canada';
        this.parentElement.hidden=true">Canada</div></div></form>""")
    field = (await prepared_snapshot(page)).fields[0]
    action = Action(field=field, value="Canada", source="facts:country")
    await fill(page, action)
    assert await page.locator('[role="combobox"]').input_value() == "Canada"
    assert (await verify_action_results(page, [action]))[0].valid


async def test_combobox_uses_keyboard_fallback_when_option_click_does_not_commit(page):
    await page.set_content("""<label id="answer-label">Answer*
      <input role="combobox" aria-labelledby="answer-label" aria-controls="answers"
        aria-expanded="false" onclick="this.setAttribute('aria-expanded','true');
          document.querySelector('#answers').hidden=false"
        onkeydown="if(event.key==='Enter'){this.value='Yes';
          this.setAttribute('aria-expanded','false');document.querySelector('#answers').hidden=true}">
      </label><div id="answers" role="listbox" hidden><div role="option">Yes</div></div>""")
    field = (await prepared_snapshot(page)).fields[0]
    action = Action(field=field, value="Yes", source="facts:answer", choice_labels=["Yes"])
    await fill(page, action)
    assert await page.locator('[role="combobox"]').input_value() == "Yes"


async def test_parser_and_handler_support_aria_choices_and_contenteditable(page):
    await page.set_content("""<form>
      <div role="checkbox" aria-checked="false" aria-label="Agree"
        onclick="this.setAttribute('aria-checked', this.getAttribute('aria-checked') !== 'true')">Agree</div>
      <div role="radio" aria-checked="false" aria-label="Remote"
        onclick="this.setAttribute('aria-checked','true')">Remote</div>
      <div role="textbox" contenteditable="true" aria-label="Summary"><br></div>
      </form>""")
    fields = (await snapshot(page)).fields
    assert [field.control_type for field in fields] == [
        "checkbox", "radio", "contenteditable_text"
    ]
    actions = [
        Action(field=fields[0], value=True, source="facts:agree"),
        Action(field=fields[1], value=True, source="facts:remote"),
        Action(field=fields[2], value="Frontend engineer", source="facts:summary"),
    ]
    for action in actions:
        await fill(page, action)
    assert all(result.valid for result in await verify_action_results(page, actions))


async def test_roleless_autocomplete_selects_and_verifies_committed_suggestion(page, profile):
    await page.set_content("""<div class="application-field">
      <label for="location-input">Current location</label>
      <input id="location-input" name="location" class="location-input"
        oninput="document.querySelector('.dropdown-container').hidden=false">
      <input id="selected-location" name="selectedLocation" type="hidden">
      <div class="dropdown-container" hidden>
        <div class="dropdown-location" onclick="
          document.querySelector('#location-input').value='Toronto, ON, CAN';
          document.querySelector('#selected-location').value='toronto-on-can';
          this.parentElement.hidden=true">Toronto, ON, CAN</div>
      </div></div>""")
    field = (await snapshot(page)).fields[0]
    assert field.kind == "text"
    assert field.control_type == "dynamic_combobox"
    actions, unresolved = await WorkflowAgent().plan([field], profile)
    assert not unresolved
    assert actions[0].source == "facts:location"
    await fill(page, actions[0])
    assert await page.locator("#location-input").input_value() == "Toronto, ON, CAN"
    assert await page.locator("#selected-location").input_value() == "toronto-on-can"
    await verify_actions(page, actions)


async def test_captcha_controls_are_excluded_from_application_fields(page):
    await page.set_content("""<label>Email<input name="email" required></label>
      <div class="g-recaptcha"><label>I'm not a robot
        <input id="recaptcha-anchor" name="recaptcha-anchor" type="checkbox">
      </label></div>""")
    fields = (await snapshot(page)).fields
    assert len(fields) == 1
    assert fields[0].name == "email"


async def test_generated_radio_names_share_explicit_fieldset_group(page):
    await page.set_content("""<fieldset role=radiogroup aria-label="Authorized to work in the US?">
      <div role=radio id=generated_yes aria-label=Yes aria-checked=false></div>
      <div role=radio id=generated_no aria-label=No aria-checked=false></div>
    </fieldset>""")
    yes, no = (await snapshot(page)).fields
    assert yes.group == no.group == "Authorized to work in the US?"
    assert yes.group_id == no.group_id


async def test_search_combobox_uses_keyboard_and_scoped_heading_when_for_target_is_missing(page):
    await page.set_content("""<div class="ashby-application-form-field-entry">
      <label class="ashby-application-form-question-title" for="generated-question-id">
        Current Location</label>
      <input role="combobox" aria-autocomplete="list" placeholder="Start typing..."
        onkeydown="document.querySelector('[role=listbox]').hidden=false">
      <div role="listbox" hidden><div role="option" onclick="
        document.querySelector('[role=combobox]').value='Toronto, ON, CAN';
        this.setAttribute('aria-selected','true');this.parentElement.hidden=true">
        Toronto, ON, CAN</div></div></div>""")
    field = (await snapshot(page)).fields[0]
    assert field.label == "Current Location"
    action = Action(field=field, value="Toronto, Ontario, Canada", source="facts:location")
    await fill(page, action)
    assert action.choice_labels == ["Toronto, ON, CAN"]
    await verify_actions(page, [action])


async def test_search_combobox_without_popup_retains_accepted_free_text(page):
    await page.set_content("""<label>Current Location
      <input role="combobox" aria-autocomplete="list" placeholder="Start typing...">
      </label>""")
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value="Toronto, Ontario, Canada", source="facts:location")
    await fill(page, action)
    assert await page.get_by_role("combobox").input_value() == "Toronto, Ontario, Canada"
    await verify_actions(page, [action])


async def test_portal_multiselect_owns_active_menu_and_traverses_nested_choices(page):
    await page.set_content("""<div class="application-field">
      <label>How Did You Hear About Us?*<input id=source data-uxi-widget-type=selectinput
        data-uxi-multiselect-id=source-widget onclick="openRoot()"></label></div>
      <div class="phone-field"><ul role=listbox data-automation-id=selectedItemList>
        <li role=option data-automation-id=selectedItem>Canada (+1)</li></ul></div>
      <div id=portal></div><script>
      function openRoot(){portal.innerHTML=`<div role=listbox data-automation-id=activeListContainer>
        <div role=option onclick="openChild()">Job Board</div></div>`}
      function openChild(){portal.innerHTML=`<div role=listbox data-automation-id=activeListContainer>
        <div role=option onclick="commit()">Company Website</div></div>`}
      function commit(){source.closest('.application-field').insertAdjacentHTML('beforeend',
        '<div role="option" data-automation-id="selectedItem">Company Website</div>');portal.innerHTML=''}
      </script>""")
    field = (await snapshot(page)).fields[0]
    assert field.control_type == "dynamic_combobox"
    action = Action(field=field, value="", source="random:source", random_choice=True)
    await fill(page, action)
    assert action.value == "Company Website"
    assert action.choice_labels == ["Company Website"]
    await verify_actions(page, [action])


async def test_roleless_country_phone_code_reads_preselected_pill(page, profile):
    await page.set_content("""<div class="application-field"><label>Country Phone Code*
      <input data-uxi-widget-type=selectinput data-uxi-multiselect-id=phone-code required>
      </label><div role=option data-automation-id=selectedItem>Canada (+1)</div></div>""")
    field = (await snapshot(page)).fields[0]
    assert field.control_type == "dynamic_combobox"
    assert field.filled
    actions, unresolved = await WorkflowAgent().plan([field], profile)
    assert not unresolved and actions[0].source == "facts:country"
    assert (await verify_action_results(page, actions))[0].valid


async def test_fill_emits_standard_events_and_settles_focus_for_native_select(page):
    await page.set_content("""<label>Level<select id=level
      onfocus="window.focusEvents=(window.focusEvents||0)+1"
      onblur="window.blurEvents=(window.blurEvents||0)+1"
      oninput="window.inputEvents=(window.inputEvents||0)+1"
      onchange="window.changeEvents=(window.changeEvents||0)+1">
      <option value="">Choose</option><option value="senior">Senior</option>
      </select></label>""")
    field = (await snapshot(page)).fields[0]
    await fill(page, Action(field=field, value="Senior", source="facts:level"))
    assert await page.evaluate(
        "[window.focusEvents, window.blurEvents, window.inputEvents, window.changeEvents]"
    ) == [1, 1, 1, 1]


@pytest.mark.parametrize(
    "markup,reason",
    [
        ('<input type=email value="not-an-email">', "typeMismatch"),
        ('<input pattern="[A-Z]{3}" value="abc">', "patternMismatch"),
        ('<input type=number min=10 value=9>', "rangeUnderflow"),
        ('<input type=number max=10 value=11>', "rangeOverflow"),
        ('<input type=number min=0 step=2 value=3>', "stepMismatch"),
        ('<input required>', "valueMissing"),
    ],
)
async def test_snapshot_records_safe_validity_state_reasons(page, markup, reason):
    await page.set_content(f"<label>Value{markup}</label>")
    field = (await snapshot(page)).fields[0]
    assert field.invalid
    assert reason in field.validity_errors
