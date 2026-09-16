"""Contracts shared by the agent coordinator and model providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import FormField


class AgentOperation(StrEnum):
    REQUIREMENTS = "requirements"
    MAPPING = "mapping"
    DRAFT = "draft"
    INFERENCE = "inference"


@dataclass(frozen=True)
class AgentRequest:
    """Provider-neutral structured model request."""

    operation: AgentOperation
    schema: dict[str, Any]
    messages: list[dict[str, str]]
    max_output_tokens: int


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_id: str
    fact_key: str = ""
    fact_keys: list[str] = Field(default_factory=list)
    separator: Literal[" ", ", ", "\n"] = " "
    evidence: str = ""
    confidence: float = Field(ge=0, le=1)


class Mappings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mappings: list[Mapping]


class RequirementAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_id: str
    required: bool
    evidence: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1)


class Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessments: list[RequirementAssessment]


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_id: str
    text: str = Field(min_length=1, max_length=4000)
    fact_keys: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class Drafts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drafts: list[Draft]


class MappingAgent(Protocol):
    async def map(self, fields: list[FormField], fact_keys: list[str]) -> list[Mapping]: ...
