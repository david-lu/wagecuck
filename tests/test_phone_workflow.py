import pytest
from playwright.async_api import async_playwright

from wagecuck.agent import Mapping, WorkflowAgent
from wagecuck.browser import fill, locator, prepared_snapshot, snapshot, verify_action_results
from wagecuck.controls.combobox import discover_options
from wagecuck.execution import execute_actions
from wagecuck.field_values import normalize_field_value
from wagecuck.models import Action, ApplicationError, Code
from wagecuck.phone_numbers import phone_candidates, phone_equivalent


@pytest.fixture
async def page(monkeypatch):
    from wagecuck.controls import upload

    monkeypatch.setattr(upload, "UPLOAD_MIN_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(upload, "UPLOAD_QUIET_SECONDS", 0.1)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        page.set_default_timeout(1000)
        yield page
        await browser.close()


@pytest.mark.parametrize(
    "phone,national",
    [
        ("+1 (650) 253-2222", "6502532222"),
        ("+44 20 8366 1177", "02083661177"),
        ("+39 02 3661 8300", "0236618300"),
    ],
)
def test_phone_formats_keep_country_specific_leading_digits(phone, national):
    assert phone_candidates(phone)[0] == national
    assert phone_equivalent(national, phone)
    assert not phone_equivalent(national + "9", phone)


def test_unknown_region_and_extensions_are_not_guessed_or_discarded():
    assert phone_candidates("020 8366 1177") == ["02083661177", "020 8366 1177"]
    value = "+1 650 253 2222 ext. 42"
    assert phone_candidates(value) == [value]
    assert not phone_equivalent("6502532222", value)
    assert not phone_equivalent("6502532222", "+16502532222", "GB", "+44")


@pytest.mark.parametrize("kind", ["tel", "text"])
async def test_national_digits_are_tried_first_and_canonical_profile_value_is_kept(page, kind):
    await page.set_content(f"""<label>Phone number<input type="{kind}" required pattern="[0-9]{{10}}"
        maxlength=10 oninput="(window.attempts ||= []).push(this.value)"></label>""")
    field = (await snapshot(page)).fields[0]
    value = "+1 (650) 253-2222"
    assert normalize_field_value(field, value) == value
    action = Action(field=field, value=value, source="facts:phone")
    await fill(page, action)
    assert action.value == value
    assert await page.evaluate("window.attempts") == ["6502532222"]
    assert (await verify_action_results(page, [action]))[0].valid


@pytest.mark.parametrize(
    "pattern,expected",
    [
        (r"\+1[0-9]{10}", "+16502532222"),
        (r"1[0-9]{10}", "16502532222"),
    ],
)
async def test_phone_retries_international_formats_after_native_rejection(page, pattern, expected):
    await page.set_content(f"""<label>Phone<input type=tel required pattern="{pattern}"
        oninput="(window.attempts ||= []).push(this.value)"></label>""")
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value="+16502532222", source="facts:phone")
    await fill(page, action)
    attempts = await page.evaluate("window.attempts")
    assert attempts[0] == "6502532222"
    assert attempts[-1] == expected
    assert len(attempts) <= 3
    assert (await verify_action_results(page, [action]))[0].valid


async def test_phone_retries_after_delayed_aria_validation(page):
    await page.set_content("""<label>Phone<input type=tel oninput="(window.attempts ||= []).push(this.value)"
      onblur="const e=this;setTimeout(()=>e.setAttribute('aria-invalid',String(!e.value.startsWith('+'))),100)"></label>""")
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value="+16502532222", source="facts:phone")
    await fill(page, action)
    assert await page.evaluate("window.attempts") == ["6502532222", "+16502532222"]


async def test_invalid_phone_formats_stay_failed_and_bounded(page):
    await page.set_content(
        '<label>Phone<input type=tel aria-invalid=true oninput="window.attempts=(window.attempts||0)+1"></label>'
    )
    field = (await snapshot(page)).fields[0]
    with pytest.raises(ApplicationError) as exc:
        await fill(page, Action(field=field, value="+16502532222", source="facts:phone"))
    assert exc.value.code == Code.FIELD_FILL_FAILED
    assert await page.evaluate("window.attempts") == 3


async def test_country_change_replans_before_phone_and_remaining_fields(page, profile):
    await page.set_content(r"""<form>
<label>First name<input required id=first oninput="window.events.push('info')"></label>
<label>Phone<input type=tel required id=phone pattern="\+1[0-9]{10}" oninput="window.events.push('phone')"></label>
<label>Country<select onchange="countryChanged()"><option value=other>Other</option><option value=ca>Canada</option></select></label>
<div><label>Resume<input type=file required onchange="window.events.push('resume')"></label></div>
</form><script>
window.events=[];
function countryChanged() {
 window.events.push('country');
 setTimeout(()=>{
  document.querySelector('#phone').pattern='[0-9]{10}';
  document.querySelector('#phone').value='';
  if(!document.querySelector('#last'))document.querySelector('form').insertAdjacentHTML('beforeend','<label>Last name<input id=last required></label>');
 },100);
}
</script>""")
    profile.facts["country"] = "Canada"
    profile.facts["phone"] = "+16502532222"
    profile.address.country = "Canada"
    previous = []
    for _ in range(5):
        snap = await prepared_snapshot(page)
        actions, unresolved = await WorkflowAgent().plan(snap.fields, profile)
        assert not unresolved
        execution = await execute_actions(
            page, actions, previous_actions=previous, assessed_fields=snap.fields
        )
        previous = [row.action for row in execution.fields]
        if not execution.needs_replan:
            break
    assert execution.retained
    events = await page.evaluate("window.events")
    assert events == ["resume", "country", "phone", "info"]
    assert await page.locator("#last").input_value() == profile.values()["last_name"]
    assert await page.locator("#phone").input_value() == "6502532222"


