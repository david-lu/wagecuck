import importlib.util
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from wagecuck.browser import snapshot
from wagecuck.execution import ExecutionResult, execute_actions, field_id, reusable_answer
from wagecuck.models import Action, FormField, Option, Snapshot
from wagecuck.reporting import execution_report


def script(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(service_workers="block")
        await context.route_web_socket("**/*", lambda socket: socket.close())
        page = await context.new_page()
        page.set_default_timeout(1000)
        yield page
        await browser.close()


async def test_probe_reports_late_mutation_as_field_failure(page, profile):
    await page.set_content("""<form>
      <label>First Name<input id="first" required></label>
      <label>Email<input type="email" required onblur="setTimeout(()=>document.querySelector('#first').value='changed',100)"></label>
      <button>Submit</button></form>""")
    report = await script("probe_live").probe_fields(page, profile)
    assert not report["required_fill_pass"]
    assert not report["completed_values_retained"]
    assert report["required_fill_failures"] == ["First Name"]
    first = next(row for row in report["control_outcomes"] if row["label"] == "First Name")
    assert first["code"] == "VALIDATION_FAILED" and not first["verified"]


async def test_probe_replans_new_required_control_without_clicking_next(page, profile):
    await page.set_content("""<form>
      <label>First Name<input onblur="if(!document.querySelector('#extra'))setTimeout(()=>{const e=document.createElement('label');e.innerHTML='Unknown clearance<input id=extra required>';document.querySelector('form').append(e)},100)" required></label>
      <button type="button" onclick="window.nextClicked=true">Continue</button>
      </form>""")
    report = await script("probe_live").probe_fields(page, profile)
    assert not report["required_fill_pass"]
    assert report["required_answers_missing"] == ["Unknown clearance"]
    assert report["field_count"] == 2
    assert not await page.evaluate("!!window.nextClicked")
    assert not await page.evaluate("navigator.onLine")


async def test_shared_executor_picks_empty_random_combobox(page, profile):
    await page.set_content("""<form>
      <label>Where did you find this job?<input name="source" role="combobox"
        aria-controls="choices" onclick="document.querySelector('#choices').hidden=false"></label>
      <div id="choices" role="listbox" hidden><div role="option"
        onclick="document.querySelector('[name=source]').value='Meetup';this.parentElement.hidden=true">Meetup</div></div>
      </form>""")
    report = await script("probe_live").probe_fields(page, profile)
    assert report["filled_count"] == 1 and report["completed_values_retained"]
    assert await page.locator('[name="source"]').input_value() == "Meetup"


@pytest.mark.parametrize("kind", ["text", "select"])
async def test_generated_answers_survive_three_dom_replacements(page, kind):
    control = (
        '<input name="answer">'
        if kind == "text"
        else '<select name="answer"><option value=""></option><option value="original">Original</option><option value="replacement">Replacement</option></select>'
    )
    await page.set_content(f"<form><label>Answer{control}</label></form>")
    history = {}
    for attempt in range(4):
        if attempt:
            await page.locator('[name="answer"]').evaluate("""element => {
              const replacement = element.cloneNode(true);
              delete replacement.dataset.wagecuckId;
              replacement.value = '';
              element.replaceWith(replacement);
            }""")
        current = await snapshot(page)
        proposed = Action(
            field=current.fields[0],
            value="original" if not attempt else "replacement",
            source="agent_fill:made_up",
            answer_basis="made_up",
            made_up=True,
        )
        result = await execute_actions(
            page,
            [proposed],
            previous_actions=list(history.values()),
            assessed_fields=current.fields,
        )
        assert result.retained
        assert await page.locator('[name="answer"]').input_value() == "original"
        history[field_id(proposed.field)] = proposed
    assert len(history) == 4


async def test_generated_choice_reuse_keeps_identical_questions_separate(page):
    await page.set_content("""<form>
      <fieldset><legend>Preferences</legend><label>Enable<input name="choice" type="checkbox"></label></fieldset>
      <fieldset><legend>Preferences</legend><label>Enable<input name="choice" type="checkbox"></label></fieldset>
      </form>""")
    initial = await snapshot(page)
    assert initial.fields[0].group_id != initial.fields[1].group_id
    previous = [
        Action(
            field=field,
            value=index == 0,
            source="agent_fill:made_up",
            answer_basis="made_up",
            made_up=True,
        )
        for index, field in enumerate(initial.fields)
    ]
    await execute_actions(page, previous, assessed_fields=initial.fields)
    await page.locator("input").evaluate_all("""elements => elements.forEach(element => {
      const replacement = element.cloneNode(); delete replacement.dataset.wagecuckId;
      replacement.checked = false; element.replaceWith(replacement);
    })""")
    current = await snapshot(page)
    proposed = [
        Action(
            field=field,
            value=index == 1,
            source="agent_fill:made_up",
            answer_basis="made_up",
            made_up=True,
        )
        for index, field in enumerate(current.fields)
    ]
    result = await execute_actions(
        page, proposed, previous_actions=previous, assessed_fields=current.fields
    )
    assert result.retained
    assert await page.locator("input").evaluate_all(
        "elements => elements.map(element => element.checked)"
    ) == [True, False]


def test_answer_reuse_respects_new_constraints_options_and_profile_grounding():
    field = FormField(
        id="answer", frame=0, label="Answer", kind="select", options=[Option(label="A", value="a")]
    )
    previous = Action(
        field=field, value="a", source="agent_fill:made_up", answer_basis="made_up", made_up=True
    )
    changed = field.model_copy(update={"options": [Option(label="B", value="b")]})
    proposed = previous.model_copy(update={"field": changed, "value": "b"})
    assert not reusable_answer(previous, proposed)
    grounded = previous.model_copy(
        update={"source": "facts:source", "answer_basis": "profile", "made_up": False}
    )
    assert not reusable_answer(previous, grounded)
    constrained = FormField(id="answer", frame=0, label="Answer", kind="number", minimum="20")
    previous = previous.model_copy(update={"field": constrained, "value": "10"})
    assert not reusable_answer(previous, previous.model_copy(update={"value": "30"}))


@pytest.mark.parametrize("change", ["options", "profile"])
async def test_new_valid_or_profile_answer_supersedes_generated_history(page, change):
    await page.set_content(
        '<label>Answer<select><option value="a">A</option><option value="b">B</option></select></label>'
    )
    initial = await snapshot(page)
    previous = Action(
        field=initial.fields[0],
        value="a",
        source="agent_fill:made_up",
        answer_basis="made_up",
        made_up=True,
    )
    await execute_actions(page, [previous], assessed_fields=initial.fields)
    if change == "options":
        await page.locator("option[value=a]").evaluate("element => element.remove()")
    current = await snapshot(page)
    proposed = Action(
        field=current.fields[0],
        value="b",
        source="facts:source" if change == "profile" else "agent_fill:made_up",
        answer_basis="profile" if change == "profile" else "made_up",
        made_up=change != "profile",
    )
    result = await execute_actions(
        page, [proposed], previous_actions=[previous], assessed_fields=current.fields
    )
    assert result.retained
    assert await page.locator("select").input_value() == "b"
    assert proposed.answer_basis == ("profile" if change == "profile" else "made_up")


@pytest.mark.parametrize("change", ["random", "profile", "removed"])
async def test_radio_replan_retains_one_effective_group_selection(page, change):
    await page.set_content("""<fieldset><legend>Source</legend>
      <label>Alpha<input type=radio name=source value=alpha></label>
      <label>Beta<input type=radio name=source value=beta></label></fieldset>""")
    initial = await snapshot(page)
    previous = Action(
        field=initial.fields[0],
        value=True,
        source="random:source",
        answer_basis="made_up",
        made_up=True,
    )
    await execute_actions(page, [previous], assessed_fields=initial.fields)
    if change == "removed":
        await page.locator("label").first.evaluate("element => element.remove()")
    current = await snapshot(page)
    beta = next(field for field in current.fields if field.label == "Beta")
    proposed = Action(
        field=beta,
        value=True,
        source="facts:source" if change == "profile" else "random:source",
        answer_basis="profile" if change == "profile" else "made_up",
        made_up=change != "profile",
    )
    result = await execute_actions(
        page,
        [proposed],
        previous_actions=[previous],
        assessed_fields=current.fields,
    )
    assert result.retained
    assert len(result.fields) == 1
    assert await page.locator("input:checked").input_value() == (
        "alpha" if change == "random" else "beta"
    )
    assert proposed.field.label == ("Alpha" if change == "random" else "Beta")
    assert result.fields[0].action.source == (
        "facts:source" if change == "profile" else "random:source"
    )


def test_page_validation_errors_are_counted_and_classified_without_raw_text():
    field = FormField(id="email", frame=0, label="Email", kind="email", required=True, filled=True)
    current = Snapshot(
        url="https://example.com", title="", fields=[field], errors=["Private validation detail"]
    )
    report = execution_report(
        ExecutionResult(current, []),
        [{"field_id": "0:email", "route": "already_filled_or_group_option", "source": None}],
    )
    assert report["required_question_satisfied_count"] == 1
    assert not report["required_fill_pass"]
    assert report["page_validation_error_count"] == 1
    assert "Private validation detail" not in json.dumps(report)
    assert script("probe_live").probe_code(report) == "VALIDATION_FAILED"


def test_summary_does_not_recalculate_or_modify_recorded_results(tmp_path):
    report = {
        "checked_at": "2026-01-01",
        "results": [
            {
                "id": "mutation",
                "url": "https://example.com",
                "code": "VALIDATION_FAILED",
                "field_count": 1,
                "mapped_count": 1,
                "filled_count": 0,
                "required_fill_pass": False,
                "completed_values_retained": False,
                "fields": [{"kind": "text", "required": True, "code": "FILLED"}],
            }
        ],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    before = path.read_bytes()
    script("dry_run_all").summarize(path)
    assert path.read_bytes() == before
    assert "| no |" in path.with_suffix(".md").read_text(encoding="utf-8")
