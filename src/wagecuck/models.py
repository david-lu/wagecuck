from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Code(StrEnum):
    OK = "OK"
    READY = "READY_NOT_SUBMITTED"
    INSPECTED = "INSPECTED_NOT_SUBMITTED"
    INVALID_INPUT = "INVALID_INPUT"
    PROFILE_INVALID = "PROFILE_INVALID"
    DOCUMENT_MISSING = "DOCUMENT_MISSING"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    JOB_CLOSED = "JOB_CLOSED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    CAPTCHA_KEY_MISSING = "CAPTCHA_KEY_MISSING"
    CAPTCHA_UNSUPPORTED = "CAPTCHA_UNSUPPORTED"
    CAPTCHA_SOLVE_FAILED = "CAPTCHA_SOLVE_FAILED"
    CAPTCHA_TIMEOUT = "CAPTCHA_TIMEOUT"
    ACCESS_DENIED = "ACCESS_DENIED"
    FORM_NOT_FOUND = "FORM_NOT_FOUND"
    UNSUPPORTED_CONTROL = "UNSUPPORTED_CONTROL"
    REQUIRED_ANSWER_MISSING = "REQUIRED_ANSWER_MISSING"
    FIELD_FILL_FAILED = "FIELD_FILL_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UPLOAD_UNVERIFIED = "UPLOAD_UNVERIFIED"
    UPLOAD_TIMEOUT = "UPLOAD_TIMEOUT"
    DEFERRED = "DEFERRED"
    AGENT_FAILED = "AGENT_FAILED"
    STEP_LIMIT = "STEP_LIMIT"
    NO_PROGRESS = "NO_PROGRESS"
    SUBMIT_NOT_FOUND = "SUBMIT_NOT_FOUND"
    SUBMISSION_UNCONFIRMED = "SUBMISSION_UNCONFIRMED"
    USER_SUBMISSION_UNCONFIRMED = "USER_SUBMISSION_UNCONFIRMED"
    ALREADY_SUBMITTED = "ALREADY_SUBMITTED"
    RUN_IN_PROGRESS = "RUN_IN_PROGRESS"
    PRIOR_SUBMISSION_UNCERTAIN = "PRIOR_SUBMISSION_UNCERTAIN"
    SYNTHETIC_PROFILE_BLOCKED = "SYNTHETIC_PROFILE_BLOCKED"
    TIMEOUT = "TIMEOUT"
    BROWSER_ERROR = "BROWSER_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class WorkAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorized: bool | None = None
    visa_type: str | None = None
    requires_sponsorship: bool | None = None
    description: str | None = None


class DemographicsProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gender_identity: Literal["man", "woman", "non_binary", "decline"] | None = None
    sexual_orientation: (
        Literal[
            "heterosexual",
            "gay",
            "lesbian",
            "bisexual",
            "pansexual",
            "asexual",
            "queer",
            "decline",
        ]
        | None
    ) = None
    transgender_status: bool | Literal["decline"] | None = None
    race_ethnicity: list[
        Literal[
            "american_indian_alaska_native",
            "asian",
            "black_african_american",
            "middle_eastern_north_african",
            "native_hawaiian_pacific_islander",
            "white",
            "multiracial",
            "decline",
        ]
    ] = Field(default_factory=list)
    hispanic_latino: bool | Literal["decline"] | None = None

    @model_validator(mode="after")
    def check_race_choices(self):
        if len(self.race_ethnicity) != len(set(self.race_ethnicity)):
            raise ValueError("race_ethnicity cannot contain duplicate choices")
        if "decline" in self.race_ethnicity and len(self.race_ethnicity) > 1:
            raise ValueError("race_ethnicity decline cannot be combined with another choice")
        return self


class ScreeningProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_work_country: str | None = None
    work_authorization: dict[str, WorkAuthorization] = Field(default_factory=dict)
    demographics: DemographicsProfile = Field(default_factory=DemographicsProfile)
    veteran_status: (
        Literal["not_a_veteran", "not_protected_veteran", "protected_veteran", "decline"] | None
    ) = None
    disability_status: Literal["no_disability", "disability", "decline"] | None = None
    disability_history: bool | None = None


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    line1: str = ""
    line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""

    def street(self, *, multiline: bool = False) -> str:
        return ("\n" if multiline else ", ").join(p for p in (self.line1, self.line2) if p)

    def formatted(self, *, multiline: bool = False, include_country: bool = True) -> str:
        locality = ", ".join(p for p in (self.city, self.state) if p)
        locality = " ".join(p for p in (locality, self.postal_code) if p)
        parts = [self.street(multiline=multiline), locality]
        if include_country:
            parts.append(self.country)
        return ("\n" if multiline else ", ").join(p for p in parts if p)


class DatedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    start_date: date | None = None
    end_date: date | None = None
    current: bool = False
    location: str = ""
    summary: str = ""

    @model_validator(mode="after")
    def check_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.current and self.end_date:
            raise ValueError("A current entry must have end_date: null")
        return self


class Employment(DatedEntry):
    company: str = Field(min_length=1)
    title: str = Field(min_length=1)


class Education(DatedEntry):
    school: str = Field(min_length=1)
    degree: str = ""
    field: str = ""
    result: str | None = None
    expected_graduation_date: date | None = None


class Compensation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    annual_target: int | None = Field(default=None, ge=0)
    expectations: str | None = None


class ApplicationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    randomize_source: bool = False
    preferred_name: str | None = None
    pronouns: str | None = None
    headline: str | None = None
    personal_summary: str | None = None
    available_start_date: date | None = None
    notice_period_days: int | None = Field(default=None, ge=0)
    availability_summary: str | None = None
    preferred_location: str | None = None
    current_work_country: str | None = None
    role_interest: str | None = None
    ai_usage: str | None = None
    cover_letter_text: str | None = None
    compensation: Compensation = Field(default_factory=Compensation)


class SkillExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    years: float | None = Field(default=None, ge=0, le=100)
    has_experience: bool | None = None
    summary: str | None = None


class ConsentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_processing: bool | None = None
    future_opportunities: bool | None = None
    sms: bool | None = None


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    synthetic: bool = False
    # Basic contact/custom facts; structured sections are projected into mapping keys.
    facts: dict[str, str | bool]
    address: Address | None = None
    employment: list[Employment] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    application: ApplicationProfile = Field(default_factory=ApplicationProfile)
    experience: dict[str, SkillExperience] = Field(default_factory=dict)
    consents: ConsentProfile = Field(default_factory=ConsentProfile)
    resume: Path
    cover_letter: Path | None = None
    screening: ScreeningProfile = Field(default_factory=ScreeningProfile)
    answers: dict[str, str | bool] = Field(
        default_factory=dict,
        description="Legacy exact-question overrides; prefer named profile fields.",
    )

    @field_validator("facts")
    @classmethod
    def contact_required(cls, facts):
        for key in ("first_name", "last_name", "email"):
            if not isinstance(facts.get(key), str) or not facts[key].strip():
                raise ValueError(f"Missing fact: {key}")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", facts["email"]):
            raise ValueError("Invalid email")
        return facts

    @classmethod
    def load(cls, path: Path) -> Profile:
        profile = cls.model_validate_json(path.read_text(encoding="utf-8"))
        for key in ("resume", "cover_letter"):
            value = getattr(profile, key)
            if value is not None:
                setattr(profile, key, (path.parent / value).resolve())
        return profile

    def get_full_name(self) -> str:
        return f"{self.facts['first_name']} {self.facts['last_name']}"

    def get_address_parts(self) -> Address:
        if self.address is not None:
            return self.address
        # Existing flat profiles remain readable. Never combine old address parts
        # into an explicitly supplied structured address.
        keys = {
            "line1": "address",
            "line2": "address_line2",
            "city": "city",
            "state": "state",
            "postal_code": "postal_code",
            "country": "country",
        }
        return Address(
            **{
                key: self.facts[source]
                for key, source in keys.items()
                if isinstance(self.facts.get(source), str)
            }
        )

    def get_address(self, *, multiline: bool = False, include_country: bool = True) -> str:
        return self.get_address_parts().formatted(
            multiline=multiline, include_country=include_country
        )

    def get_street_address(self, *, multiline: bool = False) -> str:
        return self.get_address_parts().street(multiline=multiline)

    def get_current_employment(self) -> Employment | None:
        current = [entry for entry in self.employment if entry.current]
        return current[0] if len(current) == 1 else None

    def get_work_history(self) -> str:
        entries = []
        for job in self.employment:
            dates = " - ".join(
                str(value)
                for value in (job.start_date, "Present" if job.current else job.end_date)
                if value is not None
            )
            heading = " | ".join(
                value for value in (job.company, job.title, dates, job.location) if value
            )
            entries.append("\n".join(value for value in (heading, job.summary) if value))
        return "\n\n".join(entries)

    def declared_values(self) -> dict[str, str | bool]:
        from .profile_catalog import declared_profile_values

        return declared_profile_values(self)

    def values(self) -> dict[str, str | bool]:
        from .profile_catalog import resolve_profile_values

        return resolve_profile_values(self)


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["inspect", "fill", "submit"] = "fill"
    headless: bool = True
    wait_for_user: bool = False
    agent_fill: bool = False
    slow_mo_ms: int = Field(default=0, ge=0, le=5000)
    max_steps: int = Field(default=12, ge=1, le=50)
    timeout_seconds: float = Field(default=120, gt=0, le=900)
    action_timeout_ms: int = Field(default=7000, ge=100, le=60000)
    navigation_timeout_ms: int = Field(default=30000, ge=100, le=120000)
    confirmation_timeout_seconds: float = Field(default=12, gt=0, le=120)
    artifacts_dir: Path = Path("runs")
    database: Path = Path("runs/applications.sqlite3")
    storage_state: Path | None = None
    capture_sensitive_artifacts: bool = False

    @model_validator(mode="after")
    def validate_manual_handoff(self):
        if self.wait_for_user:
            if self.mode != "fill":
                raise ValueError("wait_for_user requires fill mode")
            self.headless = False
        return self


