import pytest
from playwright.async_api import async_playwright

from wagecuck.browser import fill, snapshot
from wagecuck.field_semantics import fields_metadata
from wagecuck.field_values import FieldValueError, normalize_field_value, value_contract
from wagecuck.logical_fields import logical_field_results
from wagecuck.models import Action, FormField


def field(id, label, kind="text", **kwargs):
    return FormField(id=id, frame=0, label=label, kind=kind, **kwargs)


def test_typed_values_respect_dates_numbers_ranges_and_lengths():
    available = field(
        "date",
        "Date Available",
        placeholder="mm/dd/yyyy",
        minimum="2026-09-01",
        maximum="2026-12-31",
    )
    salary = field("salary", "Desired Salary", minimum="50000", maximum="200000", step="1000")
    code = field("code", "Employee code", pattern=r"[A-Z]{2}\d{3}", min_length=5, max_length=5)
    assert value_contract(available) == "date"
    assert normalize_field_value(available, "2026-10-01") == "2026-10-01"
    assert value_contract(salary) == "number"
    assert normalize_field_value(salary, "100000") == "100000"
    with pytest.raises(FieldValueError):
        normalize_field_value(salary, "CAD 100000 per year")
    with pytest.raises(FieldValueError):
        normalize_field_value(salary, "100500")
    assert normalize_field_value(code, "AB123") == "AB123"
    with pytest.raises(FieldValueError):
        normalize_field_value(code, "ABC123")
    with pytest.raises(FieldValueError):
        normalize_field_value(available, "2027-01-01")


def test_native_calendar_and_clock_types_use_html_value_formats():
    assert (
        normalize_field_value(field("dt", "Interview", "datetime-local"), "2026-10-01T09:30")
        == "2026-10-01T09:30"
    )
    assert (
        normalize_field_value(field("month", "Graduation month", "month"), "2026-06") == "2026-06"
    )
    assert normalize_field_value(field("week", "Start week", "week"), "2026-W40") == "2026-W40"
    assert normalize_field_value(field("time", "Interview time", "time"), "09:30") == "09:30"
    with pytest.raises(FieldValueError):
        normalize_field_value(field("week", "Start week", "week"), "2026-W99")
    with pytest.raises(FieldValueError):
        normalize_field_value(field("time", "Interview time", "time"), "09:30+01:00")


async def test_parser_captures_native_value_constraints_and_fill_formats_date():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        await page.set_content(
            """<form>
            <label>Date Available<input name="available" placeholder="mm/dd/yyyy" minlength="10" maxlength="10"></label>
            <label>Score<input name="score" type="number" min="1" max="10" step="1" inputmode="decimal"></label>
            <label>Code<input name="code" pattern="[A-Z]{2}[0-9]{3}"></label>
            </form>"""
        )
        fields = (await snapshot(page)).fields
        available, score, code = fields
        assert available.placeholder == "mm/dd/yyyy" and available.min_length == 10
        assert available.max_length == 10
        assert score.minimum == "1" and score.maximum == "10" and score.step == "1"
        assert score.input_mode == "decimal"
        assert code.pattern == "[A-Z]{2}[0-9]{3}"
        action = Action(field=available, value="2026-10-01", source="agent_fill:made_up")
        await fill(page, action)
        assert action.value == "2026-10-01"
        assert await page.locator('[name="available"]').input_value() == "10/01/2026"
        await browser.close()


def test_radio_and_checkbox_controls_report_as_logical_questions():
    fields = [
        field("yes", "Yes", "radio", name="authorized", group="Authorized to work?", required=True),
        field("no", "No", "radio", name="authorized", group="Authorized to work?", required=True),
        field("python", "Python", "checkbox", name="skills[]", group="Skills"),
        field("go", "Go", "checkbox", name="skills[]", group="Skills"),
    ]
    outcomes = [
        {
            "field_id": "0:yes",
            "source": "facts:authorized_us",
            "answer_basis": "profile",
            "made_up": False,
            "selected": True,
            "code": "FILLED",
        },
        {
            "field_id": "0:python",
            "source": "agent_fill:skills",
            "answer_basis": "inferred",
            "made_up": False,
            "selected": True,
            "code": "FILLED",
        },
    ]
    analysis = [
        {
            "field_id": f"0:{item.id}",
            "route": "agent_fill" if item.id == "python" else "deterministic",
            "source": "present" if item.id in ("yes", "python") else None,
        }
        for item in fields
    ]
    rows = logical_field_results(fields, outcomes, analysis)
    assert len(rows) == 2
    assert rows[0]["question"] == "Authorized to work?"
    assert rows[0]["kind"] == "radio_group"
    assert rows[0]["route"] == "deterministic"
    assert [option["label"] for option in rows[0]["options"]] == ["Yes", "No"]
    assert [option["selected"] for option in rows[0]["options"]] == [True, False]
    assert rows[1]["question"] == "Skills" and rows[1]["kind"] == "checkbox_group"
    assert rows[1]["route"] == "agent_fill"


async def test_parser_groups_checkbox_options_by_shared_question_heading():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        await page.set_content(
            """<form><div class="application-question">
            <h3>Race or ethnicity *</h3>
            <label>Asian<input type="checkbox" name="race_asian"></label>
            <label>White<input type="checkbox" name="race_white"></label>
            </div></form>"""
        )
        fields = (await snapshot(page)).fields
        await browser.close()
    assert [field.group for field in fields] == ["Race or ethnicity *"] * 2
    assert all(field.required for field in fields)
    rows = logical_field_results(
        fields,
        [],
        [
            {"field_id": f"0:{field.id}", "route": "unresolved", "source": None}
            for field in fields
        ],
    )
    assert len(rows) == 1
    assert rows[0]["question"] == "Race or ethnicity *"
    assert [option["label"] for option in rows[0]["options"]] == ["Asian", "White"]


def test_prefilled_control_is_reported_as_satisfied_without_an_action():
    fields = [field("period", "Pay period", "select", required=True, filled=True)]
    analysis = [
        {
            "field_id": "0:period",
            "route": "already_filled_or_group_option",
            "source": None,
        }
    ]
    rows = logical_field_results(fields, [], analysis)
    assert rows[0]["code"] == "ALREADY_FILLED"


def test_model_metadata_pairs_choice_controls_and_exposes_value_contract():
    fields = [
        field("yes", "Question text yes", "radio", name="q", group="3.Questions"),
        field("no", "Question text no", "radio", name="q", group="3.Questions"),
        field("date", "Date Available", placeholder="mm/dd/yyyy"),
    ]
    metadata = fields_metadata(fields)
    assert metadata[0]["logical_question"] == "Question text"
    assert [option["label"] for option in metadata[0]["group_options"]] == ["yes", "no"]
    assert metadata[2]["value_contract"]["type"] == "date"
    assert metadata[2]["value_contract"]["format"] == "MDY"
