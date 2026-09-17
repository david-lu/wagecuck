"""Field roles shared by ordering and control dispatch; no site-specific rules."""

import re


def is_country_field(field):
    autocomplete = field.autocomplete.split()[-1:]
    label = re.sub(r"[^a-z]+", " ", field.label.casefold()).strip()
    return (
        autocomplete in (["country"], ["country-name"], ["tel-country-code"])
        or field.fact_key in ("country", "address.country")
        or bool(re.search(r"\bcountry\b|\b(?:dial|dialing|dialling|calling) code\b", label))
        and field.kind in ("select", "combobox", "radio", "text")
    )


def is_phone_field(field):
    autocomplete = field.autocomplete.split()[-1:]
    if autocomplete in (["tel-country-code"], ["tel-extension"]):
        return False
    label = re.sub(r"[^a-z]+", " ", field.label.casefold()).strip()
    if re.search(r"country|dial(?:ing|ling)? code|calling code|extension", label):
        return False
    return (
        field.kind == "tel"
        or field.control_type == "text_input"
        and (
            autocomplete in (["tel"], ["tel-national"])
            or field.input_mode == "tel"
            or field.fact_key == "phone"
            or bool(
                re.fullmatch(
                    r"(?:your )?(?:mobile|cell|telephone|phone)(?: phone)?(?: number)?", label
                )
            )
        )
    )


def fill_priority(action):
    field = action.field
    if field.kind == "file":
        return 0
    mapping, _, keys = action.source.partition(":")
    mapped_keys = set(keys.split("+")) if mapping in ("facts", "agent") else set()
    if is_country_field(field) or mapped_keys and mapped_keys <= {"country", "address.country"}:
        return 1
    if is_phone_field(field):
        return 2
    return 3
