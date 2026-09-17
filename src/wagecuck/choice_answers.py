"""Choice compatibility and the last resort after model inference."""

import random
import re

from .controls.base import normalize
from .controls.combobox import option_name
from .controls.native import match_option
from .field_values import FieldValueError
from .logical_fields import logical_groups, logical_key
from .models import Action


def available_options(field):
    """Exclude prompts and choices whose value/label cannot be selected uniquely."""
    options = [
        option
        for option in field.options
        if option.value.strip()
        and option.label.strip()
        and not re.fullmatch(
            r"(?:please )?(?:select|choose)(?: (?:an? |your )?[^?]*)?", normalize(option.label)
        )
    ]
    return [
        option
        for option in options
        if match_option(option.value if field.kind == "select" else option.label, options)
        is not None
    ]


def validate_choice(action):
    """A semantic mapping is useful only if the widget can represent its answer."""
    field = action.field
    if field.kind not in ("select", "combobox"):
        return
    if field.kind == "combobox" and not field.options:
        return  # Searchable widgets may require typing before options load.
    options = available_options(field)
    option = match_option(action.value, options)
    if field.kind == "combobox":
        # Use the same aliases and country/dial-code matching as the browser writer.
        name = option_name(str(action.value), action)
        matches = [
            candidate
            for candidate in options
            if (
                name.fullmatch(candidate.label)
                if isinstance(name, re.Pattern)
                else normalize(name) == normalize(candidate.label)
            )
        ]
        if len(matches) == 1:
            option = matches[0]
    if option is None:
        raise FieldValueError("The mapped answer does not identify an available choice.")
    if field.kind == "combobox":
        action.value = option.label
        action.choice_labels = [option.label]


def random_unresolved_choices(fields, actions):
    """Called only after inference; never invent labels or partially overwrite groups."""
    planned_groups = {logical_key(action.field) for action in actions}
    generated = []
    resolved = set()
    for peers in logical_groups(fields):
        field = peers[0]
        kind = field.kind
        if logical_key(field) in planned_groups:
            continue
        values = []
        if kind in ("select", "combobox"):
            options = available_options(field)
            if not options:
                continue
            option = random.choice(options)
            values = [(field, option.value if kind == "select" else option.label)]
        elif kind in ("checkbox", "radio"):
            if kind == "checkbox" and len(peers) == 1:
                values = [(field, True if field.required else random.choice([True, False]))]
            else:
                chosen = random.choice(peers)
                values = [
                    (peer, peer is chosen) for peer in peers if kind == "checkbox" or peer is chosen
                ]
        else:
            continue
        for peer, value in values:
            generated.append(
                Action(
                    field=peer,
                    value=value,
                    source="random:unmatched_choice",
                    answer_basis="made_up",
                    made_up=True,
                    choice_labels=[value] if kind == "combobox" else [],
                    inference_reason=(
                        "Agent inference returned no usable answer for this choice question; "
                        "selected an available fallback at random."
                    ),
                )
            )
        resolved.update(f"{peer.frame}:{peer.id}" for peer in peers)
    return generated, resolved