def duplicate_picker(selected=False, same_value=True):
    return f"""<div><div role=combobox tabindex=0 aria-label="Calling code" aria-controls=choices aria-expanded=false
      onclick="const open=this.getAttribute('aria-expanded')!=='true';this.setAttribute('aria-expanded',String(open));document.querySelector('#choices').hidden=!open"
      onkeydown="if(event.key==='Escape'){{this.setAttribute('aria-expanded','false');document.querySelector('#choices').hidden=true}}">+1</div>
    <div role=listbox id=choices hidden>
      <div role=option data-country-code=ca data-dial-code=1 aria-selected="{str(selected).lower()}" onclick="choose(this)">Canada +1</div>
      <div role=option data-country-code="{"ca" if same_value else "us"}" data-dial-code=1 aria-selected=false onclick="choose(this)">Canada +1</div>
    </div><label>Phone<input type=tel></label></div>
    <script>window.choices=0;function choose(e){{window.choices++;e.setAttribute('aria-selected','true');e.parentElement.hidden=true;document.querySelector('[role=combobox]').setAttribute('aria-expanded','false')}}</script>"""


async def test_selected_country_is_recognized_without_reselecting_or_opening(page):
    await page.set_content(duplicate_picker(selected=True))
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value="Canada +1", source="agent_fill:phone+country")
    assert (await verify_action_results(page, [action]))[0].valid
    result = await execute_actions(page, [action], assessed_fields=[field])
    assert result.retained
    assert await page.evaluate("window.choices") == 0
    assert await locator(page, field).get_attribute("aria-expanded") == "false"


async def test_equivalent_duplicate_countries_can_be_selected(page):
    await page.set_content(duplicate_picker())
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value="Canada +1", source="agent_fill:phone+country")
    await fill(page, action)
    assert (await verify_action_results(page, [action]))[0].valid
    assert await page.evaluate("window.choices") == 1


async def test_distinct_duplicate_values_remain_ambiguous_and_popup_closes(page):
    await page.set_content(duplicate_picker(same_value=False))
    field = (await snapshot(page)).fields[0]
    with pytest.raises(ApplicationError) as exc:
        await fill(page, Action(field=field, value="Canada +1", source="agent_fill:phone+country"))
    assert exc.value.code == Code.UNSUPPORTED_CONTROL
    assert await locator(page, field).get_attribute("aria-expanded") == "false"
    assert await page.evaluate("window.choices") == 0
    assert await discover_options(page, field)


async def test_option_discovery_does_not_toggle_an_already_open_popup_closed(page):
    await page.set_content(duplicate_picker())
    field = (await snapshot(page)).fields[0]
    await locator(page, field).click()
    assert await discover_options(page, field)
    assert await locator(page, field).get_attribute("aria-expanded") == "false"


async def test_phone_does_not_verify_against_a_wrong_dial_code(page):
    await page.set_content("""<div><select autocomplete=tel-country-code><option value=GB data-dial-code=44>United Kingdom</option></select>
      <label>Phone<input type=tel value=6502532222></label></div>""")
    field = next(f for f in (await snapshot(page)).fields if f.kind == "tel")
    action = Action(field=field, value="+16502532222", source="facts:phone")
    assert not (await verify_action_results(page, [action]))[0].valid


async def test_model_mapped_text_phone_uses_same_format_handler(page, profile):
    await page.set_content(
        '<label>Reach me<input type=text required maxlength=10 pattern="[0-9]{10}"></label>'
    )
    profile.facts["phone"] = "+16502532222"

    class Mapper:
        async def map(self, fields, fact_keys):
            return [
                Mapping(
                    field_id=f"{fields[0].frame}:{fields[0].id}", fact_key="phone", confidence=1
                )
            ]

    fields = (await snapshot(page)).fields
    actions, unresolved = await WorkflowAgent(Mapper()).plan(fields, profile)
    assert not unresolved and len(actions) == 1
    result = await execute_actions(page, actions, assessed_fields=fields)
    assert result.retained
    assert await page.locator("input").input_value() == "6502532222"


async def test_phone_verification_does_not_borrow_unrelated_form_error(page):
    await page.set_content(
        "<form><input type=tel aria-label=Phone value=6502532222><div role=alert>Resume missing</div></form>"
    )
    field = (await snapshot(page)).fields[0]
    assert (
        await verify_action_results(
            page, [Action(field=field, value="+16502532222", source="facts:phone")]
        )
    )[0].valid


async def test_phone_with_native_iso_country_option_uses_the_displayed_dial_code(page):
    await page.set_content(
        "<div><select autocomplete=tel-country-code><option value=CA>Canada +1</option></select><label>Phone<input type=tel required></label></div>"
    )
    field = next(f for f in (await snapshot(page)).fields if f.kind == "tel")
    action = Action(field=field, value="+16502532222", source="facts:phone")
    await fill(page, action)
    assert await page.locator("input").input_value() == "6502532222"
    assert (await verify_action_results(page, [action]))[0].valid
