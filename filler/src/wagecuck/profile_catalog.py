"""Profile field labels, descriptions and value projections used by every planning route.

The public Profile helpers delegate here; planners resolve this catalog once per pass.
"""

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


WORK_COUNTRY_ALIASES = {"US": "us", "CA": "canada", "GB": "uk"}


def declared_profile_values(profile) -> dict[str, str | bool]:
    """Named declarations shared by deterministic matching and agent field selection."""
    result = {}

    def flatten(prefix, value):
        if prefix == "application.randomize_source":
            return  # This is behavior configuration, not an applicant answer.
        if isinstance(value, dict):
            for key, child in value.items():
                flatten(f"{prefix}.{key}" if prefix else key, child)
        elif isinstance(value, list):
            if value:
                result[prefix] = "; ".join(str(item) for item in value)
        elif value is not None:
            result[prefix] = value if isinstance(value, (str, bool)) else f"{value:g}"

    for section in ("application", "experience", "consents", "screening"):
        data = getattr(profile, section)
        if section == "experience":
            data = {key: entry.model_dump(mode="json") for key, entry in data.items()}
        else:
            data = data.model_dump(mode="json")
        flatten(section, data)
    result.pop("screening.default_work_country", None)
    for country, suffix in WORK_COUNTRY_ALIASES.items():
        auth = profile.screening.work_authorization.get(country)
        if auth:
            for field, alias in (
                ("authorized", "authorized"),
                ("requires_sponsorship", "requires_sponsorship"),
            ):
                value = getattr(auth, field)
                if value is not None:
                    result[f"{alias}_{suffix}"] = value
    return {key: value for key, value in result.items() if isinstance(value, bool) or value.strip()}


def resolve_profile_values(profile, declared=None) -> dict[str, str | bool]:
    """Resolve atomic and composite field options without exposing model objects/nulls."""
    values = dict(profile.facts)
    values["full_name"] = profile.get_full_name()
    address = profile.get_address_parts()
    aliases = {
        "line1": "address_line1",
        "line2": "address_line2",
        "city": "city",
        "state": "state",
        "postal_code": "postal_code",
        "country": "country",
    }
    for key, value in address.model_dump().items():
        values[f"address.{key}"] = value
        values[aliases[key]] = value
    values.update(
        address=address.line1,  # Legacy street-line alias; never includes city/postal code.
        street_address=profile.get_street_address(),
        full_address=profile.get_address(),
        full_address_multiline=profile.get_address(multiline=True),
        location=", ".join(p for p in (address.city, address.state, address.country) if p),
    )
    for section in ("employment", "education"):
        entries = getattr(profile, section)
        if entries:
            values = {
                key: value for key, value in values.items() if not key.startswith(section + ".")
            }
        for index, entry in enumerate(entries):
            for key, value in entry.model_dump(mode="json").items():
                if value is not None:
                    values[f"{section}.{index}.{key}"] = value
    if profile.employment:
        values.pop("current_company", None)
        values.pop("current_title", None)
        current = profile.get_current_employment()
        if current:
            values.update(current_company=current.company, current_title=current.title)
    values["work_history"] = profile.get_work_history()
    values.update(declared if declared is not None else declared_profile_values(profile))
    if profile.application.notice_period_days is not None:
        values["notice_period"] = f"{profile.application.notice_period_days} days"
    availability = profile.application.availability_summary
    if not availability:
        availability = "; ".join(
            value
            for value in (
                f"Available from {profile.application.available_start_date}"
                if profile.application.available_start_date
                else "",
                f"Notice period: {profile.application.notice_period_days} days"
                if profile.application.notice_period_days is not None
                else "",
            )
            if value
        )
    values["availability"] = availability
    pay = profile.application.compensation
    values["compensation_expectations"] = pay.expectations or (
        f"{pay.currency} {pay.annual_target} per year"
        if pay.currency and pay.annual_target is not None
        else ""
    )
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, bool) or (isinstance(value, str) and value.strip())
    }
