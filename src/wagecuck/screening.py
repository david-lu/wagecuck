"""Screening questions use explicit declarations, never inferred personal attributes."""

import re
from dataclasses import dataclass

from .browser import normalize
from .models import Action, Option


@dataclass
class Answer:
    value: str | bool
    source: str
    labels: list[str]


DECLINE = [
    "Prefer not to say",
    "I don't wish to answer",
    "I do not wish to answer",
    "Decline to self-identify",
    "I choose not to disclose",
    "Prefer not to disclose",
]
NO_DISABILITY = [
    "No",
    "No, I do not have a disability",
    "No, I don't have a disability",
    "I do not have a disability",
    "Not disabled",
]
NO_DISABILITY_HISTORY = [
    "No, I do not have a disability and have not had one in the past",
    "No, I don't have a disability, or a history/record of having a disability",
    "No, I don't have a disability and have not had one in the past",
]
NO_VETERAN = [
    "No",
    "I am not a veteran",
    "Not a veteran",
    "I am not a protected veteran",
    "Not a protected veteran",
    "I do not identify as a protected veteran",
    "I am not a veteran of the U.S. military",
]


def country_for(question, screening):
    countries = {
        "US": r"\bunited states\b|\bu s(?: a)?\b|\busa?\b",
        "CA": r"\bcanada\b",
        "GB": r"\bunited kingdom\b|\buk\b",
    }
    matched = [key for key, pattern in countries.items() if re.search(pattern, question)]
    if len(matched) == 1:
        return matched[0]
    jurisdiction_text = re.sub(r"\bin (?:the )?future\b", "", question)
    if matched or re.search(r"\b(?:in|within)\b", jurisdiction_text):
        return None  # Unrecognized or multiple jurisdictions must not inherit US answers.
    return screening.default_work_country


def answer_for(label, screening):
    q = normalize(label)
    if re.search(
        r"consent|agree|certif|privacy|signature|accommodat|adjustment|medical condition|describe|explain",
        q,
    ):
        return None
    if re.search(r"\bdisabilit(?:y|ies)\b|\bdisabled\b", q) and not re.search(
        r"veteran|name|date|when|which|type of", q
    ):
        state = screening.disability_status
        if state is None:
            return None
        if state == "decline":
            return Answer("I do not wish to answer", "disability_status", DECLINE)
        if re.search(r"ever|history|past|previous", q):
            if screening.disability_history is None:
                return None
            yes = state == "disability" or screening.disability_history
        else:
            yes = state == "disability"
        labels = (
            ["Yes", "Yes, I have a disability", "I have a disability"]
            if yes
            else NO_DISABILITY.copy()
        )
        if not yes and screening.disability_history is False:
            labels += NO_DISABILITY_HISTORY
        return Answer(yes, "disability_status", labels)
    if re.search(r"\bveteran\b|\bveterans\b", q) and not re.search(
        r"which|when|date|branch|disab|combat|recently|wartime|medal", q
    ):
        state = screening.veteran_status
        if state is None:
            return None
        if state == "decline":
            return Answer(
                "Decline to self-identify",
                "veteran_status",
                DECLINE
                + [
                    "I don't wish to answer",
                    "I decline to self-identify for protected veteran status",
                ],
            )
        if state == "not_protected_veteran" and "protected" not in q:
            return None  # Non-protected veterans are not necessarily non-veterans.
        yes = state == "protected_veteran"
        labels = (
            [
                "Yes",
                "I identify as one or more of the classifications of protected veteran listed above",
                "I am a protected veteran",
            ]
            if yes
            else NO_VETERAN
        )
        return Answer(yes, "veteran_status", labels)
    country = country_for(q, screening)
    auth = screening.work_authorization.get(country)
    if auth is None:
        return None
    if auth.description and "work permit" in q and re.search(r"list|type|status", q):
        return Answer(
            auth.description, f"work_authorization.{country}.description", [auth.description]
        )
    if re.search(r"sponsor", q):
        if not re.search(r"need|require|sponsorship status", q) or re.search(
            r"without|not require|don t require|h 1b|h1b|citizen|permanent", q
        ):
            return None
        if auth.requires_sponsorship is None:
            return None
        value = auth.requires_sponsorship
        labels = (
            ["Yes", "Yes, I require sponsorship"]
            if value
            else [
                "No",
                "No, I do not require sponsorship",
                "No sponsorship required",
                "I do not require sponsorship",
            ]
        )
        return Answer(value, f"work_authorization.{country}.requires_sponsorship", labels)
    if re.search(r"visa (?:type|status)|immigration status|current visa|type of visa", q):
        if not auth.visa_type:
            return None
        labels = [auth.visa_type, f"{auth.visa_type} visa", f"{auth.visa_type} status"]
        if auth.visa_type.upper() == "TN":
            labels += [
                "TN (NAFTA)",
                "TN - NAFTA Professional",
                "TN visa (Canadian/Mexican professionals)",
            ]
        return Answer(auth.visa_type, f"work_authorization.{country}.visa_type", labels)
    if re.search(r"authoriz|eligible|right to work|legally allowed", q) and re.search(
        r"work|employ", q
    ):
        if re.search(
            r"citizen|permanent|unrestricted|without|not authorized|not eligible|unlimited|any employer",
            q,
        ):
            return None
        if auth.authorized is None:
            return None
        value = auth.authorized
        labels = (
            ["Yes", "Yes, I am legally authorized to work", "I am authorized to work"]
            if value
            else ["No", "No, I am not legally authorized to work"]
        )
        if country == "US" and value:
            labels += [
                "Yes, I am legally authorized to work in the United States",
                "Yes, I am authorized to work in the U.S.",
            ]
        if country == "CA" and value:
            labels += [
                "Yes, I am legally authorized to work in Canada",
                "Yes, I am authorized to work in Canada",
            ]
        return Answer(value, f"work_authorization.{country}.authorized", labels)
    return None


def pick_option(options, labels):
    allowed = {normalize(label) for label in labels}
    matches = [option for option in options if normalize(option.label) in allowed]
    return matches[0] if len(matches) == 1 else None


def plan_screening(field, fields, profile):
    """Return (handled, action). Non-selected members of a resolved radio group are handled."""
    label = field.group if field.kind == "radio" else field.label
    answer = answer_for(label, profile.screening)
    if answer is None:
        return False, None
    return plan_answer(field, fields, answer)


def plan_answer(field, fields, answer):
    """Render a declared value into the actual choice/control without guessing option IDs."""
    value = answer.value
    if field.kind == "radio":
        if not field.group:
            return False, None
        peers = [
            f
            for f in fields
            if f.kind == "radio"
            and f.frame == field.frame
            and f.name == field.name
            and f.group == field.group
        ]
        option = pick_option([Option(label=f.label, value=f.id) for f in peers], answer.labels)
        if option is None:
            return False, None
        if field.id != option.value:
            return True, None
        value = True
    elif field.kind == "select":
        option = pick_option(field.options, answer.labels)
        if option is None:
            return False, None
        value = option.value
    elif field.kind == "checkbox":
        if not isinstance(value, bool):
            return False, None
    elif field.kind not in ("text", "textarea", "combobox"):
        return False, None
    return True, Action(
        field=field, value=value, source=f"screening:{answer.source}", choice_labels=answer.labels
    )
