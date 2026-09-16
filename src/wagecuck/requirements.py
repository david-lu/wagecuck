"""Validation and application of model-proposed required-field metadata."""

import re

from .agent_types import RequirementAssessment
from .browser import normalize
from .models import ApplicationError, Code, FormField


def apply_requirement_assessments(
    fields: list[FormField], assessments: list[RequirementAssessment]
) -> None:
    """Validate the complete assessment before changing native field metadata."""
    by_id = {f"{field.frame}:{field.id}": field for field in fields}
    seen: set[str] = set()
    promoted: list[tuple[FormField, str, bool]] = []
    for assessment in assessments:
        if assessment.field_id not in by_id or assessment.field_id in seen:
            raise ApplicationError(
                Code.AGENT_FAILED,
                "Required-field agent proposed an unknown or duplicate field.",
            )
        seen.add(assessment.field_id)
        field = by_id[assessment.field_id]
        if assessment.confidence < 0.95:
            continue
        evidence = normalize(assessment.evidence)
        observed = normalize(f"{field.label} {field.group} {field.context}")
        if not evidence or evidence not in observed:
            raise ApplicationError(
                Code.AGENT_FAILED, "Required-field agent cited evidence absent from the field."
            )
        if not assessment.required:
            if not field.required and re.search(r"optional|not required", evidence):
                promoted.append((field, assessment.evidence, False))
            continue
        if re.search(
            r"\boptional\b|not required",
            f"{field.label} {field.group} {assessment.evidence}",
            re.IGNORECASE,
        ):
            continue
        if not re.search(
            r"required|mandatory|must|cannot|can not|need to|please (?:answer|complete|provide)",
            evidence,
        ):
            continue
        promoted.append((field, assessment.evidence, True))
    for field, evidence, required in promoted:
        field.required = required
        field.requirement_status = "required" if required else "optional"
        field.required_evidence = f"agent: {evidence}"
