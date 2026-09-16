"""Validate and apply model field-to-profile mappings independently."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field

from .agent_types import Mapping
from .browser import normalize
from .field_semantics import SENSITIVE, question
from .logical_fields import logical_key
from .models import Action, Code, FormField, Profile
from .profile_fields import action_for_key, allowed_named_mapping, key_for, restricted_key


@dataclass
class MappingResult:
    actions: list[Action] = dataclass_field(default_factory=list)
    resolved: set[str] = dataclass_field(default_factory=set)
    warnings: list[dict] = dataclass_field(default_factory=list)


def available_mapping_values(profile: Profile, facts: dict, *, declared=None) -> dict:
    """Expose declared sensitive values and ordinary profile facts to classification."""
    declared = profile.declared_values() if declared is None else declared
    available = {
        key: value
        for key, value in facts.items()
        if not SENSITIVE.search(key) or key in declared or key == "compensation_expectations"
    }
    available["documents.resume"] = str(profile.resume)
    if profile.cover_letter:
        available["documents.cover_letter"] = str(profile.cover_letter)
    return available


def apply_mappings(
    proposals: list[Mapping],
    eligible: list[FormField],
    all_fields: list[FormField],
    available: dict,
    profile: Profile,
    existing_actions: list[Action],
    *,
    values: dict | None = None,
) -> MappingResult:
    """Keep valid proposals even when other model proposals are malformed or unsafe."""
    result = MappingResult()
    by_id = {f"{field.frame}:{field.id}": field for field in eligible}
    accepted_fields: set[str] = set()
    accepted_radio_groups = {
        logical_key(action.field) for action in existing_actions if action.field.kind == "radio"
    }

    def reject(field_id: str, message: str) -> None:
        result.warnings.append(
            {
                "field_id": field_id,
                "stage": "mapping",
                "message": message,
                "code": Code.AGENT_FAILED,
            }
        )

    for proposal in proposals:
        field_id = proposal.field_id
        field = by_id.get(field_id)
        keys = proposal.fact_keys or ([proposal.fact_key] if proposal.fact_key else [])
        if field is None:
            reject(field_id, "Model proposed an unknown field ID.")
            continue
        if field_id in accepted_fields:
            reject(field_id, "Model proposed a duplicate mapping for an already mapped field.")
            continue
        if not keys:
            reject(field_id, "Model mapping did not include a profile field.")
            continue
        if proposal.fact_key and proposal.fact_keys:
            reject(field_id, "Model mapping used both single-key and multi-key forms.")
            continue
        unknown = [key for key in keys if key not in available]
        if unknown:
            reject(field_id, "Model mapping referenced an unknown profile field.")
            continue
        if len(keys) != len(set(keys)):
            reject(field_id, "Model mapping repeated a profile field.")
            continue
        if proposal.confidence < 0.95:
            reject(field_id, "Model mapping confidence was below the acceptance threshold.")
            continue
        sensitive = bool(SENSITIVE.search(f"{field.label} {field.group} {field.context}"))
        if not allowed_named_mapping(field, keys, profile, sensitive):
            reject(
                field_id,
                "Model selected a declaration with incompatible meaning, jurisdiction or currency.",
            )
            continue
        observed = normalize(f"{field.label} {field.group} {field.context}")
        if proposal.evidence and normalize(proposal.evidence) not in observed:
            reject(field_id, "Model cited evidence absent from this field.")
            continue
        document_keys = [key for key in keys if key.startswith("documents.")]
        if (field.kind == "file" and (len(keys) != 1 or not document_keys)) or (
            field.kind != "file" and document_keys
        ):
            reject(field_id, "Document mappings must target upload fields only.")
            continue
        if len(keys) > 1 and (
            field.kind not in ("text", "textarea")
            or not all(isinstance(available[key], str) for key in keys)
        ):
            reject(field_id, "Only text fields support combined profile values.")
            continue

        value = (
            available[keys[0]]
            if len(keys) == 1
            else proposal.separator.join(available[key] for key in keys)
        )
        if len(keys) == 1 and (restricted_key(keys[0]) or keys[0] == key_for(field, profile)):
            handled, action = action_for_key(field, all_fields, profile, keys[0], values=values)
            if not handled or action is None:
                reject(field_id, "Profile value does not select or fill this control.")
                continue
            action.source = "agent:" + keys[0]
            result.actions.append(action)
            accepted_fields.add(field_id)
            if field.kind == "radio":
                group = logical_key(field)
                accepted_radio_groups.add(group)
                result.resolved.update(
                    f"{peer.frame}:{peer.id}"
                    for peer in all_fields
                    if peer.kind == "radio" and logical_key(peer) == group
                )
            else:
                result.resolved.add(field_id)
            continue

        if field.kind == "radio":
            choice = "yes" if value is True else "no" if value is False else question(value)
            if choice != question(field.label):
                reject(field_id, "Mapped value does not select this radio option.")
                continue
            group = logical_key(field)
            if group in accepted_radio_groups:
                reject(field_id, "Radio mapping does not identify one unambiguous choice.")
                continue
            value = True
        elif field.kind == "checkbox" and not isinstance(value, bool):
            reject(field_id, "Checkbox mappings require a boolean profile field.")
            continue

        result.actions.append(Action(field=field, value=value, source="agent:" + "+".join(keys)))
        accepted_fields.add(field_id)
        if field.kind == "radio":
            group = logical_key(field)
            accepted_radio_groups.add(group)
            result.resolved.update(
                f"{peer.frame}:{peer.id}"
                for peer in all_fields
                if peer.kind == "radio" and logical_key(peer) == group
            )
        else:
            result.resolved.add(field_id)
    return result
