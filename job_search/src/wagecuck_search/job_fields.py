"""Field types, extraction definitions, and deterministic numeric predicates."""

import math
import operator
import re
from decimal import Decimal

from .array_fields import validate_name

FIELD_TYPES = ("number_field", "array_field", "string_field", "boolean_field")
TYPE_ALIASES = {"number": "number_field", "partial": "array_field", "array": "array_field",
                "string": "string_field", "enum": "string_field", "boolean": "boolean_field"}
NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
COMPARISON = re.compile(r"^\s*(<=|>=|==|!=|>|<|=)\s*(" + NUMBER + r")\s*$")
OPERATORS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
             "==": operator.eq, "!=": operator.ne}
NUMERIC_FIELDS = {"salary.minimum", "salary.maximum", "salary_minimum", "salary_maximum"}
BOOLEAN_FIELDS = {"internship", "sponsors_visa"}


def field_type_name(kind):
    kind = TYPE_ALIASES.get(kind, kind) if isinstance(kind, str) else None
    if kind not in FIELD_TYPES:
        raise ValueError("Field type must be " + ", ".join(FIELD_TYPES))
    return kind


def validate_filter_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", name):
        raise ValueError("Filter fields must be identifiers or dotted field paths")


def normalize_field_types(values):
    if not isinstance(values, dict):
        raise ValueError("field_types must map names to field types")
    result = {}
    for name, kind in values.items():
        validate_filter_name(name)
        result[name] = field_type_name(kind)
        if name in NUMERIC_FIELDS and result[name] != "number_field":
            raise ValueError(f"{name} is a number_field")
        if name in BOOLEAN_FIELDS and result[name] != "boolean_field":
            raise ValueError(f"{name} is a boolean_field")
        if name == "location" and result[name] != "array_field":
            raise ValueError("location is an array_field")
    return result


def value_type(value):
    if isinstance(value, bool):
        return "boolean_field"
    if isinstance(value, (int, float)):
        return "number_field"
    if isinstance(value, (list, tuple)):
        return "array_field"
    return "string_field" if value is not None else None


def job_field_types(job):
    result = normalize_field_types(job.get("field_types") or {})
    for name in job.get("array_fields") or {}:
        if name in result and result[name] != "array_field":
            raise ValueError(f"{name}: declared type conflicts with its array value")
        result[name] = "array_field"
    for name, value in (job.get("fields") or {}).items():
        actual = value_type(value)
        declared = result.get(name)
        if declared == "array_field" or actual and declared and actual != declared:
            raise ValueError(f"{name}: value does not match its declared {declared} type")
        if actual is not None:
            result.setdefault(name, actual)
    return result


def field_type(jobs, name):
    if name == "location":
        return "array_field"
    if name in NUMERIC_FIELDS:
        return "number_field"
    if name in BOOLEAN_FIELDS:
        return "boolean_field"
    kinds = set()
    for job in jobs:
        kind = job_field_types(job).get(name)
        if kind is None:
            value = job
            for part in name.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            kind = value_type(value)
        # A null without declared metadata doesn't determine the column type.
        if (kind == "string_field" and name not in (job.get("field_types") or {})
                and name in (job.get("fields") or {}) and job["fields"][name] is None):
            kind = None
        if kind:
            kinds.add(kind)
    if len(kinds) > 1:
        raise ValueError(f"{name}: inconsistent field types across rows: {', '.join(sorted(kinds))}")
    return next(iter(kinds), "array_field" if name == "location" else "string_field")


def normalize_scalars(values):
    if not isinstance(values, dict):
        raise ValueError("fields must be an object of scalar values")
    for name, value in values.items():
        validate_name(name)
        if name == "location":
            raise ValueError("location is an array field")
        if value is not None and not isinstance(value, (str, bool, int, float)):
            raise ValueError(f"{name}: ordinary fields must be scalar")
        if isinstance(value, (float, int)) and not isinstance(value, bool) and not math.isfinite(value):
            raise ValueError(f"{name}: numbers must be finite")
    return dict(values)


def merge_scalars(first, second):
    result = dict(first or {})
    for name, value in (second or {}).items():
        if name not in result:
            result[name] = value
        elif result[name] != value:
            result[name] = None
    return result


def merge_field_types(first, second):
    result = dict(first or {})
    for name, kind in (second or {}).items():
        if name in result and result[name] != kind:
            raise ValueError(f"{name}: cannot merge conflicting field types")
        result[name] = kind
    return result


