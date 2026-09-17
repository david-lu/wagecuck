"""One bounded pass of the shared field executor and final DOM verification."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

from .browser import fill, match_option, prepared_snapshot, snapshot, verify_action_results
from .controls.registry import handler_for
from .field_roles import fill_priority
from .field_values import FieldValueError, normalize_field_value
from .logical_fields import logical_groups, logical_key
from .models import Action, ApplicationError, Code, FormField, Snapshot


def field_id(field: FormField) -> str:
    return f"{field.frame}:{field.id}"


def form_signature(snap: Snapshot) -> tuple:
    """Describe actionable state without volatile DOM IDs or applicant values."""
    return (
        snap.url,
        tuple(
            (
                field.frame,
                field.name,
                field.label,
                field.kind,
                field.control_type,
                field.group,
                field.group_id,
                field.required,
                field.requirement_status,
                field.filled,
                field.invalid,
                tuple((option.label, option.value) for option in field.options),
                field.placeholder,
                field.pattern,
                field.minimum,
                field.maximum,
                field.step,
            )
            for field in snap.fields
        ),
        tuple((control.label, control.action, control.kind) for control in snap.controls),
        tuple(snap.errors),
    )


def field_contracts(fields: list[FormField]) -> tuple:
    """Compare control contracts while ignoring ordinary value/validity changes."""
    return tuple(
        (
            field.frame,
            field.name,
            field.label,
            field.kind,
            field.control_type,
            field.group,
            field.group_id,
            field.required,
            field.requirement_status,
            tuple((option.label, option.value) for option in field.options),
            field.placeholder,
            field.pattern,
            field.minimum,
            field.maximum,
            field.step,
            field_id(field),
        )
        for field in fields
    )


def fields_changed(before: list[FormField], after: list[FormField]) -> bool:
    return field_contracts(before) != field_contracts(after)


def form_changed(before: Snapshot, after: Snapshot) -> bool:
    """Replan changed contracts and replaced nodes, excluding ordinary filling."""
    return (
        before.url != after.url
        or fields_changed(before.fields, after.fields)
        or tuple((control.label, control.action, control.kind) for control in before.controls)
        != tuple((control.label, control.action, control.kind) for control in after.controls)
        or tuple(before.errors) != tuple(after.errors)
    )


def preserve_requirements(fields, assessed_fields):
    assessed = {field_id(field): field for field in assessed_fields}
    for field in fields:
        previous = assessed.get(field_id(field))
        if (
            previous
            and previous.required_evidence.startswith("agent:")
            and field.requirement_status == "unknown"
        ):
            field.required = previous.required
            field.requirement_status = previous.requirement_status
            field.required_evidence = previous.required_evidence


async def settled_snapshot(page, *, polls: int = 4, interval: float = 0.1) -> Snapshot:
    """Give bounded change handlers time to reveal local conditional controls."""
    current = await snapshot(page)
    quiet = 0
    previous = form_signature(current)
    # Reset the quiet window when meaningful state changes, with an absolute cap.
    for index in range(max(polls, 12)):
        await asyncio.sleep(interval)
        current = await snapshot(page)
        state = form_signature(current)
        quiet = quiet + 1 if state == previous else 0
        previous = state
        if index + 1 >= polls and quiet >= 2:
            break
    return await prepared_snapshot(page)


@dataclass(frozen=True)
class FieldExecution:
    action: Action
    present: bool
    verified: bool
    code: str
    message: str = ""
    selected: bool | None = None

    def report(self) -> dict:
        action, field = self.action, self.action.field
        return {
            "field_id": field_id(field),
            "label": field.label,
            "group": field.group,
            "kind": field.kind,
            "required": field.required,
            "code": self.code,
            "present": self.present,
            "verified": self.verified,
            "source": action.source,
            "answer_basis": action.answer_basis,
            "made_up": action.made_up,
            "source_keys": action.source_keys,
            "inference_reason": action.inference_reason,
            "message": self.message,
            "selected": self.selected,
        }


@dataclass(frozen=True)
class ExecutionResult:
    snapshot: Snapshot
    fields: list[FieldExecution]
    needs_replan: bool = False

    @property
    def retained(self) -> bool:
        return all(field.verified for field in self.fields if field.present)


def reusable_answer(previous: Action, proposed: Action) -> bool:
    """Retain generated answers only while their current control permits them."""
    generated_sources = ("random:", "agent_fill:")
    if not previous.source.startswith(generated_sources) or not proposed.source.startswith(
        generated_sources
    ):
        return False
    if proposed.answer_basis == "profile":
        return False
    try:
        normalize_field_value(proposed.field, previous.value)
    except FieldValueError:
        return False
    if proposed.field.kind == "select" or proposed.field.options:
        return match_option(previous.value, proposed.field.options) is not None
    return True


async def execute_actions(
    page,
    actions: list[Action],
    *,
    previous_actions: list[Action] | None = None,
    assessed_fields: list[FormField] | None = None,
    settle_polls: int = 4,
) -> ExecutionResult:
    """Fill independently, then assign success only after verifying the final DOM.

    Both the application runner and the network-isolated probe use this function.
    It never navigates or clicks next/submit. Missing targets are reported for the
    caller's bounded replanning, rather than silently treated as verified.
    """

    # Keep generated answers consistent within this invocation, including after
    # a DOM replacement; a replan must not invent a different applicant answer.
    def identity(field):
        return (
            field.frame,
            field.name,
            field.label,
            field.kind,
            field.group,
            field.group_id,
        )

    current_counts = Counter(
        identity(field)
        for field in (
            assessed_fields if assessed_fields is not None else [action.field for action in actions]
        )
    )
    current_fields = (
        assessed_fields if assessed_fields is not None else [action.field for action in actions]
    )
    radio_plans = Counter(
        logical_key(action.field) for action in actions if action.field.kind == "radio"
    )
    prior_choices = {
        logical_key(action.field): action
        for action in previous_actions or []
        if action.field.kind == "radio" and action.value is True
    }
    for action in actions:
        if action.field.kind != "radio" or action.value is not True:
            continue
        group = logical_key(action.field)
        prior = prior_choices.get(group)
        if radio_plans[group] != 1 or prior is None or not reusable_answer(prior, action):
            continue
        peers = [
            field
            for field in current_fields
            if logical_key(field) == group and identity(field) == identity(prior.field)
        ]
        if len(peers) == 1:
            action.field = peers[0]

    latest = {identity(action.field): action for action in previous_actions or []}
    # Reuse a generated checkbox set atomically. If its selected option vanished,
    # retaining only the old False values would erase the replacement selection.
    checkbox_reuse = {}
    for peers in logical_groups([action.field for action in actions]):
        if peers[0].kind != "checkbox":
            continue
        group = logical_key(peers[0])
        proposed = [action for action in actions if logical_key(action.field) == group]
        prior = [action for action in latest.values() if logical_key(action.field) == group]
        if not prior:
            continue
        selected = {identity(action.field) for action in prior if action.value is True}
        current = {identity(peer) for peer in peers}
        prior_by_identity = {identity(action.field): action for action in prior}
        checkbox_reuse[group] = (
            selected.issubset(current)
            and (bool(selected) or not any(peer.required for peer in peers))
            and all(
                current_counts[identity(action.field)] == 1
                and reusable_answer(prior_by_identity.get(identity(action.field), prior[0]), action)
                for action in proposed
            )
        )
        if checkbox_reuse[group]:
            for action in proposed:
                if identity(action.field) not in prior_by_identity:
                    action.value = False
                    action.answer_basis, action.made_up = prior[0].answer_basis, prior[0].made_up
                    action.source, action.source_keys = prior[0].source, prior[0].source_keys.copy()
                    action.inference_reason = prior[0].inference_reason
    for action in actions:
        key = identity(action.field)
        prior = latest.get(key)
        can_reuse_group = checkbox_reuse.get(logical_key(action.field), True)
        if (
            can_reuse_group
            and current_counts[key] == 1
            and prior
            and reusable_answer(prior, action)
        ):
            action.value, action.choice_labels = prior.value, prior.choice_labels.copy()
            action.answer_basis, action.made_up = prior.answer_basis, prior.made_up
            action.source, action.source_keys = prior.source, prior.source_keys.copy()
            action.inference_reason = prior.inference_reason
            action.random_choice = prior.random_choice and not bool(prior.choice_labels)
    # A radio group has one desired selection. Superseded peer actions are no
    # longer verification targets when the effective plan selects another peer.
    by_id = {
        field_id(action.field): action
        for action in previous_actions or []
        if action.field.kind != "radio" or logical_key(action.field) not in radio_plans
    }
    by_id.update({field_id(action.field): action for action in actions})
    # Transfer upload attempts only for one unambiguous document mapping.
    # Never re-upload a failing/replaced widget on every planning pass.
    for action in actions:
        if action.field.kind != "file":
            continue
        peers = [
            a
            for a in actions
            if a.field.kind == "file"
            and (a.field.frame, a.source, a.value)
            == (action.field.frame, action.source, action.value)
        ]
        prior = [
            a
            for a in previous_actions or []
            if a.field.kind == "file"
            and (a.field.frame, a.source, a.value)
            == (action.field.frame, action.source, action.value)
        ]
        if len(peers) == len(prior) == 1:
            action.upload_attempted = prior[0].upload_attempted
            action.upload_error = prior[0].upload_error
            by_id.pop(field_id(prior[0].field), None)
            by_id[field_id(action.field)] = action
    failures = {}
    structural_checkpoint = None
    needs_replan = False
    visited = set()
    current_fields = list(assessed_fields or [])
    # Uploads precede country selection, phone entry, and the remaining fields.
    # Fresh checks account for autofill and country-dependent value changes.
    for action in sorted(actions, key=fill_priority):
        visited.add(field_id(action.field))
        check = (await verify_action_results(page, [action]))[0]
        if not check.present or (check.valid and not action.random_choice):
            continue
        if action.field.kind == "file":
            if not action.upload_attempted:
                try:
                    await fill(page, action)
                except ApplicationError as exc:
                    action.upload_error = action.upload_error or exc.code
                    if (
                        action.upload_attempted
                        and action.upload_error == Code.FIELD_FILL_FAILED
                        and not await page.evaluate("navigator.onLine")
                    ):
                        action.upload_error = Code.UPLOAD_UNVERIFIED
                # Even identical field shapes need replanning after autofill.
                structural_checkpoint = await settled_snapshot(page, polls=settle_polls)
                needs_replan = True
                break
            continue
        try:
            await fill(page, check.action)
        except ApplicationError as exc:
            failures[field_id(check.action.field)] = exc
            continue
        handler = handler_for(check.action.field)
        if handler and (handler.may_change_form or fill_priority(check.action) == 1):
            candidate = await settled_snapshot(page, polls=settle_polls)
            preserve_requirements(candidate.fields, assessed_fields or [])
            if current_fields and fields_changed(current_fields, candidate.fields):
                structural_checkpoint = candidate
                needs_replan = True
                break
            current_fields = candidate.fields
    after = structural_checkpoint or await settled_snapshot(page, polls=settle_polls)
    preserve_requirements(after.fields, assessed_fields or [])
    observed = {field_id(field): field for field in after.fields}
    outcomes = []
    for check in await verify_action_results(page, list(by_id.values())):
        key = field_id(check.action.field)
        failure = failures.get(key)
        current = observed.get(key)
        code = "FILLED" if check.valid else (failure.code if failure else check.code)
        message = str(failure) if failure else check.message
        if check.action.upload_error:
            code = check.action.upload_error
            message = "Document upload was not verified; manual field filling may still complete."
        elif not check.valid and needs_replan and key not in visited:
            code = Code.DEFERRED
            message = "Waiting for a fresh plan after the form changed; fill not attempted."
        outcomes.append(
            FieldExecution(
                action=check.action,
                present=check.present,
                verified=check.valid,
                code=str(code or Code.VALIDATION_FAILED),
                message=message,
                selected=current.filled
                if current and current.kind in ("radio", "checkbox")
                else None,
            )
        )
    return ExecutionResult(after, outcomes, needs_replan=needs_replan)
