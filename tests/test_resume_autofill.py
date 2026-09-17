import importlib.util
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from wagecuck import ApplicationRunner
from wagecuck.agent import RequirementAssessment, WorkflowAgent
from wagecuck.browser import snapshot, verify_action_results
from wagecuck.controls import upload
from wagecuck.execution import execute_actions
from wagecuck.fixtures import Handler
from wagecuck.models import Action, Code


@pytest.fixture
async def page(monkeypatch):
    monkeypatch.setattr(upload, "UPLOAD_MIN_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(upload, "UPLOAD_QUIET_SECONDS", 0.2)
    monkeypatch.setattr(upload, "UPLOAD_TIMEOUT_SECONDS", 2.5)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        page.set_default_timeout(700)
        yield page
        await browser.close()


def probe_module():
    spec = importlib.util.spec_from_file_location(
        "probe_upload", Path(__file__).parents[1] / "scripts/probe_live.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUTOFILL_FORM = """<form>
<label>First name<input id="first" required value="wrong before upload"
  oninput="window.earlyWrite = !window.parsed"></label>
<label>Email<input type="email" id="email" required value="wrong@example.com"></label>
<div><label>Resume<input type="file" required id="resume" onchange="autofill(this)"></label>
<span role="status" id="status"></span></div>
<button type="submit" onclick="window.submitted=true;return false">Submit application</button>
</form><script>
window.uploads=0;
function autofill(input) {
  window.uploads++;
  document.querySelector('#status').textContent='Processing resume';
  setTimeout(() => {
    document.querySelector('#first').value='wrong parsed name';
    document.querySelector('#email').outerHTML='<input type=email id=email required value=parsed@example.com>';
    document.querySelector('#status').textContent='';
    window.parsed=true;
  }, 800);
}
</script>"""


async def test_upload_precedes_manual_writes_and_corrects_delayed_autofill(page, profile):
    await page.set_content(AUTOFILL_FORM)
    module = probe_module()
    report = await module.probe_fields(page, profile)
    assert report["required_fill_pass"], report
    assert await page.locator("#first").input_value() == profile.values()["first_name"]
    assert await page.locator("#email").input_value() == profile.values()["email"]
    assert await page.evaluate("window.uploads") == 1
    assert not await page.evaluate("window.earlyWrite || window.submitted || false")


async def test_upload_checkpoint_preserves_agent_requirements_and_defers_text(page, profile):
    await page.set_content(
        "<label>First name<input required></label><label>Resume<input type=file></label>"
    )
    fields = (await snapshot(page)).fields
    fields[1].required = True
    fields[1].requirement_status = "required"
    fields[1].required_evidence = "agent: Resume is required"
    actions, _ = await WorkflowAgent().plan(fields, profile)
    result = await execute_actions(page, actions, assessed_fields=fields)
    assert result.needs_replan
    text = next(row for row in result.fields if row.action.field.kind == "text")
    assert text.code == Code.DEFERRED
    assert "not attempted" in text.message
    assert await page.locator("input:not([type=file])").input_value() == ""
    assert result.snapshot.fields[1].required
    result2 = await execute_actions(
        page, actions, previous_actions=actions, assessed_fields=result.snapshot.fields
    )
    assert result2.retained and not result2.needs_replan


async def test_replacement_file_with_attachment_does_not_upload_again(page, profile):
    await page.set_content("""<form><label>First name<input required></label>
<div class="field"><label>Resume<input type=file id=resume required onchange="attach(this)"></label>
<span id=filename></span><button type=button id=remove hidden>Remove</button></div></form>
<script>window.uploads=0; function attach(e) { window.uploads++;
 document.querySelector('#filename').textContent=e.files[0].name;
 document.querySelector('#remove').hidden=false;
 e.outerHTML='<input type=file id=resume required onchange="attach(this)">';
}</script>""")
    report = await probe_module().probe_fields(page, profile)
    assert report["required_fill_pass"], report
    assert await page.evaluate("window.uploads") == 1
    assert await page.locator("#resume").evaluate("e=>e.files.length") == 0


async def test_attachment_from_other_widget_or_wrong_file_does_not_verify(page, profile):
    await page.set_content(f"""<form>
<div><label>Resume<input type=file required></label></div>
<div><span>{profile.resume.name}</span><button type=button>Remove</button></div>
</form>""")
    field = (await snapshot(page)).fields[0]
    action = Action(field=field, value=str(profile.resume), source="document:resume")
    assert not (await verify_action_results(page, [action]))[0].valid


async def test_offline_upload_failure_is_explicit_and_other_fields_still_fill(page, profile):
    await page.set_content("""<form><label>First name<input required></label>
<div><label>Resume<input type=file required onchange="fetch('https://example.test/upload',
 {method:'POST', body:this.files[0]}).catch(()=>{
 this.value='';document.querySelector('#error').textContent='Upload failed'})"></label>
<span id=error role=alert></span></div></form>""")
    module = probe_module()
    report = await module.probe_fields(page, profile)
    assert module.probe_code(report) == "UPLOAD_UNVERIFIED", report
    assert (
        await page.locator("input:not([type=file])").input_value() == profile.values()["first_name"]
    )
    assert not report["required_fill_pass"]
    assert not await page.evaluate("navigator.onLine")


async def test_processing_timeout_is_explicit_and_bounded(page, profile, monkeypatch):
    monkeypatch.setattr(upload, "UPLOAD_TIMEOUT_SECONDS", 0.5)
    await page.set_content("""<div><label>Resume<input type=file required
 onchange="document.querySelector('#busy').textContent='Processing resume'"></label>
<span id=busy role=status></span></div>""")
    report = await probe_module().probe_fields(page, profile)
    assert probe_module().probe_code(report) == "UPLOAD_TIMEOUT", report
    assert not report["required_fill_pass"]


async def test_normal_runner_replans_after_resume_autofill(portal, profile, options, monkeypatch):
    base, server = portal
    previous = Handler.do_GET

    def get(handler):
        if handler.path == "/autofill":
            return handler.respond(AUTOFILL_FORM)
        return previous(handler)

    monkeypatch.setattr(Handler, "do_GET", get)
    result = await ApplicationRunner().run(
        base + "/autofill", profile, options.model_copy(update={"mode": "fill"})
    )
    assert result.code == Code.READY, result.model_dump()
    assert 2 <= result.steps <= 3
    assert not server.submissions
    assert any(row["status"] == "deferred" for row in result.answer_log)
    assert not any(row["status"] == "failed" for row in result.answer_log)
    final = json.loads(
        (options.artifacts_dir / result.run_id / f"execution-{result.steps:02d}.json").read_text()
    )
    assert final["required_fill_pass"]


async def test_optional_resume_autofill_is_skipped_but_attachment_is_filled(page, profile):
    await page.set_content("""<form>
<label>Autofill with resume<input type=file onchange="window.helperUsed=true"></label>
<label>First name<input required></label>
<label>Resume<input type=file required></label></form>""")
    report = await probe_module().probe_fields(page, profile)
    assert report["required_fill_pass"], report
    assert not await page.evaluate("window.helperUsed || false")
    assert await page.locator("input[type=file]").first.evaluate("e=>e.files.length") == 0
    assert await page.locator("input[type=file]").last.evaluate("e=>e.files.length") == 1


async def test_upload_waits_for_network_autofill_without_a_busy_indicator(page, profile):
    import asyncio

    async def respond(route):
        await asyncio.sleep(0.8)
        await route.fulfill(json={"parsed": True}, headers={"access-control-allow-origin": "*"})

    await page.route("https://example.test/parse", respond)
    await page.set_content("""<form><label>First name<input id=first required></label>
<div><label>Resume<input type=file required onchange="
 fetch('https://example.test/parse').then(r=>r.json()).then(()=>{
 document.querySelector('#first').value='wrong parsed value';window.parsed=true})"></label></div>
</form>""")
    fields = (await snapshot(page)).fields
    actions, _ = await WorkflowAgent().plan(fields, profile)
    first = await execute_actions(page, actions, assessed_fields=fields)
    assert first.needs_replan
    assert await page.evaluate("window.parsed === true")
    assert await page.locator("#first").input_value() == "wrong parsed value"
    fields = first.snapshot.fields
    actions2, _ = await WorkflowAgent().plan(fields, profile)
    second = await execute_actions(page, actions2, previous_actions=actions, assessed_fields=fields)
    assert second.retained
    assert await page.locator("#first").input_value() == profile.values()["first_name"]


async def test_disappearing_upload_does_not_erase_failure_from_report(page, profile):
    await page.set_content("""<form><label>First name<input required></label>
<div><label>Resume<input type=file required onchange="this.parentElement.remove()"></label></div>
</form>""")
    module = probe_module()
    report = await module.probe_fields(page, profile)
    assert report["upload_error_codes"] == ["UPLOAD_UNVERIFIED"]
    assert module.probe_code(report) == "UPLOAD_UNVERIFIED"
    assert not report["required_fill_pass"]


async def test_upload_instructions_and_completed_progress_are_not_busy(page, profile):
    await page.set_content("""<form><label>First name<input required></label>
<div><label>Resume<input type=file required></label>
<p>Your resume is used for processing this application.</p>
<div role=progressbar aria-valuenow=100 aria-valuemax=100></div>
<span>Processing complete</span></div></form>""")
    report = await probe_module().probe_fields(page, profile)
    assert report["required_fill_pass"], report


async def test_resume_import_marked_required_by_model_is_not_skipped(page, profile):
    await page.set_content("<label>Autofill with resume<input type=file></label>")
    fields = (await snapshot(page)).fields
    fields[0].context = "You must provide a resume."

    class Assessor:
        async def assess(self, fields):
            return [
                RequirementAssessment(
                    field_id=f"{fields[0].frame}:{fields[0].id}",
                    required=True,
                    evidence="You must provide a resume.",
                    confidence=1.0,
                )
            ]

    actions, unresolved = await WorkflowAgent(Assessor()).plan(fields, profile)
    assert not unresolved
    assert len(actions) == 1
    assert actions[0].field.required
    assert actions[0].value == str(profile.resume)
