"""Keep planned values canonical and render them at the browser boundary."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation

from .models import FormField


class FieldValueError(ValueError):
    pass


def value_contract(field: FormField) -> str:
    """Return the semantic value type expected by a control."""
    if field.kind == "range":
        return "number"
    if field.kind in (
        "checkbox",
        "radio",
        "number",
        "date",
        "datetime-local",
        "month",
        "week",
        "time",
        "email",
        "tel",
        "url",
        "file",
    ):
        return field.kind
    text = _normalize(f"{field.label} {field.placeholder}")
    if field.kind in ("text", "search"):
        if _date_format(field) or re.fullmatch(r"date available", _normalize(field.label)):
            return "date"
        if field.input_mode in ("numeric", "decimal") or re.search(
            r"\bdesired (?:annual )?(?:salary|pay)\b", text
        ):
            return "number"
    return field.kind


def field_contract(field: FormField) -> dict:
    """Small provider/report representation of the control's value constraints."""
    return {
        "type": value_contract(field),
        "format": _date_format(field) or "",
        "placeholder": field.placeholder,
        "input_mode": field.input_mode,
        "pattern": field.pattern,
        "minimum": field.minimum,
        "maximum": field.maximum,
        "step": field.step,
        "min_length": field.min_length,
        "max_length": field.max_length,
    }


def normalize_field_value(field: FormField, value: str | bool) -> str | bool:
    """Validate and return a stable control-independent value.

    Dates stay ISO throughout planning and inference. Display constraints are
    checked against rendered text, which is never stored back into the action.
    """
    contract = value_contract(field)
    if contract in ("checkbox", "radio"):
        if not isinstance(value, bool):
            raise FieldValueError("Checkbox and radio controls require a boolean value.")
        return value
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    string = str(value).strip()
    if not string:
        raise FieldValueError("The control requires a nonempty value.")
    if contract == "number":
        string = _number_value(string, field)
    elif contract == "date":
        parsed = _date_value(string, field)
        _check_date_bounds(parsed, field)
        string = parsed.isoformat()
    elif contract == "datetime-local":
        string = _datetime_local_value(string)
    elif contract == "month":
        string = _month_value(string)
    elif contract == "week":
        string = _week_value(string)
    elif contract == "time":
        string = _time_value(string)
    elif contract == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", string):
        raise FieldValueError("The value is not an email address.")
    elif contract == "url" and not re.match(r"https?://[^\s]+$", string, re.IGNORECASE):
        raise FieldValueError("The value is not an HTTP(S) URL.")
    rendered = _render_canonical(field, string)
    if field.min_length is not None and len(rendered) < field.min_length:
        raise FieldValueError("The value is shorter than the control's minimum length.")
    if field.max_length is not None and len(rendered) > field.max_length:
        raise FieldValueError("The value exceeds the control's maximum length.")
    if field.pattern:
        try:
            if not re.fullmatch(field.pattern, rendered):
                raise FieldValueError("The value does not match the control's required pattern.")
        except re.error:
            pass  # JavaScript regex syntax is not always compatible with Python.
    return string


def render_field_value(field: FormField, value: str | bool) -> str | bool:
    """Render an already-normalized value at the browser boundary.

    Call normalize_field_value for raw profile/model input first. Keeping this
    operation separate prevents displayed dates from being parsed a second time.
    """
    return value if isinstance(value, bool) else _render_canonical(field, value)


def _render_canonical(field: FormField, value: str) -> str:
    if value_contract(field) == "date":
        return _format_date(date.fromisoformat(value), field)
    return value


