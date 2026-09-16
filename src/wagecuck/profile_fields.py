"""Question intent -> stable profile keys. These rules contain no applicant answers."""

import re

from .browser import normalize
from .models import Action
from .screening import Answer, answer_for, plan_answer


def text(field):
    return re.sub(
        r"\brequired\b$", "", normalize(field.group if field.kind == "radio" else field.label)
    ).strip()


def key_for(field, profile):
    q = text(field)
    if re.search(r"pronouns?|preferred pronoun", q):
        return "application.pronouns"
    if "preferred name" in q:
        return "application.preferred_name"
    if q == "headline":
        return "application.headline"
    if q == "summary" or q.startswith("personal summary"):
        return "application.personal_summary"
    if q in ("work history", "employment history"):
        return "work_history"
    if q == "cover letter" and field.kind in ("text", "textarea"):
        return "application.cover_letter_text"
    if re.search(r"how (?:do|have) you use(?:d)? ai", q):
        return "application.ai_usage"
    if re.fullmatch(
        r"why (?:are you interested in|do you want) (?:this|the) (?:role|position|job)", q
    ):
        return "application.role_interest"
    if re.search(r"which location.*applying|preferred (?:job |work )?location", q):
        return "application.preferred_location"
    if re.search(r"country.*currently work|current work country", q):
        return "application.current_work_country"
    if re.search(r"where are you (?:presently located|located|based)|what is your location", q):
        return "location" if field.kind in ("text", "textarea") else "country"
    if q.startswith("current location no location found"):
        return "city"
    if re.search(r"how did you hear about|which channel led you to apply", q):
        return "source"
    if re.search(
        r"when.*(?:available|looking).*start|date available|availability.*start|available to start|immediate joiners",
        q,
    ):
        return "application.available_start_date" if field.kind == "date" else "availability"
    if re.search(r"notice period", q):
        return "application.notice_period_days" if field.kind == "number" else "notice_period"
    if re.search(r"salary|compensation|desired pay", q):
        if re.search(r"current|previous|last|hourly|per hour|monthly|per month|ctc", q):
            return None
        if not re.search(r"desired|expect|target|range", q):
            return None
        currencies = {
            "CAD": r"\bcad\b|canadian",
            "USD": r"\busd\b|u s dollars",
            "GBP": r"\bgbp\b|pounds",
            "EUR": r"\beur\b|euros",
            "INR": r"\binr\b|rupees",
        }
        requested = [currency for currency, pattern in currencies.items() if re.search(pattern, q)]
        currency = profile.application.compensation.currency
        if len(requested) > 1 or (requested and requested != [currency]):
            return None
        if field.kind == "number":
            return "application.compensation.annual_target" if currency and "annual" in q else None
        return "compensation_expectations"
    # Degree fields map only when there is one entry; never pick an arbitrary school.
    if len(profile.education) == 1:
        if q in (
            "school",
            "university",
            "college",
            "select a university or college",
        ) or q.startswith("if your post secondary is not listed"):
            return "education.0.school"
        if q == "degree":
            return "education.0.degree"
        if q in ("discipline", "field of study", "major"):
            return "education.0.field"
        if "degree result" in q:
            return "education.0.result"
        if "anticipated graduation date" in q or "expected graduation date" in q:
            return "education.0.expected_graduation_date"
    if re.search(r"how many years", q):
        # Compound experience requirements need their own explicitly declared category.
        skills = {
            "python": r"\bpython\b",
            "golang": r"\bgolang\b|\bgo language\b",
            "kubernetes": r"\bkubernetes\b",
            "terraform": r"\bterraform\b",
            "microservices_rest": r"microservices and restful apis",
            "genai": r"genai|agentic ai",
        }
        matched = [key for key, pattern in skills.items() if re.search(pattern, q)]
        if len(matched) == 1:
            return f"experience.{matched[0]}.years"
    if re.search(r"what aws services.*worked", q):
        return "experience.aws.summary"
    if re.search(r"hands on experience with github or gitlab apis", q):
        return "experience.git_platform_apis.has_experience"
    # Processing consent is distinct from attesting to reading a policy or future marketing.
    if re.search(r"sms.*consent|consent.*sms", q):
        return "consents.sms"
    if "future job opportunities" in q and re.search(r"contact|consent|agree", q):
        return "consents.future_opportunities"
    if re.search(r"consent.*process.*application data", q) and not re.search(
        r"read|privacy|policy|future|marketing", q
    ):
        return "consents.application_processing"
    return None


def screening_options(field, profile):
    """Allow semantic selection of an existing declaration with the same meaning/jurisdiction."""
    label = field.group if field.kind == "radio" else field.label
    answer = answer_for(label, profile.screening)
    if answer is None:
        # Synonyms may still require model classification; preserve country and negation.
        q = normalize(label)
        if re.search(r"work|employment", q) and re.search(r"permitted|permission|entitled", q):
            answer = answer_for(
                re.sub(r"permitted|permission|entitled", "authorized", q), profile.screening
            )
    if answer is None:
        return {}
    primary = f"screening.{answer.source}"
    result = {primary: answer}
    aliases = {"CA": "canada", "US": "us", "GB": "uk"}
    match = re.fullmatch(
        r"work_authorization\.(CA|US|GB)\.(authorized|requires_sponsorship)", answer.source
    )
    if match:
        result[f"{match[2]}_{aliases[match[1]]}"] = answer
    return result


def plan_named(field, fields, profile):
    key = key_for(field, profile)
    if key is None:
        return False, None
    value = profile.values().get(key)
    if value is None:
        return False, None
    return action_for_key(field, fields, profile, key)


def action_for_key(field, fields, profile, key):
    screening = screening_options(field, profile)
    if key in screening:
        handled, action = plan_answer(field, fields, screening[key])
        if action:
            action.source = f"facts:{key}"
        return handled, action
    value = profile.values().get(key)
    if value is None:
        return False, None
    if isinstance(value, bool):
        labels = ["Yes"] if value else ["No"]
    else:
        labels = [value]
    if field.kind in ("radio", "checkbox", "select", "combobox"):
        handled, action = plan_answer(field, fields, Answer(value, key, labels))
        if action:
            action.source = f"facts:{key}"
        return handled, action
    if field.kind in ("text", "textarea", "number", "date", "email", "url", "tel"):
        if field.kind == "number" and (
            isinstance(value, bool) or not re.fullmatch(r"\d+(?:\.\d+)?", str(value))
        ):
            return False, None
        return True, Action(field=field, value=value, source=f"facts:{key}")
    return False, None


def restricted_key(key):
    return key.startswith(
        (
            "screening.",
            "consents.",
            "authorized_",
            "requires_sponsorship_",
            "application.compensation.",
        )
    ) or key in ("compensation_expectations", "application.pronouns")


def allowed_named_mapping(field, keys, profile, sensitive):
    permitted = set(screening_options(field, profile))
    screening = bool(permitted)
    named = key_for(field, profile)
    if named:
        permitted.add(named)
    if sensitive or screening or any(restricted_key(key) for key in keys):
        return len(keys) == 1 and keys[0] in permitted
    return True
