from datetime import date

import pytest
from playwright.async_api import async_playwright

from wagecuck.agent import WorkflowAgent
from wagecuck.browser import fill, snapshot, verify_actions
from wagecuck.field_values import FieldValueError, normalize_field_value, render_field_value
from wagecuck.inference import FieldAnswer, apply_inferred_answers
from wagecuck.models import FormField


def date_field(placeholder, **kwargs):
    return FormField(
        id="date", frame=0, kind="text", label="Date Available", placeholder=placeholder, **kwargs
    )


@pytest.mark.parametrize(
    "placeholder,display",
    [
        ("DD/MM/YYYY", "01/10/2026"),
        ("MM/DD/YYYY", "10/01/2026"),
        ("YYYY/MM/DD", "2026/10/01"),
        ("DD.MM.YYYY", "01.10.2026"),
        ("DD-MM-YY", "01-10-26"),
    ],
)
def test_date_normalization_is_canonical_idempotent_and_placeholder_aware(placeholder, display):
    field = date_field(placeholder)
    canonical = normalize_field_value(field, display)
    assert canonical == "2026-10-01"
    assert normalize_field_value(field, canonical) == canonical
    assert render_field_value(field, canonical) == display
    assert normalize_field_value(field, render_field_value(field, canonical)) == canonical


def test_date_constraints_apply_to_rendered_text_and_native_date_always_uses_iso():
    field = date_field(
        "DD/MM/YYYY",
        pattern=r"[0-9]{2}/[0-9]{2}/[0-9]{4}",
        min_length=10,
        max_length=10,
        minimum="2026-10-01",
        maximum="2026-10-31",
    )
    assert normalize_field_value(field, "01/10/2026") == "2026-10-01"
    with pytest.raises(FieldValueError):
        normalize_field_value(field, "12/31/2026")
    field.kind = "date"
    field.pattern = ""
    assert render_field_value(field, "2026-10-01") == "2026-10-01"


@pytest.mark.parametrize(
    "raw,canonical", [("1,000.00", "1000"), ("1.50", "1.5"), ("0.00", "0"), ("-0.00", "0")]
)
def test_numbers_have_a_stable_canonical_form(raw, canonical):
    field = FormField(id="salary", frame=0, label="Salary", kind="number", step="any")
    assert normalize_field_value(field, raw) == canonical
    assert normalize_field_value(field, canonical) == canonical


@pytest.mark.parametrize("route", ["profile", "inference"])
async def test_ambiguous_dmy_date_survives_planning_fill_and_final_verification(profile, route):
    profile.application.available_start_date = date(2026, 10, 1)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        await page.set_content(
            '<label>Date Available<input placeholder="DD/MM/YYYY" required></label>'
        )
        fields = (await snapshot(page)).fields
        if route == "profile":
            actions, unresolved = await WorkflowAgent().plan(fields, profile)
            assert not unresolved
        else:
            actions, resolved, warnings = apply_inferred_answers(
                fields,
                [
                    FieldAnswer(
                        field_id=f"0:{fields[0].id}",
                        value="01/10/2026",
                        basis="made_up",
                        fact_keys=[],
                        reason="A sample availability date.",
                    )
                ],
                {},
            )
            assert resolved and not warnings
        assert len(actions) == 1 and actions[0].value == "2026-10-01"
        await fill(page, actions[0])
        await fill(page, actions[0])
        await verify_actions(page, actions)
        assert actions[0].value == "2026-10-01"
        assert await page.locator("input").input_value() == "01/10/2026"
        await browser.close()
