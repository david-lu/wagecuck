"""Screening questions use explicit declarations, never inferred personal attributes."""

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from .browser import normalize
from .logical_fields import group_members, logical_question, option_label
from .models import Action, Option


@dataclass
class Answer:
    value: str | bool
    source: str
    labels: list[str]
    selected_label_sets: list[list[str]] = dataclass_field(default_factory=list)


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
    "No, I do not have a disability, or have a history/record of having a disability",
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
    "No military service",
]

GENDER_LABELS = {
    "man": ["Man", "Male", "Cisgender man", "Man (including trans man)"],
    "woman": ["Woman", "Female", "Cisgender woman", "Woman (including trans woman)"],
    "non_binary": [
        "Non-binary",
        "Nonbinary",
        "Gender non-conforming",
        "Genderqueer",
        "Non-binary / gender non-conforming",
        "Non-binary (inclusive of bigender, agender, androgynous, gender fluid, and gender non-conforming)",
    ],
    "decline": DECLINE,
}
ORIENTATION_LABELS = {
    "heterosexual": ["Heterosexual", "Straight", "Heterosexual / straight"],
    "gay": ["Gay"],
    "lesbian": ["Lesbian"],
    "bisexual": ["Bisexual", "Bi", "Bisexual, pansexual, or queer"],
    "pansexual": ["Pansexual"],
    "asexual": ["Asexual"],
    "queer": ["Queer"],
    "decline": DECLINE,
}
RACE_LABELS = {
    "american_indian_alaska_native": [
        "American Indian or Alaska Native",
        "Indigenous",
        "Native American",
    ],
    "asian": ["Asian", "Asian (not Hispanic or Latino)"],
    "black_african_american": [
        "Black or African American",
        "Black or African American (not Hispanic or Latino)",
        "Black",
    ],
    "middle_eastern_north_african": [
        "Middle Eastern or North African",
        "Middle Eastern / North African",
        "MENA",
    ],
    "native_hawaiian_pacific_islander": [
        "Native Hawaiian or Other Pacific Islander",
        "Native Hawaiian or Pacific Islander",
    ],
    "white": ["White", "Caucasian"],
    "multiracial": [
        "Two or more races",
        "Multiracial",
        "Two or more races (not Hispanic or Latino)",
    ],
    "decline": DECLINE,
}


def _declared_choice(value, source, choices):
    if value is None:
        return None
    labels = choices[value]
    return Answer(labels[0], source, labels)


def _yes_no_or_decline(value, source, yes_labels, no_labels):
    if value is None:
        return None
    if value == "decline":
        return Answer(DECLINE[0], source, DECLINE)
    return Answer(bool(value), source, yes_labels if value else no_labels)


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
    demographics = screening.demographics
    if re.search(r"\btransgender\b|\btrans identity\b", q):
        return _yes_no_or_decline(
            demographics.transgender_status,
            "demographics.transgender_status",
            ["Yes", "Yes, I identify as transgender", "Transgender"],
            ["No", "No, I do not identify as transgender", "Not transgender"],
        )
    if re.search(r"\bgender(?: identity)?\b", q) and not re.search(
        r"pronoun|transgender|sex assigned|sexual orientation", q
    ):
        return _declared_choice(
            demographics.gender_identity, "demographics.gender_identity", GENDER_LABELS
        )
    if re.search(r"\bsexual orientation\b|\bsexuality\b", q):
        return _declared_choice(
            demographics.sexual_orientation,
            "demographics.sexual_orientation",
            ORIENTATION_LABELS,
        )
    if re.search(r"\bhispanic\b|\blatin(?:o|a|x|e)\b", q):
        return _yes_no_or_decline(
            demographics.hispanic_latino,
            "demographics.hispanic_latino",
            ["Yes", "Yes, Hispanic or Latino", "Hispanic or Latino", "Hispanic/Latino"],
            ["No", "No, not Hispanic or Latino", "Not Hispanic or Latino"],
        )
    if re.search(r"\brace\b|\bracial\b|\bethnicity\b|\bethnic origin\b", q):
        selected = demographics.race_ethnicity
        if not selected:
            return None
        label_sets = [RACE_LABELS[value].copy() for value in selected]
        if demographics.hispanic_latino is False and "white" in selected:
            label_sets[selected.index("white")].append("White (not Hispanic or Latino)")
        labels = [label for choices in label_sets for label in choices]
        return Answer(
            ", ".join(choices[0] for choices in label_sets),
            "demographics.race_ethnicity",
            labels,
            label_sets,
        )
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


def answer_for_field(field, screening):
    """Resolve a choice question from its group heading or bounded local context."""
    candidates = []
    if field.kind in ("radio", "checkbox") and field.group:
        candidates.append(field.group)
    candidates.append(field.label)
    if field.kind in ("radio", "checkbox") and field.context:
        candidates.append(field.context)
    for candidate in candidates:
        answer = answer_for(candidate, screening)
        if answer is not None:
            return answer
    return None


def plan_screening(field, fields, profile):
    """Return (handled, action). Non-selected members of a resolved radio group are handled."""
    answer = answer_for_field(field, profile.screening)
    if answer is None:
        return False, None
    return plan_answer(field, fields, answer)


def plan_answer(field, fields, answer):
    """Render a declared value into the actual choice/control without guessing option IDs."""
    value = answer.value
    if len(answer.selected_label_sets) > 1 and field.kind in ("radio", "select", "combobox"):
        return False, None
    if field.kind == "radio":
        peers = group_members(field, fields)
        question = logical_question(peers)
        option = pick_option(
            [Option(label=option_label(peer, question), value=peer.id) for peer in peers],
            answer.labels,
        )
        if option is None:
            return False, None
        if field.id != option.value:
            return True, None
        value = True
    elif field.kind == "checkbox" and (
        answer.selected_label_sets
        or not isinstance(value, bool)
        or len(group_members(field, fields)) > 1
    ):
        peers = group_members(field, fields)
        question = logical_question(peers)
        options = [Option(label=option_label(peer, question), value=peer.id) for peer in peers]
        # Every requested answer needs exactly one option. Never silently select a subset.
        selected = [
            pick_option(options, labels)
            for labels in (answer.selected_label_sets or [answer.labels])
        ]
        if any(option is None for option in selected):
            return False, None
        desired = {option.value for option in selected}
        if len(desired) != len(selected):
            return False, None
        value = field.id in desired
        if not value and not field.filled:
            return True, None
    elif field.kind in ("select", "combobox") and field.options:
        option = pick_option(field.options, answer.labels)
        if option is None:
            # The profile concept is known, but this employer uses an unfamiliar
            # label. Leave it unresolved so constrained model inference can select
            # from the runtime options instead of typing a guessed value.
            return False, None
        value = option.value if field.kind == "select" else option.label
        answer = Answer(value, answer.source, [option.label])
    elif field.kind == "checkbox":
        if not isinstance(value, bool):
            return False, None
    elif field.kind not in ("text", "textarea", "combobox"):
        return False, None
    return True, Action(
        field=field, value=value, source=f"screening:{answer.source}", choice_labels=answer.labels
    )
