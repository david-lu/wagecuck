"""Deterministic label normalization and profile-field recognition."""

import re

from .browser import normalize
from .field_values import field_contract
from .logical_fields import logical_groups, logical_question, option_label
from .models import FormField
from .profile_catalog import ALIASES, AUTOCOMPLETE, PROFILE_FIELD_DESCRIPTIONS

__all__ = [
    "ALIASES",
    "AUTOCOMPLETE",
    "PROFILE_FIELD_DESCRIPTIONS",
    "SENSITIVE",
    "fact_key",
    "field_metadata",
    "fields_metadata",
    "question",
]

SENSITIVE = re.compile(
    r"consent|agree|certif|privacy|terms|sponsor|authoriz|eligib|right to work|legally allowed|permission|permitted|entitled|visa|immigration|work permit|citizen|gender|pronoun|race|racial|ethnic|hispanic|latin(?:o|a|x|e)|sexual orientation|sexuality|transgender|veteran|disab|medical|health|salary|compensation|desired pay|criminal|felony|background|relocat|password|secret|token|passport|social security",
    re.IGNORECASE,
)


def question(text: str) -> str:
    text = re.sub(r"[\s*✱]+$", "", text)
    text = re.sub(
        r"\s*[\[(]?(?:not required|optional|required)[\])]?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*\(if any\)\s*$", "", text, flags=re.IGNORECASE)
    return normalize(re.sub(r"^\s*\(required\)\s*", "", text, flags=re.IGNORECASE))


def fact_key(field: FormField) -> str | None:
    label = question(field.label)
    if field.kind == "tel":
        label = re.sub(r"\s+\d{1,4}$", "", label)
    candidates = [label, re.sub(r"^(?:your|please (?:enter|provide))\s+", "", label)]
    if not label or label == question(field.name):
        name = re.sub(r"([a-z])([A-Z])", r"\1 \2", field.name)
        name = normalize(re.sub(r"[_\[\].-]+", " ", name))
        candidates.append(re.sub(r"^(?:candidate|applicant|application|personal)\s+", "", name))
    keys = {key for key, aliases in ALIASES.items() if any(c in aliases for c in candidates)}
    if len(keys) == 1:
        return keys.pop()
    if field.fact_key:
        return field.fact_key
    return AUTOCOMPLETE.get(field.autocomplete.split()[-1] if field.autocomplete else "")


def field_metadata(field: FormField, peers: list[FormField] | None = None) -> dict:
    peers = peers or [field]
    question_text = logical_question(peers)
    return {
        "field_id": f"{field.frame}:{field.id}",
        "label": field.label,
        "group": field.group,
        "group_id": field.group_id,
        "logical_question": question_text,
        "group_options": [
            {
                "field_id": f"{peer.frame}:{peer.id}",
                "label": option_label(peer, question_text),
            }
            for peer in peers
        ]
        if len(peers) > 1 and field.kind in ("radio", "checkbox")
        else [],
        "name": field.name,
        "autocomplete": field.autocomplete,
        "kind": field.kind,
        "value_contract": field_contract(field),
        "context": field.context,
        "required": field.required,
        "requirement_status": field.requirement_status,
        "options": [option.model_dump() for option in field.options],
    }


def fields_metadata(fields: list[FormField]) -> list[dict]:
    return [field_metadata(field, group) for group in logical_groups(fields) for field in group]