def _number_value(value: str, field: FormField) -> str:
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise FieldValueError("The control requires a numeric value.") from exc
    if not number.is_finite():
        raise FieldValueError("The control requires a finite numeric value.")
    minimum = field.minimum or ("0" if field.kind == "range" else "")
    maximum = field.maximum or ("100" if field.kind == "range" else "")
    for boundary, compare, message in (
        (minimum, lambda actual, limit: actual < limit, "below the minimum"),
        (maximum, lambda actual, limit: actual > limit, "above the maximum"),
    ):
        if boundary:
            try:
                if compare(number, Decimal(boundary)):
                    raise FieldValueError(f"The numeric value is {message}.")
            except InvalidOperation:
                pass
    step_text = field.step or ("1" if field.kind in ("number", "range") else "")
    if step_text and step_text.casefold() != "any":
        try:
            step = Decimal(step_text)
            base = Decimal(field.minimum or "0")
            if step <= 0 or (number - base) % step:
                raise FieldValueError("The numeric value does not satisfy the control's step.")
        except InvalidOperation:
            pass
    if not number:
        return "0"
    rendered = format(number, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _date_value(value: str, field: FormField) -> date:
    # ISO is canonical. An explicit placeholder disambiguates non-ISO values.
    formats = ["%Y-%m-%d"]
    placeholder = _placeholder_date_format(field)
    if placeholder:
        formats.append(placeholder)
    else:
        formats.extend(("%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y/%m/%d"))
    for format_string in formats:
        try:
            return datetime.strptime(value, format_string).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    raise FieldValueError("The control requires a calendar date.")


def _check_date_bounds(value: date, field: FormField) -> None:
    for boundary, compare, message in (
        (field.minimum, lambda actual, limit: actual < limit, "before the minimum"),
        (field.maximum, lambda actual, limit: actual > limit, "after the maximum"),
    ):
        if not boundary:
            continue
        try:
            limit = date.fromisoformat(boundary)
        except ValueError:
            continue
        if compare(value, limit):
            raise FieldValueError(f"The date is {message}.")


def _datetime_local_value(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FieldValueError("The control requires a local date and time.") from exc
    if parsed.tzinfo is not None:
        raise FieldValueError("The control requires a local date and time without a timezone.")
    timespec = "minutes" if not parsed.second and not parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec)


def _month_value(value: str) -> str:
    if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value):
        raise FieldValueError("The control requires a month in YYYY-MM format.")
    return value


def _week_value(value: str) -> str:
    match = re.fullmatch(r"(\d{4})-W(\d{2})", value)
    if not match:
        raise FieldValueError("The control requires an ISO week in YYYY-Www format.")
    try:
        date.fromisocalendar(int(match[1]), int(match[2]), 1)
    except ValueError as exc:
        raise FieldValueError("The control requires a real ISO calendar week.") from exc
    return value


def _time_value(value: str) -> str:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise FieldValueError("The control requires a clock time.") from exc
    if parsed.tzinfo is not None:
        raise FieldValueError("The control requires a local clock time without a timezone.")
    timespec = "minutes" if not parsed.second and not parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec)


def _date_format(field: FormField) -> str | None:
    pattern = _placeholder_date_format(field)
    return (
        {"%m": "MDY", "%d": "DMY", "%Y": "YMD", "%y": "YMD"}.get(pattern[:2]) if pattern else None
    )


def _format_date(value: date, field: FormField) -> str:
    # Native date inputs accept ISO regardless of the displayed placeholder.
    pattern = _placeholder_date_format(field) if field.kind != "date" else None
    return value.strftime(pattern) if pattern else value.isoformat()


def _placeholder_date_format(field: FormField) -> str | None:
    match = re.search(
        r"(?:m{1,2}[/.-]d{1,2}[/.-]y{2,4}|d{1,2}[/.-]m{1,2}[/.-]y{2,4}|y{2,4}[/.-]m{1,2}[/.-]d{1,2})",
        field.placeholder.casefold().replace(" ", ""),
    )
    if not match:
        return None
    return re.sub(
        r"y+|m+|d+",
        lambda token: "%y" if token[0] == "yy" else {"y": "%Y", "m": "%m", "d": "%d"}[token[0][0]],
        match[0],
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()
