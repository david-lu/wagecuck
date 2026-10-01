import json

import httpx
import pytest
from playwright.async_api import async_playwright
from pydantic import ValidationError

from wagecuck import Address, Education, Employment, Profile
from wagecuck.agent import Mapping, OllamaMappingAgent, WorkflowAgent
from wagecuck.browser import fill, snapshot, verify_actions
from wagecuck.models import FormField


def test_structured_history_resolves_values_and_current_job(profile):
    values = profile.values()
    assert profile.employment[0].company == "Example Systems (fictional)"
    assert values["employment.0.start_date"] == "2023-06-01"
    assert values["employment.0.current"] is True
    assert "employment.0.end_date" not in values
    assert values["education.0.degree"] == "Bachelor of Science"
    assert values["education.0.end_date"] == "2023-05-31"
    assert values["current_company"] == profile.get_current_employment().company
    assert all(isinstance(value, (str, bool)) for value in values.values())
    profile.employment[0].current = False
    assert profile.get_current_employment() is None
    assert "current_company" not in profile.values()


def test_multiple_current_jobs_do_not_choose_arbitrarily(profile):
    profile.employment.append(
        Employment(company="Second company", title="Consultant", current=True)
    )
    assert profile.get_current_employment() is None
    assert "current_company" not in profile.values()
    assert profile.values()["employment.1.title"] == "Consultant"


def test_address_helpers_and_composites(profile):
    profile.address.line2 = "Suite 12"
    assert profile.get_address() == "100 Example Street, Suite 12, Toronto, Ontario M5V 2T6, Canada"
    assert (
        profile.get_address(multiline=True, include_country=False)
        == "100 Example Street\nSuite 12\nToronto, Ontario M5V 2T6"
    )
    assert profile.get_street_address() == "100 Example Street, Suite 12"
    values = profile.values()
    assert values["address.line2"] == values["address_line2"] == "Suite 12"
    assert values["address"] == values["address_line1"] == "100 Example Street"
    assert values["full_address"] == profile.get_address()
    assert values["full_address_multiline"] == profile.get_address(multiline=True)
    assert values["location"] == "Toronto, Ontario, Canada"
    assert values["full_name"] == profile.get_full_name() == "Alex Morgan"


def test_partial_and_empty_addresses_do_not_invent_components(profile):
    profile.address = Address(city=" London ", country=" United Kingdom ")
    assert profile.get_address() == "London, United Kingdom"
    assert "street_address" not in profile.values()
    profile.address = Address()
    assert profile.get_address() == ""
    assert "full_address" not in profile.values()


def test_legacy_flat_profile_compatibility_and_structured_precedence(profile):
    data = profile.model_dump(mode="json")
    data.pop("address")
    data.pop("employment")
    data.pop("education")
    data["facts"].update(
        address="1 Old Street",
        city="Ottawa",
        country="Canada",
        **{"employment.0.title": "Former title", "education.0.school": "Old school"},
    )
    legacy = Profile.model_validate(data)
    assert legacy.get_address() == "1 Old Street, Ottawa, Canada"
    assert legacy.values()["employment.0.title"] == "Former title"
    legacy.address = Address(line1="2 New Street", city="Toronto")
    legacy.employment = [Employment(company="New company", title="New title")]
    assert legacy.get_address() == "2 New Street, Toronto"
    assert "country" not in legacy.values()
    assert legacy.values()["employment.0.title"] == "New title"


@pytest.mark.parametrize(
    "model,kwargs",
    [
        (
            Employment,
            {"company": "A", "title": "B", "start_date": "2024-02-01", "end_date": "2023-01-01"},
        ),
        (Employment, {"company": "A", "title": "B", "current": True, "end_date": "2025-01-01"}),
        (Education, {"school": "A", "start_date": "not a date"}),
    ],
)
def test_invalid_history_dates_rejected(model, kwargs):
    with pytest.raises(ValidationError):
        model.model_validate(kwargs)


async def test_composite_options_reach_model_without_profile_values(profile):
    def respond(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        assert "full_address" in payload["profile_field_options"]
        assert "Composite:" in payload["profile_field_descriptions"]["full_address"]
        assert "employment.0.summary" in payload["profile_field_options"]
        assert profile.address.line1 not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "mappings": [
                                {
                                    "field_id": "0:delivery",
                                    "fact_key": "full_address",
                                    "confidence": 1,
                                    "evidence": "Where should we mail correspondence?",
                                }
                            ]
                        }
                    )
                }
            },
        )

    model = OllamaMappingAgent("test", transport=httpx.MockTransport(respond))
    # Exercise mapping independently; requirement assessment has its own transport tests.
    mappings = await model.map(
        [
            FormField(
                id="delivery", frame=0, label="Where should we mail correspondence?", kind="text"
            )
        ],
        list(profile.values()),
    )
    assert mappings[0].fact_key == "full_address"


async def test_browser_maps_atomic_and_composite_address_fields(profile):
    class Agent:
        async def map(self, fields, keys):
            assert "full_address" in keys and "employment.0.summary" in keys
            choices = {
                "delivery": "full_address",
                "began": "employment.0.start_date",
                "responsibilities": "employment.0.summary",
            }
            return [
                Mapping(field_id=f"{f.frame}:{f.id}", fact_key=choices[f.name], confidence=1)
                for f in fields
                if f.name in choices
            ]

    profile.address.line2 = "Suite 12"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content("""<form>
          <label>Full address<textarea name="full"></textarea></label>
          <label>Street address<input name="street"></label>
          <label>Address line 1<input name="line1"></label>
          <label>Address line 2<input name="line2"></label>
          <label>City<input name="city"></label>
          <label>Where should we mail correspondence?<textarea name="delivery"></textarea></label>
          <label>Engagement began<input name="began" type="date"></label>
          <label>Prior responsibilities<textarea name="responsibilities"></textarea></label>
        </form>""")
        actions, missing = await WorkflowAgent(Agent()).plan((await snapshot(page)).fields, profile)
        assert not missing and len(actions) == 8
        for action in actions:
            await fill(page, action)
        await verify_actions(page, actions)
        assert await page.locator("[name=full]").input_value() == profile.get_address()
        assert await page.locator("[name=delivery]").input_value() == profile.get_address()
        assert await page.locator("[name=street]").input_value() == profile.get_street_address()
        assert await page.locator("[name=line1]").input_value() == profile.address.line1
        assert await page.locator("[name=line2]").input_value() == "Suite 12"
        assert await page.locator("[name=began]").input_value() == "2023-06-01"
        assert (
            await page.locator("[name=responsibilities]").input_value()
            == profile.employment[0].summary
        )
        await browser.close()
