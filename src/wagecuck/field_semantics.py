"""Deterministic label normalization and profile-field recognition."""

import re

from .browser import normalize
from .field_values import field_contract
from .logical_fields import logical_groups, logical_question, option_label
from .models import FormField

ALIASES = {
    "first_name": ["first name", "given name", "firstname"],
    "last_name": ["last name", "family name", "surname", "lastname"],
    "full_name": ["full name", "name", "your name", "legal name"],
    "email": ["email", "email address", "e mail"],
    "phone": ["phone", "phone number", "mobile phone", "mobile", "telephone"],
    "city": [
        "city",
        "town",
        "location city",
        "current city",
        "current location",
        "location",
        "which city are you currently located in",
    ],
    "country": ["country", "country of residence"],
    "state": ["state", "province", "province state", "state province"],
    "postal_code": ["postal code", "postcode", "zip", "zip code", "zip postal code"],
    "address": ["address"],
    "address_line1": ["address line 1", "street address line 1", "street number and name"],
    "address_line2": ["address line 2", "apartment suite", "apartment suite unit", "unit number"],
    "street_address": ["street address"],
    "full_address": [
        "full address",
        "complete address",
        "mailing address",
        "full mailing address",
        "postal address",
    ],
    "linkedin": [
        "linkedin",
        "linkedin profile",
        "linkedin url",
        "linkedin profile url",
        "linkedin link profile",
    ],
    "twitter": ["twitter", "twitter url", "x profile"],
    "github": ["github", "github url", "github profile"],
    "website": [
        "website",
        "portfolio",
        "personal website",
        "portfolio url",
        "website url",
        "website blog or portfolio",
        "other website",
    ],
    "current_company": ["current company", "company", "current employer"],
    "current_title": ["current title", "current job title"],
    "source": [
        "how did you hear about us",
        "how did you hear about this job",
        "how did you hear about this opportunity",
    ],
}

AUTOCOMPLETE = {
    "given-name": "first_name",
    "family-name": "last_name",
    "name": "full_name",
    "email": "email",
    "tel": "phone",
    "address-level2": "city",
    "address-level1": "state",
    "postal-code": "postal_code",
    "country-name": "country",
    "street-address": "street_address",
    "address-line1": "address_line1",
    "address-line2": "address_line2",
}

PROFILE_FIELD_DESCRIPTIONS = {
    "full_name": "Composite: first name followed by last name.",
    "address": "Legacy alias for the first street address line only.",
    "street_address": "Composite: street address lines 1 and 2; excludes city, region and country.",
    "full_address": "Composite: street lines, city, state/province, postal code and country on one line.",
    "full_address_multiline": "Composite: full postal address with line breaks, suitable for a textarea.",
    "location": "Composite: city, state/province and country; excludes street and postal code.",
    "current_company": "Company of the single employment entry explicitly marked current.",
    "current_title": "Title of the single employment entry explicitly marked current.",
    "authorized_canada": "Explicit permission to work in Canada; alias of screening.work_authorization.CA.authorized. Not citizenship or US authorization.",
    "authorized_us": "Explicit permission to work in the US; alias of screening.work_authorization.US.authorized. Not citizenship or unrestricted permission.",
    "authorized_uk": "Explicit permission to work in the UK; not permission in another country.",
    "availability": "Declared start availability, including notice period if supplied.",
    "notice_period": "Declared notice period with the unit days; not years or a date.",
    "compensation_expectations": "Declared pay expectations with currency and period. Never current salary or a converted currency.",
    "work_history": "Composite: all supplied employment records, dates, locations and summaries.",
}

SENSITIVE = re.compile(
    r"consent|agree|certif|privacy|terms|sponsor|authoriz|eligib|right to work|legally allowed|permission|permitted|entitled|visa|immigration|work permit|citizen|gender|pronoun|race|racial|ethnic|hispanic|latin(?:o|a|x|e)|sexual orientation|sexuality|transgender|veteran|disab|medical|health|salary|compensation|desired pay|criminal|felony|background|relocat|password|secret|token|passport|social security",
    re.IGNORECASE,
)


def question(text: str) -> str:
    text = re.sub(r"[\s*✱]+$", "", text)
    text = re.sub(
        r"\s*[\[(]?(?:not required|optional|required)[\])]?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*\(if any\)\s*$", "", text, flags=re.IGNORECASE)
    return normalize(re.sub(r"^\s*\(required\)\s*", "", text, flags=re.IGNORECASE))


def fact_key(field: FormField) -> str | None:
    label = question(field.label)
    if field.kind == "tel":
        label = re.sub(r"\s+\d{1,4}$", "", label)
    candidates = [label, re.sub(r"^(?:your|please (?:enter|provide))\s+", "", label)]
    if not label or label == question(field.name):
        name = re.sub(r"([a-z])([A-Z])", r"\1 \2", field.name)
        name = normalize(re.sub(r"[_\[\].-]+", " ", name))
        candidates.append(re.sub(r"^(?:candidate|applicant|application|personal)\s+", "", name))
    keys = {key for key, aliases in ALIASES.items() if any(c in aliases for c in candidates)}
    if len(keys) == 1:
        return keys.pop()
    if field.fact_key:
        return field.fact_key
    return AUTOCOMPLETE.get(field.autocomplete.split()[-1] if field.autocomplete else "")


def field_metadata(field: FormField, peers: list[FormField] | None = None) -> dict:
    peers = peers or [field]
    question_text = logical_question(peers)
    return {
        "field_id": f"{field.frame}:{field.id}",
        "label": field.label,
        "group": field.group,
        "logical_question": question_text,
        "group_options": [
            {
                "field_id": f"{peer.frame}:{peer.id}",
                "label": option_label(peer, question_text),
            }
            for peer in peers
        ]
        if len(peers) > 1 and field.kind in ("radio", "checkbox")
        else [],
        "name": field.name,
        "autocomplete": field.autocomplete,
        "kind": field.kind,
        "value_contract": field_contract(field),
        "context": field.context,
        "required": field.required,
        "requirement_status": field.requirement_status,
        "options": [option.model_dump() for option in field.options],
    }


def fields_metadata(fields: list[FormField]) -> list[dict]:
    return [field_metadata(field, group) for group in logical_groups(fields) for field in group]