def extraction_definitions(value):
    if not isinstance(value, dict):
        raise ValueError("field definitions must be an object")
    result = {}
    for name, definition in value.items():
        validate_name(name)
        if not isinstance(definition, dict) or set(definition) - {"type", "prompt", "options"}:
            raise ValueError(f"{name}: use type, prompt, and optional string options")
        kind = field_type_name(definition.get("type"))
        if name == "location" and kind != "array_field":
            raise ValueError("location is an array_field")
        if not isinstance(definition.get("prompt"), str) or not definition["prompt"].strip():
            raise ValueError(f"{name}: a nonempty extraction prompt is required")
        options = definition.get("options")
        if options is not None or definition.get("type") == "enum":
            if (kind != "string_field" or not isinstance(options, list) or not options
                    or any(not isinstance(v, str) or not v for v in options)
                    or len(set(options)) != len(options)):
                raise ValueError(f"{name}: options must be unique strings on a string_field")
        result[name] = {**definition, "type": kind}
    return result


def number_value(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Expected a finite number")
    if isinstance(value, str) and not re.fullmatch(NUMBER, value.strip()):
        raise ValueError(f"Invalid number: {value!r}")
    result = Decimal(str(value).strip())
    if not result.is_finite() or not math.isfinite(float(result)):
        raise ValueError("Numbers must be finite")
    return result


def parse_comparison(expression):
    if not isinstance(expression, str) or not (match := COMPARISON.fullmatch(expression)):
        raise ValueError("Use a numeric comparison such as >200, <=400, ==0, or !=10")
    # Validate magnitude/finite values without evaluating arbitrary expressions.
    number_value(match[2])
    return {"operator": "==" if match[1] == "=" else match[1], "value": match[2]}


def is_comparison(expression):
    return isinstance(expression, str) and expression.lstrip().startswith((">", "<", "=", "!"))


def filter_definitions(value):
    if not isinstance(value, dict):
        raise ValueError("field_filters must be an object")
    result = {}
    for name, spec in value.items():
        validate_filter_name(name)
        if isinstance(spec, str):
            spec = {"comparisons": [spec]} if is_comparison(spec) else {"prompt": spec}
        if isinstance(spec, list):
            spec = {"comparisons": spec}
        if isinstance(spec, dict) and "comparisons" in spec:
            if set(spec) != {"comparisons"} or not isinstance(spec["comparisons"], list) or not spec["comparisons"]:
                raise ValueError(f"{name}: comparisons must be a nonempty list")
            comparisons = []
            for expression in spec["comparisons"]:
                if isinstance(expression, dict) and set(expression) == {"operator", "value"}:
                    expression = str(expression["operator"]) + str(expression["value"])
                comparisons.append(parse_comparison(expression))
            result[name] = {"comparisons": comparisons}
            continue
        if not isinstance(spec, dict) or set(spec) - {"prompt", "mode"}:
            raise ValueError(f"{name}: use prompt/mode or numeric comparisons")
        if not isinstance(spec.get("prompt"), str) or not spec["prompt"].strip():
            raise ValueError(f"{name}: a nonempty filter prompt is required")
        mode = spec.get("mode", "any")
        if mode not in ("any", "all", "none"):
            raise ValueError(f"{name}: filter mode must be any, all, or none")
        result[name] = {"prompt": spec["prompt"], "mode": mode}
    return result


def validate_filter_types(jobs, predicates, declared_types=None):
    declared = normalize_field_types(declared_types or {})
    result = {}
    for name, spec in predicates.items():
        kind = declared.get(name) or field_type(jobs, name)
        if kind == "number_field" and "comparisons" not in spec:
            raise ValueError(f"{name} is a number_field; use comparisons like >200 or <=400, not a prompt")
        if kind != "number_field" and "comparisons" in spec:
            raise ValueError(f"{name} is a {kind}; numeric comparisons require a number_field")
        if kind != "array_field" and spec.get("mode", "any") != "any":
            raise ValueError(f"{name} is a {kind}; all/none matching is only valid for array_field")
        result[name] = kind
    return result


def matches_number(value, comparisons):
    if value is None or value == "":
        return None
    value = number_value(value)
    return all(OPERATORS[comparison["operator"]](value, number_value(comparison["value"]))
               for comparison in comparisons)


def aggregate(decisions, mode):
    """Three-valued logic: unknown cannot make an all/none predicate pass."""
    if not decisions:
        return None
    if mode == "all":
        return False if False in decisions else None if None in decisions else True
    any_match = True if True in decisions else None if None in decisions else False
    return (not any_match) if mode == "none" and any_match is not None else any_match


def scalar_from_csv(value, reference=None, kind=None):
    if not value.strip():
        return None
    if kind == "string_field":
        return value
    if kind == "boolean_field" or isinstance(reference, bool) or (kind is None and value.casefold() in ("true", "false")):
        if value.casefold() not in ("true", "false"):
            raise ValueError("Boolean CSV fields must be true or false")
        return value.casefold() == "true"
    if (kind == "number_field" or type(reference) in (float, int)
            or kind is None and reference is None and re.fullmatch(NUMBER, value.strip())):
        parsed = number_value(value)
        return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)
    return value
