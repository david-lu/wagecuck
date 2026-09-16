from wagecuck import agent, profile_catalog
from wagecuck.agent import Mapping, WorkflowAgent
from wagecuck.models import FormField


async def test_planner_resolves_profile_once_across_named_and_mapped_fields(profile, monkeypatch):
    calls = {"values": 0, "declared": 0}
    original_values = profile_catalog.resolve_profile_values
    original_declared = profile_catalog.declared_profile_values

    def values(*args, **kwargs):
        calls["values"] += 1
        return original_values(*args, **kwargs)

    def declared(*args, **kwargs):
        calls["declared"] += 1
        return original_declared(*args, **kwargs)

    monkeypatch.setattr(agent, "resolve_profile_values", values)
    monkeypatch.setattr(agent, "declared_profile_values", declared)
    monkeypatch.setattr(profile_catalog, "resolve_profile_values", values)
    monkeypatch.setattr(profile_catalog, "declared_profile_values", declared)

    class Mapper:
        async def map(self, fields, keys):
            return [Mapping(field_id="0:portfolio", fact_key="website", confidence=1)]

    fields = [
        FormField(id=id, frame=0, kind="text", label=label)
        for id, label in [
            ("notice", "Notice period"),
            ("salary", "Desired Salary"),
            ("portfolio", "Where can we view your work?"),
        ]
    ]
    actions, unresolved = await WorkflowAgent(Mapper()).plan(fields, profile)
    assert len(actions) == 3 and not unresolved
    assert calls == {"values": 1, "declared": 1}


def test_catalog_preserves_false_values_aliases_and_structured_precedence(profile):
    profile.screening.work_authorization["CA"].authorized = False
    profile.consents.application_processing = False
    profile.facts.update(authorized_canada=True, city="Stale legacy city")
    values = profile.values()
    assert values["authorized_canada"] is False
    assert values["screening.work_authorization.CA.authorized"] is False
    assert values["consents.application_processing"] is False
    assert values["city"] == profile.address.city != "Stale legacy city"
    assert values["full_address"] == profile.get_address()
    assert values["current_company"] == profile.get_current_employment().company
    assert "full_address" in profile_catalog.PROFILE_FIELD_DESCRIPTIONS
