"""Application-form planning coordinator.

Public agent contracts remain re-exported here for compatibility. Provider clients,
field semantics, requirement validation, mapping validation, and inference validation
live in focused modules.
"""

from __future__ import annotations

import re

from .agent_client import OllamaMappingAgent, StructuredMappingAgent
from .agent_types import (
    AgentOperation,
    AgentRequest,
    Draft,
    Drafts,
    Mapping,
    MappingAgent,
    Mappings,
    RequirementAssessment,
    Requirements,
)
from .field_semantics import (
    ALIASES,
    AUTOCOMPLETE,
    PROFILE_FIELD_DESCRIPTIONS,
    SENSITIVE,
    fact_key,
    field_metadata,
    question,
)
from .field_values import FieldValueError, normalize_field_value
from .inference import apply_inferred_answers
from .logical_fields import group_members, logical_groups
from .mapping import apply_mappings, available_mapping_values
from .models import Action, ApplicationError, Code, FormField, Profile
from .profile_catalog import declared_profile_values, resolve_profile_values
from .profile_fields import key_for, plan_named, screening_options
from .requirements import apply_requirement_assessments
from .screening import plan_screening

# Historical public name used by a few integrations.
metadata = field_metadata


class WorkflowAgent:
    """Run deterministic parsing before bounded model mapping and answer inference."""

    def __init__(self, fallback: MappingAgent | None = None):
        self.fallback = fallback
        self.warnings: list[dict] = []

    async def assess_required(self, fields: list[FormField]) -> None:
        for field in fields:
            if field.required:
                field.requirement_status = "required"
        assessor = getattr(self.fallback, "assess", None)
        if not assessor or not fields:
            return
        apply_requirement_assessments(fields, await assessor(fields))

    async def plan(
        self, fields: list[FormField], profile: Profile, *, agent_fill: bool = False
    ) -> tuple[list[Action], list[FormField]]:
        declared = declared_profile_values(profile)
        facts = resolve_profile_values(profile, declared)
        self.warnings = []
        answers = {question(key): value for key, value in profile.answers.items()}
        actions: list[Action] = []
        unresolved: list[FormField] = []
        source_choices = {}

        for field in fields:
            label = question(field.label)
            group = question(field.group)
            value, source = None, ""
            if label not in answers and not (field.kind == "radio" and group in answers):
                handled, action = plan_screening(field, fields, profile)
                if handled:
                    if action:
                        actions.append(action)
                    continue
                handled, action = plan_named(field, fields, profile, source_choices, values=facts)
                if handled:
                    if action:
                        actions.append(action)
                    continue
            if field.kind == "radio" and group in answers:
                answer = answers[group]
                chosen = "yes" if answer is True else "no" if answer is False else question(answer)
                if not any(question(peer.label) == chosen for peer in group_members(field, fields)):
                    unresolved.append(field)
                    continue
                if chosen == label:
                    value, source = True, f"answers:{group}"
                else:
                    continue
            elif label in answers:
                value, source = answers[label], f"answers:{label}"
            elif field.kind == "file":
                if field.fact_key == "resume" or re.search(r"resume|résumé|cv", label):
                    value, source = str(profile.resume), "document:resume"
                elif re.search(r"cover.?letter", label) and profile.cover_letter:
                    value, source = str(profile.cover_letter), "document:cover_letter"
            elif field.kind not in ("radio", "checkbox") and not SENSITIVE.search(
                label + " " + group
            ):
                key = fact_key(field)
                if key in facts and key != "source":
                    value, source = facts[key], f"facts:{key}"
            if value is not None:
                actions.append(Action(field=field, value=value, source=source))
            elif not field.filled:
                unresolved.append(field)

        # A fallback must see the complete desired selection, including checked peers
        # it may need to clear. Preserve explicit per-option deterministic answers.
        for group_fields in logical_groups(fields):
            if group_fields[0].kind not in ("radio", "checkbox"):
                continue
            if any(field in unresolved for field in group_fields) and not any(
                action.field in group_fields for action in actions
            ):
                unresolved.extend(field for field in group_fields if field not in unresolved)

        try:
            await self.assess_required(fields)
        except ApplicationError as exc:
            if not (agent_fill and getattr(self.fallback, "infer", None)):
                raise
            self.warnings.append({"stage": "requirements", "message": str(exc), "code": exc.code})

        eligible = [
            field
            for field in unresolved
            if not (
                field.kind not in ("checkbox", "radio", "file")
                and (known_key := (fact_key(field) or key_for(field, profile)))
                and known_key not in facts
            )
            if (
                not SENSITIVE.search(f"{field.label} {field.group} {field.context}")
                or key_for(field, profile)
                or screening_options(field, profile)
            )
            and field.kind
            in (
                "text",
                "textarea",
                "email",
                "tel",
                "url",
                "select",
                "combobox",
                "checkbox",
                "radio",
                "number",
                "range",
                "date",
                "datetime-local",
                "month",
                "week",
                "time",
                "file",
            )
        ]
        if self.fallback and eligible:
            try:
                await self._map_unresolved(
                    eligible, fields, facts, profile, actions, unresolved, declared
                )
            except ApplicationError as exc:
                if not (agent_fill and getattr(self.fallback, "infer", None)):
                    raise
                self.warnings.append({"stage": "mapping", "message": str(exc), "code": exc.code})

        self._validate_planned_values(fields, actions, unresolved)

        if agent_fill:
            if getattr(self.fallback, "infer", None):
                await self._infer_unmapped(unresolved, actions, facts)
            else:
                await self._draft_unmapped(unresolved, actions, facts)

        for action in actions:
            if action.source.startswith("random:"):
                action.answer_basis = "made_up"
                action.made_up = True
                action.inference_reason = "Random source choice requested by the profile."
        return actions, unresolved

    def _validate_planned_values(self, fields, actions, unresolved) -> None:
        """Apply HTML value contracts before deciding which fields need inference."""
        rejected = []
        for action in actions:
            if action.random_choice:
                continue
            try:
                action.value = normalize_field_value(action.field, action.value)
            except FieldValueError as exc:
                rejected.append(action)
                self.warnings.append(
                    {
                        "field_id": f"{action.field.frame}:{action.field.id}",
                        "stage": "value_contract",
                        "message": str(exc),
                        "code": Code.FIELD_FILL_FAILED,
                    }
                )
        for action in rejected:
            actions.remove(action)
            field = action.field
            candidates = group_members(field, fields) if field.kind == "radio" else [field]
            for candidate in candidates:
                if not candidate.filled and candidate not in unresolved:
                    unresolved.append(candidate)

    async def _infer_unmapped(self, unresolved, actions, facts) -> None:
        candidates = [
            field
            for field in unresolved
            if field.kind
            in (
                "text",
                "textarea",
                "email",
                "url",
                "tel",
                "number",
                "range",
                "date",
                "datetime-local",
                "month",
                "week",
                "time",
                "select",
                "combobox",
                "checkbox",
                "radio",
            )
        ]
        if not candidates:
            return
        grounding = {
            key: value
            for key, value in facts.items()
            if not re.search(r"password|secret|token|api_key|documents\.", key, re.IGNORECASE)
        }
        answers = await self.fallback.infer(candidates, grounding)
        inferred, resolved, warnings = apply_inferred_answers(candidates, answers, grounding)
        actions.extend(inferred)
        self.warnings.extend(warnings)
        unresolved[:] = [
            field for field in unresolved if f"{field.frame}:{field.id}" not in resolved
        ]

    async def _map_unresolved(
        self, eligible, fields, facts, profile, actions, unresolved, declared
    ) -> None:
        available = available_mapping_values(profile, facts, declared=declared)
        proposals = await self.fallback.map(eligible, list(available))
        take_warnings = getattr(self.fallback, "take_mapping_warnings", None)
        if take_warnings:
            self.warnings.extend(take_warnings())
        result = apply_mappings(
            proposals, eligible, fields, available, profile, actions, values=facts
        )
        actions.extend(result.actions)
        self.warnings.extend(result.warnings)
        unresolved[:] = [
            field for field in unresolved if f"{field.frame}:{field.id}" not in result.resolved
        ]

    async def _draft_unmapped(self, unresolved, actions, facts) -> None:
        draft = getattr(self.fallback, "draft", None)
        if not draft:
            raise ApplicationError(
                Code.AGENT_FAILED, "Agent-fill requires a configured drafting agent."
            )
        candidates = [
            field
            for field in unresolved
            if field.kind in ("text", "textarea")
            and not SENSITIVE.search(f"{field.label} {field.group} {field.context}")
            and re.search(
                r"why|describe|tell us|cover.?letter|summary|motivat|interest|about you|anything else|additional (?:information|comments)",
                field.label,
                re.IGNORECASE,
            )
        ]
        grounding = {
            key: value
            for key, value in facts.items()
            if isinstance(value, str)
            and (
                key in ("skills", "project_summary", "current_company", "current_title")
                or key.startswith(("employment.", "education.", "projects.", "achievements."))
            )
            and not SENSITIVE.search(key)
        }
        if not candidates or not grounding:
            return
        drafts = await draft(candidates, grounding)
        by_id = {f"{field.frame}:{field.id}": field for field in candidates}
        seen = set()
        for result in drafts:
            if (
                result.field_id not in by_id
                or result.field_id in seen
                or not result.fact_keys
                or any(key not in grounding for key in result.fact_keys)
            ):
                raise ApplicationError(
                    Code.AGENT_FAILED,
                    "Agent-fill referenced an unknown field or unsupported source fact.",
                )
            seen.add(result.field_id)
            if result.confidence < 0.95:
                continue
            numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", result.text))
            supported_numbers = set(
                re.findall(
                    r"\b\d+(?:\.\d+)?\b",
                    " ".join(grounding[key] for key in result.fact_keys),
                )
            )
            if not numbers.issubset(supported_numbers):
                raise ApplicationError(
                    Code.AGENT_FAILED, "Agent-fill introduced unsupported numerical claims."
                )
            field = by_id[result.field_id]
            actions.append(
                Action(
                    field=field,
                    value=result.text,
                    source="agent_fill:" + "+".join(result.fact_keys),
                    answer_basis="inferred",
                    source_keys=result.fact_keys,
                    inference_reason="Drafted from the cited career facts.",
                )
            )
            unresolved.remove(field)

    @staticmethod
    def describe(fields, actions, unresolved):
        by_id = {(action.field.frame, action.field.id): action for action in actions}
        missing = {(field.frame, field.id) for field in unresolved}
        rows = []
        for field in fields:
            action = by_id.get((field.frame, field.id))
            route = (
                (
                    "random_choice"
                    if action.source.startswith("random:")
                    else "agent_fill"
                    if action.source.startswith("agent_fill:")
                    else "agent_mapping"
                    if action.source.startswith("agent:")
                    else "deterministic"
                )
                if action
                else "unresolved"
                if (field.frame, field.id) in missing
                else "already_filled_or_group_option"
            )
            rows.append(
                {
                    "field_id": f"{field.frame}:{field.id}",
                    "label": field.label,
                    "group": field.group,
                    "kind": field.kind,
                    "required": field.required,
                    "requirement_status": field.requirement_status,
                    "required_evidence": field.required_evidence,
                    "route": route,
                    "source": action.source if action else None,
                    "answer_basis": action.answer_basis if action else None,
                    "made_up": action.made_up if action else False,
                    "source_keys": action.source_keys if action else [],
                    "inference_reason": action.inference_reason if action else "",
                }
            )
        return rows


__all__ = [
    "ALIASES",
    "AUTOCOMPLETE",
    "PROFILE_FIELD_DESCRIPTIONS",
    "SENSITIVE",
    "AgentOperation",
    "AgentRequest",
    "Draft",
    "Drafts",
    "Mapping",
    "MappingAgent",
    "Mappings",
    "OllamaMappingAgent",
    "RequirementAssessment",
    "Requirements",
    "StructuredMappingAgent",
    "WorkflowAgent",
    "fact_key",
    "metadata",
    "question",
]
