"""Typed answers for the final profile inference / invented-answer fallback."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from .browser import normalize
from .field_values import FieldValueError, normalize_field_value
from .models import Action


class FieldAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_id: str
    value: StrictStr | StrictBool
    basis: Literal["profile", "inferred", "made_up"]
    fact_keys: list[str]
    reason: str = Field(min_length=1, max_length=1200)


class FieldAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[FieldAnswer]


def apply_inferred_answers(fields, answers, facts):
    """Validate control types/options, keep per-answer provenance, isolate invalid answers."""
    by_id = {f"{f.frame}:{f.id}": f for f in fields}
    counts = {}
    for answer in answers:
        counts[answer.field_id] = counts.get(answer.field_id, 0) + 1
    actions, resolved, warnings = [], set(), []
    radio_selections = {}
    for answer in answers:
        field = by_id.get(answer.field_id)
        if field and field.kind == "radio" and answer.value is True:
            group = (field.frame, field.name, field.group)
            radio_selections.setdefault(group, []).append(answer.field_id)
    for answer in answers:
        field = by_id.get(answer.field_id)
        error = None
        value = answer.value
        if field is None or counts[answer.field_id] != 1:
            error = "Unknown or duplicate field ID."
        elif any(key not in facts for key in answer.fact_keys):
            error = "Unknown profile source key."
        elif field.kind in ("checkbox", "radio"):
            if not isinstance(value, bool):
                error = "Checkbox/radio answers must be boolean."
            elif field.kind == "radio":
                group = (field.frame, field.name, field.group)
                if not field.name or not field.group or len(radio_selections.get(group, [])) != 1:
                    error = "Radio group needs one unambiguous selection."
                elif not value:
                    continue  # Checking the selected peer clears the other options.
            elif field.required and not field.group and not value:
                error = "An unchecked required standalone checkbox remains unresolved."
        elif not isinstance(value, str) or not value.strip():
            error = "This field needs a nonempty text answer."
        elif field.kind in ("select", "combobox") and field.options:
            matches = [o for o in field.options if value in (o.label, o.value)]
            if len(matches) != 1 or not matches[0].value:
                error = "Answer does not identify one available option."
            else:
                value = matches[0].value if field.kind == "select" else matches[0].label
        elif field.kind == "select":
            error = "No available select options."
        else:
            try:
                value = normalize_field_value(field, value)
            except FieldValueError as exc:
                error = str(exc)
        if error:
            warnings.append({"field_id": answer.field_id, "stage": "inference", "message": error})
            continue
        basis = answer.basis
        # Unsupported answers must never be logged as grounded merely because
        # the model omitted citations or called a transformed value a direct copy.
        if (
            not answer.fact_keys
            or basis == "profile"
            and not any(
                normalize(str(answer.value)) == normalize(str(facts[k])) for k in answer.fact_keys
            )
        ):
            basis = "made_up"
        if (
            basis == "inferred"
            and re.search(r"\bpreferred|\bfavou?rite|\bpreference", field.label, re.IGNORECASE)
            and not any(
                re.search(r"preferred|favou?rite|preference", key, re.IGNORECASE)
                for key in answer.fact_keys
            )
        ):
            basis = "made_up"
        if re.search(
            r"\bnot (?:explicitly )?(?:provided|stated|specified|supplied|known)\b|"
            r"\bassum(?:ed|ption)\b|\binvented\b|\bguess(?:ed)?\b|\bplausible\b",
            answer.reason,
            re.IGNORECASE,
        ):
            basis = "made_up"  # An admitted unsupported component makes the whole answer invented.
        actions.append(
            Action(
                field=field,
                value=value,
                source="agent_fill:" + ("+".join(answer.fact_keys) or "made_up"),
                answer_basis=basis,
                made_up=basis == "made_up",
                source_keys=answer.fact_keys,
                inference_reason=answer.reason,
            )
        )
        resolved.add(answer.field_id)
        if field.kind == "radio":
            resolved.update(
                key
                for key, peer in by_id.items()
                if peer.kind == "radio"
                and (peer.frame, peer.name, peer.group) == (field.frame, field.name, field.group)
            )
    return actions, resolved, warnings
