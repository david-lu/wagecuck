"""Lossless phone representations from country metadata, never guessed prefixes."""

import re

import phonenumbers


def parse_phone(value, region=None):
    try:
        return phonenumbers.parse(value, region)
    except phonenumbers.NumberParseException:
        return None


def digits(value):
    return re.sub(r"\D", "", value)


def phone_candidates(value, region=None):
    number = parse_phone(value, region)
    # Keep extensions and unparseable national numbers intact rather than losing digits.
    if number is None:
        return (
            list(dict.fromkeys([digits(value), value]))
            if re.fullmatch(r"[+\d\s().-]+", value)
            else [value]
        )
    if number.extension:
        return [value]
    national = digits(phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.NATIONAL))
    international = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return list(dict.fromkeys([national, international, digits(international), value]))


def phone_equivalent(actual, expected, region=None, dial_code=""):
    number = parse_phone(expected, region)
    if number and dial_code and digits(dial_code) != str(number.country_code):
        return False
    if number and number.extension:
        other = parse_phone(
            actual, region or phonenumbers.region_code_for_country_code(number.country_code)
        )
        return other == number
    if digits(actual) and digits(actual) == digits(expected):
        return True
    if number is None:
        return False
    if region is None:
        region = phonenumbers.region_code_for_number(
            number
        ) or phonenumbers.region_code_for_country_code(number.country_code)
    other = parse_phone(actual, region)
    return other is not None and (
        phonenumbers.format_number(other, phonenumbers.PhoneNumberFormat.E164)
        == phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    )