class Option(BaseModel):
    label: str
    value: str


ControlType = Literal[
    "text_input",
    "file_upload",
    "native_select",
    "dynamic_combobox",
    "checkbox",
    "radio",
    "range",
]


class FormField(BaseModel):
    id: str
    frame: int
    label: str
    name: str = ""
    kind: str
    # kind describes the value contract; control_type describes interaction.
    control_type: ControlType | None = None
    required: bool = False
    options: list[Option] = Field(default_factory=list)
    filled: bool = False
    invalid: bool = False
    autocomplete: str = ""
    placeholder: str = ""
    input_mode: str = ""
    pattern: str = ""
    minimum: str = ""
    maximum: str = ""
    step: str = ""
    min_length: int | None = None
    max_length: int | None = None
    group: str = ""
    group_id: str = ""
    fact_key: str = ""
    context: str = ""
    required_evidence: str = ""
    requirement_status: Literal["required", "optional", "unknown"] = "unknown"

    @model_validator(mode="after")
    def infer_control_type(self):
        if self.control_type is not None:
            return self
        self.control_type = {
            "file": "file_upload",
            "select": "native_select",
            "combobox": "dynamic_combobox",
            "checkbox": "checkbox",
            "radio": "radio",
            "range": "range",
        }.get(self.kind, "text_input")
        return self


class Control(BaseModel):
    id: str
    frame: int
    label: str
    kind: str
    action: str = ""


class Snapshot(BaseModel):
    url: str
    title: str
    fields: list[FormField] = Field(default_factory=list)
    controls: list[Control] = Field(default_factory=list)
    text: str = ""
    errors: list[str] = Field(default_factory=list)


class Action(BaseModel):
    field: FormField
    value: str | bool
    source: str
    # Per-run upload attempt state survives replanning; never persisted as profile data.
    upload_attempted: bool = Field(default=False, exclude=True)
    upload_error: Code | None = Field(default=None, exclude=True)
    choice_labels: list[str] = Field(default_factory=list)
    random_choice: bool = False
    answer_basis: Literal["profile", "inferred", "made_up"] = "profile"
    made_up: bool = False
    source_keys: list[str] = Field(default_factory=list)
    inference_reason: str = ""


class ApplicationResult(BaseModel):
    schema_version: int = 1
    run_id: str
    profile_id: str
    job_url: str
    final_url: str = ""
    ats: str = "generic"
    mode: str
    success: bool = False
    status: Literal["succeeded", "ready", "inspected", "failed", "unknown"] = "failed"
    code: Code = Code.INTERNAL_ERROR
    message: str = ""
    retryable: bool = False
    submitted: bool = False
    submission_attempted: bool = False
    user_handoff: bool = False
    steps: int = 0
    fields_filled: int = 0
    answer_log: list[dict] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    artifact_dir: str = ""
    started_at: str
    finished_at: str = ""


class ApplicationError(Exception):
    def __init__(self, code: Code, message: str, unresolved: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.unresolved = unresolved or []
