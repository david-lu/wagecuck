"""Aggregate verified current controls once; report rendering does not change results."""

from dataclasses import dataclass

from .execution import ExecutionResult, field_id
from .logical_fields import logical_field_results
from .models import Code

SATISFIED_CODES = {"FILLED", "ALREADY_FILLED"}


@dataclass(frozen=True)
class QuestionExecution:
    question: str
    required: bool
    code: str
    control_ids: tuple[str, ...]

    @property
    def satisfied(self) -> bool:
        return self.code in SATISFIED_CODES


def execution_report(execution: ExecutionResult, analysis: list[dict]) -> dict:
    fields = execution.snapshot.fields
    active_ids = {field_id(field) for field in fields}
    outcomes = [
        field.report() for field in execution.fields if field_id(field.action.field) in active_ids
    ]
    known_analysis = {row["field_id"]: row for row in analysis}
    analysis = [
        known_analysis.get(
            field_id(field),
            {
                "field_id": field_id(field),
                "route": "unresolved",
                "source": None,
            },
        )
        for field in fields
    ]
    questions = logical_field_results(fields, outcomes, analysis)
    by_id = {field_id(field): field for field in fields}
    for question in questions:
        if question["code"] != "UNRESOLVED" and any(
            by_id[key].invalid for key in question["control_ids"]
        ):
            question["code"] = Code.VALIDATION_FAILED.value
    question_results = [
        QuestionExecution(row["question"], row["required"], row["code"], tuple(row["control_ids"]))
        for row in questions
    ]
    required = [question for question in question_results if question.required]
    failures = [question for question in required if not question.satisfied]
    unresolved = [question for question in questions if question["code"] == "UNRESOLVED"]
    retained = all(row["verified"] for row in outcomes)
    # Page-level validation errors have no reliable field association, so they
    # also prevent a pass even if each native input currently looks valid.
    pass_required = not failures and not execution.snapshot.errors
    return {
        "field_count": len(fields),
        "question_count": len(questions),
        "mapped_count": len(outcomes),
        "filled_count": sum(row["verified"] for row in outcomes),
        "mapped_question_count": sum(
            question["code"] not in ("UNRESOLVED", "ALREADY_FILLED", "NOT_SELECTED_GROUP_OPTION")
            for question in questions
        ),
        "filled_question_count": sum(question["code"] == "FILLED" for question in questions),
        "satisfied_question_count": sum(
            question["code"] in SATISFIED_CODES for question in questions
        ),
        "required_question_count": len(required),
        "required_question_satisfied_count": len(required) - len(failures),
        "required_fill_pass": pass_required,
        "required_fill_failure_count": len(failures),
        "required_fill_failures": [question.question for question in failures],
        "made_up_answer_count": sum(
            question.get("made_up", False) and question["code"] == "FILLED"
            for question in questions
        ),
        "completed_values_retained": retained,
        "page_validation_error_count": len(execution.snapshot.errors),
        "required_answers_missing": [
            question["question"] for question in unresolved if question["required"]
        ],
        "unmapped_fields": [
            {
                "label": question["question"],
                "group": question["question"],
                "kind": question["kind"],
                "required": question["required"],
                "options": question.get("options", []),
            }
            for question in unresolved
        ],
        "unmapped_controls": [
            {
                "label": field.label,
                "group": field.group,
                "kind": field.kind,
                "required": field.required,
            }
            for field in fields
            if known_analysis.get(field_id(field), {}).get("route", "unresolved") == "unresolved"
        ],
        "fields": questions,
        "control_outcomes": outcomes,
        "field_analysis": questions,
        "control_analysis": analysis,
    }
