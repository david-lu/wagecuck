from __future__ import annotations

from wagecuck.models import FormField

from .base import ControlHandler
from .choice import matches_choice, write_choice
from .combobox import matches_combobox, write_combobox
from .native import (
    matches_file,
    matches_number,
    matches_select,
    matches_text,
    write_file,
    write_range,
    write_select,
    write_text,
)


def handler_for(field: FormField) -> ControlHandler | None:
    """Resolve interaction behavior independently from the field's value contract."""
    if field.control_type == "file_upload":
        return ControlHandler(write_file, matches_file, may_change_form=True)
    if field.control_type == "native_select":
        return ControlHandler(write_select, matches_select, may_change_form=True)
    if field.control_type == "dynamic_combobox":
        return ControlHandler(
            write_combobox,
            matches_combobox,
            validates_natively=False,
            may_change_form=True,
        )
    if field.control_type in ("checkbox", "radio"):
        return ControlHandler(
            write_choice,
            matches_choice,
            validates_natively=False,
            may_change_form=True,
        )
    if field.control_type == "range":
        return ControlHandler(write_range, matches_number)
    if field.control_type == "text_input":
        return ControlHandler(
            write_text,
            matches_number if field.kind == "number" else matches_text,
        )
    return None