"""Group physical choice controls into logical application questions."""

from __future__ import annotations

import re
from collections import OrderedDict

from .models import FormField


def logical_groups(fields: list[FormField]) -> list[list[FormField]]:
    groups = OrderedDict()
    for field in fields:
        key = logical_key(field)
        groups.setdefault(key, []).append(field)
    return list(groups.values())


def logical_key(field: FormField) -> tuple:
    """Use DOM identity; textual fallbacks support snapshots made by older callers."""
    if field.kind in ("radio", "checkbox"):
        if field.group_id:
            return (field.frame, field.kind, "dom", field.group_id)
        # Native radio membership is determined by name, never its visible heading.
        if field.kind == "radio" and field.name:
            return (field.frame, field.kind, "name", field.name)
        group = field.group.strip()
        if group and not re.fullmatch(r"\d+[.\s-]*(?:questions?|section)", group, re.IGNORECASE):
            return (field.frame, field.kind, "label", group)
        if field.name:
            return (field.frame, field.kind, "name", field.name)
        if field.context:
            return (field.frame, field.kind, "context", field.context)
    return (field.frame, field.kind, field.id)


def group_members(field: FormField, fields: list[FormField]) -> list[FormField]:
    key = logical_key(field)
    return [peer for peer in fields if logical_key(peer) == key]


def logical_question(group: list[FormField]) -> str:
    first = group[0]
    if len(group) == 1 or first.kind not in ("radio", "checkbox"):
        return first.label
    group_label = first.group.strip()
    if group_label and not re.fullmatch(
        r"\d+[.\s-]*(?:questions?|section)", group_label, re.IGNORECASE
    ):
        return group_label
    stripped = [_strip_boolean_suffix(field.label) for field in group]
    if stripped and len(set(stripped)) == 1 and stripped[0]:
        return stripped[0]
    return group_label or first.label


def option_label(field: FormField, question: str) -> str:
    label = field.label.strip()
    if question and label.casefold().startswith(question.casefold()):
        label = label[len(question) :].strip(" :-–—")
    return label or field.label


def group_invalid(group: list[FormField]) -> bool:
    """Validate choice groups as questions, not as independent required inputs."""
    grouped_choice = len(group) > 1 and group[0].kind in ("radio", "checkbox")
    if grouped_choice:
        return any(field.required for field in group) and not any(field.filled for field in group)
    return any(field.invalid for field in group)


def logical_field_results(fields, outcomes, analysis):
    """Return one report row per question while retaining physical controls as options."""
    outcome_by_id = {row["field_id"]: row for row in outcomes}
    analysis_by_id = {row["field_id"]: row for row in analysis}
    rows = []
    for group in logical_groups(fields):
        question = logical_question(group)
        ids = [f"{field.frame}:{field.id}" for field in group]
        group_outcomes = [outcome_by_id[field_id] for field_id in ids if field_id in outcome_by_id]
        group_analysis = [
            analysis_by_id[field_id] for field_id in ids if field_id in analysis_by_id
        ]
        failures = [row for row in group_outcomes if row["code"] != "FILLED"]
        if failures:
            code = failures[0]["code"]
        elif any(row["route"] == "unresolved" for row in group_analysis):
            code = "UNRESOLVED"
        elif group_invalid(group):
            code = "VALIDATION_FAILED"
        elif group_outcomes:
            code = "FILLED"
        elif any(field.filled for field in group):
            code = "ALREADY_FILLED"
        else:
            code = "NOT_SELECTED_GROUP_OPTION"
        kind = group[0].kind
        grouped_choice = kind in ("radio", "checkbox") and len(group) > 1
        sources = list(dict.fromkeys(row["source"] for row in group_outcomes if row.get("source")))
        bases = list(
            dict.fromkeys(row["answer_basis"] for row in group_outcomes if row.get("answer_basis"))
        )
        acted_routes = [
            analysis_by_id[row["field_id"]]["route"]
            for row in group_outcomes
            if row["field_id"] in analysis_by_id and analysis_by_id[row["field_id"]].get("route")
        ]
        routes = list(
            dict.fromkeys(
                acted_routes or [row["route"] for row in group_analysis if row.get("route")]
            )
        )
        row = {
            "question": question,
            "label": question,
            "kind": f"{kind}_group" if grouped_choice else kind,
            "required": any(field.required for field in group),
            "code": code,
            "control_ids": ids,
            "group_id": group[0].group_id,
            "source": sources[0] if len(sources) == 1 else None,
            "sources": sources,
            "answer_basis": bases[0] if len(bases) == 1 else None,
            "made_up": any(result.get("made_up", False) for result in group_outcomes),
            "route": routes[0] if len(routes) == 1 else "mixed" if routes else None,
        }
        if grouped_choice:
            row["options"] = [
                {
                    "field_id": field_id,
                    "label": option_label(field, question),
                    "selected": bool(outcome_by_id.get(field_id, {}).get("selected", field.filled)),
                    "code": outcome_by_id.get(field_id, {}).get("code", "NOT_SELECTED"),
                }
                for field, field_id in zip(group, ids, strict=True)
            ]
        elif group[0].options:
            row["options"] = [option.model_dump() for option in group[0].options]
        rows.append(row)
    return rows


def _strip_boolean_suffix(label: str) -> str:
    return re.sub(r"\s+(?:yes|no|true|false)\s*$", "", label, flags=re.IGNORECASE).strip()
