import pytest
from playwright.async_api import async_playwright

from wagecuck.agent import WorkflowAgent
from wagecuck.browser import fill, snapshot, verify_actions


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        page.set_default_timeout(2000)
        yield page
        await browser.close()


async def test_labels_are_scoped_and_shadow_aria_is_resolved(page, profile):
    await page.set_content("""<label for="first">First Name *<span> </span></label><input id="first">
      <label>Last Name <input id="last"></label>
      <div id="shadow"></div><input name="candidate[postalCode]">
      <label>Town<input id="town"></label><input placeholder="Unrelated unknown answer">
      <script>document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML=
        '<span id="title">Email Address</span><input aria-labelledby="title" type="email">';</script>""")
    snap = await snapshot(page)
    actions, unresolved = await WorkflowAgent().plan(snap.fields, profile)
    assert {a.source for a in actions} == {
        "facts:first_name",
        "facts:last_name",
        "facts:email",
        "facts:postal_code",
        "facts:city",
    }
    assert [f.label for f in unresolved] == ["Unrelated unknown answer"]
    for action in actions:
        await fill(page, action)
    await verify_actions(page, actions)


async def test_radio_question_required_and_changed_default(page, profile):
    await page.set_content("""<div class="application-question">
      <div class="application-label">Do you require sponsorship? *</div>
      <label>Yes<input type="radio" name="sponsor" checked></label>
      <label>No<input type="radio" name="sponsor"></label></div>""")
    snap = await snapshot(page)
    assert all(f.group == "Do you require sponsorship? *" and f.required for f in snap.fields)
    actions, unresolved = await WorkflowAgent().plan(snap.fields, profile)
    assert len(actions) == 1 and actions[0].field.label == "No" and not unresolved
    await fill(page, actions[0])
    assert not await page.get_by_label("Yes", exact=True).is_checked()
    await verify_actions(page, actions)


async def test_required_group_without_native_required_is_not_silently_skipped(page, profile):
    await page.set_content("""<div data-field-path="eligibility">
      <label class="_heading_abcd _required_abcd_91">Export authorization</label>
      <div><label>Option A<input type="radio" name="eligibility"></label>
      <label>Option B<input type="radio" name="eligibility"></label></div></div>""")
    fields = (await snapshot(page)).fields
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not actions and len(unresolved) == 2
    assert all(f.required and f.group == "Export authorization" for f in unresolved)
    profile.answers["Export authorization"] = "Unavailable option"
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert not actions and len(unresolved) == 2


async def test_upload_uses_local_heading_and_does_not_borrow_neighbor(page, profile):
    await page.set_content("""<div><label>Resume *</label><div><div>
      <button>Choose File*</button><input type="file" id="file-input"></div></div></div>
      <div><label>Email<input type="email"></label><input type="file" id="unknown-upload"></div>""")
    fields = (await snapshot(page)).fields
    resume = next(f for f in fields if f.name == "file-input")
    assert resume.required and resume.label == "Resume *"
    unknown = next(f for f in fields if f.name == "unknown-upload")
    assert unknown.label == "unknown-upload"
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert any(a.source == "document:resume" for a in actions)
    assert unknown in unresolved


async def test_react_select_readonly_input_keeps_selection_in_sibling(page, profile):
    await page.set_content("""<label for="country">Country</label><div>
      <div class="select__single-value"></div><input id="country" role="combobox" readonly
        onclick="document.querySelector('[role=listbox]').hidden=false">
      <div role="listbox" hidden><div role="option" onclick="document.querySelector('.select__single-value').textContent='Canada';this.parentElement.hidden=true">Canada</div></div></div>""")
    actions, _ = await WorkflowAgent().plan((await snapshot(page)).fields, profile)
    assert len(actions) == 1
    await fill(page, actions[0])
    assert await page.locator("#country").input_value() == ""
    await verify_actions(page, actions)
    assert (await snapshot(page)).fields[0].filled


@pytest.mark.parametrize("rendered", ["Canada +1", "+1"])
async def test_country_option_with_dial_code_and_separate_phone_prefix(page, profile, rendered):
    await page.set_content(
        """<label for="country">Country</label><div>
      <div class="select__single-value"></div><input id="country" role="combobox"
        oninput="document.querySelector('[role=listbox]').hidden=false"
        onclick="document.querySelector('[role=listbox]').hidden=false"
        onkeydown="if(event.key==='Escape')document.querySelector('[role=listbox]').hidden=true">
      <div role="listbox" hidden><div role="option" aria-selected="false" onclick="document.querySelector('.select__single-value').textContent='{{selected}}';this.setAttribute('aria-selected','true');document.querySelector('#country').value='';this.parentElement.hidden=true">Canada +1</div></div></div>
      <div class="iti--separate-dial-code"><span class="iti__selected-dial-code">+1</span>
      <label>Phone<input type="tel" onblur="if(this.value.startsWith('+1'))this.value=this.value.slice(2)"></label></div>""".replace(
            "{{selected}}", rendered
        )
    )
    actions, _ = await WorkflowAgent().plan((await snapshot(page)).fields, profile)
    assert len(actions) == 2
    for action in actions:
        await fill(page, action)
    await verify_actions(page, actions)


async def test_radio_group_label_can_reference_group_container(page, profile):
    await page.set_content("""<div data-field-path="sponsor"><label for="options" class="_required_123">Do you require sponsorship?</label>
      <div id="options"><label for="yes">Yes</label><input type="radio" name="sponsor" id="yes">
      <label for="no">No</label><input type="radio" name="sponsor" id="no"></div></div>""")
    fields = (await snapshot(page)).fields
    assert all(f.group == "Do you require sponsorship?" and f.required for f in fields)
    actions, unresolved = await WorkflowAgent().plan(fields, profile)
    assert len(actions) == 1 and actions[0].field.name == "sponsor" and not unresolved
    await fill(page, actions[0])
    assert await page.locator("#no").is_checked()
