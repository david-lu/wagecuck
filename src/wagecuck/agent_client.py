"""Provider-neutral structured agent operations and the Ollama transport."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from .agent_prompts import DRAFT_PROMPT, INFERENCE_PROMPT, MAPPING_PROMPT, REQUIREMENTS_PROMPT
from .agent_types import (
    AgentOperation,
    AgentRequest,
    Drafts,
    Mapping,
    Mappings,
    Requirements,
)
from .field_semantics import PROFILE_FIELD_DESCRIPTIONS, fields_metadata
from .inference import FieldAnswers
from .models import ApplicationError, Code


class StructuredMappingAgent:
    """Build typed operations; provider subclasses only adapt and send requests."""

    def __init__(self, model: str, endpoint: str, *, transport=None, timeout=60):
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport
        self.timeout = timeout
        self.calls = 0
        self.operation_calls: Counter[str] = Counter()
        self.operation_attempts: Counter[str] = Counter()
        self.retries = 0
        self.failures: Counter[str] = Counter()
        self.input_tokens = 0
        self.output_tokens = 0
        self.request_ids: list[str] = []
        self.last_error: dict[str, Any] | None = None
        self._mapping_warnings: list[dict] = []

    def _start_operation(self, operation: AgentOperation) -> None:
        self.operation_calls[operation.value] += 1
        self.last_error = None

    def _start_attempt(self, operation: AgentOperation) -> None:
        self.calls += 1
        self.operation_attempts[operation.value] += 1

    def record_error(
        self,
        operation: AgentOperation,
        category: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.failures[category] += 1
        self.last_error = {
            "operation": operation.value,
            "category": category,
            "status_code": status_code,
            "request_id": request_id,
        }
        if request_id:
            self._record_request_id(request_id)

    def record_usage(self, data: dict[str, Any], request_id: str | None = None) -> None:
        usage = data.get("usage") or {}
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        if request_id or data.get("id"):
            self._record_request_id(str(request_id or data["id"]))
        self.last_error = None

    def _record_request_id(self, request_id: str) -> None:
        if request_id not in self.request_ids:
            self.request_ids.append(request_id)
            self.request_ids[:] = self.request_ids[-20:]

    def metrics(self) -> dict[str, Any]:
        return {
            "operation_calls": dict(self.operation_calls),
            "operation_attempts": dict(self.operation_attempts),
            "retries": self.retries,
            "failures": dict(self.failures),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "request_ids": list(self.request_ids),
            "last_error": self.last_error,
        }

    def take_mapping_warnings(self) -> list[dict]:
        warnings, self._mapping_warnings = self._mapping_warnings, []
        return warnings

    async def assess(self, fields):
        request = AgentRequest(
            operation=AgentOperation.REQUIREMENTS,
            schema=Requirements.model_json_schema(),
            max_output_tokens=4000,
            messages=[
                {"role": "system", "content": REQUIREMENTS_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "fields": [
                                {
                                    "field_id": item["field_id"],
                                    "label": item["label"],
                                    "group": item["group"],
                                    "logical_question": item["logical_question"],
                                    "group_options": item["group_options"],
                                    "kind": item["kind"],
                                    "context": item["context"],
                                    "dom_required": item["required"],
                                }
                                for item in fields_metadata(fields)
                            ]
                        }
                    ),
                },
            ],
        )
        try:
            return Requirements.model_validate_json(await self._request(request)).assessments
        except Exception as exc:
            if self.last_error is None:
                self.record_error(request.operation, "invalid_structured_output")
            raise ApplicationError(
                Code.AGENT_FAILED,
                "Required-field agent was unavailable or returned invalid metadata.",
            ) from exc

    async def map(self, fields, fact_keys):
        self._mapping_warnings = []
        schema = Mappings.model_json_schema()
        properties = schema["$defs"]["Mapping"]["properties"]
        properties["fact_key"]["enum"] = ["", *fact_keys]
        properties["fact_keys"]["items"]["enum"] = fact_keys
        properties["field_id"]["enum"] = [f"{field.frame}:{field.id}" for field in fields]
        request = AgentRequest(
            operation=AgentOperation.MAPPING,
            schema=schema,
            max_output_tokens=8000,
            messages=[
                {"role": "system", "content": MAPPING_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "fields": fields_metadata(fields),
                            "profile_field_options": fact_keys,
                            "profile_field_descriptions": {
                                key: description
                                for key, description in PROFILE_FIELD_DESCRIPTIONS.items()
                                if key in fact_keys
                            },
                        }
                    ),
                },
            ],
        )
        try:
            payload = json.loads(await self._request(request))
            if not isinstance(payload, dict) or not isinstance(payload.get("mappings"), list):
                raise TypeError("Invalid mapping response envelope.")
            mappings = []
            for candidate in payload["mappings"]:
                try:
                    mappings.append(Mapping.model_validate(candidate))
                except ValidationError:
                    field_id = candidate.get("field_id", "") if isinstance(candidate, dict) else ""
                    self._mapping_warnings.append(
                        {
                            "field_id": str(field_id),
                            "stage": "mapping",
                            "message": "Model returned malformed mapping metadata for this field.",
                            "code": Code.AGENT_FAILED,
                        }
                    )
            return mappings
        except Exception as exc:
            if self.last_error is None:
                self.record_error(request.operation, "invalid_structured_output")
            raise ApplicationError(
                Code.AGENT_FAILED, "Mapping agent returned an invalid response or was unavailable."
            ) from exc

    async def draft(self, fields, facts):
        schema = Drafts.model_json_schema()
        properties = schema["$defs"]["Draft"]["properties"]
        properties["field_id"]["enum"] = [f"{field.frame}:{field.id}" for field in fields]
        properties["fact_keys"]["items"]["enum"] = list(facts)
        request = AgentRequest(
            operation=AgentOperation.DRAFT,
            schema=schema,
            max_output_tokens=12000,
            messages=[
                {"role": "system", "content": DRAFT_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps({"fields": fields_metadata(fields), "facts": facts}),
                },
            ],
        )
        try:
            return Drafts.model_validate_json(await self._request(request)).drafts
        except Exception as exc:
            if self.last_error is None:
                self.record_error(request.operation, "invalid_structured_output")
            raise ApplicationError(
                Code.AGENT_FAILED, "Agent-fill returned invalid prose or was unavailable."
            ) from exc

    async def infer(self, fields, facts):
        schema = FieldAnswers.model_json_schema()
        properties = schema["$defs"]["FieldAnswer"]["properties"]
        properties["field_id"]["enum"] = [f"{field.frame}:{field.id}" for field in fields]
        if facts:
            properties["fact_keys"]["items"]["enum"] = list(facts)
        request = AgentRequest(
            operation=AgentOperation.INFERENCE,
            schema=schema,
            max_output_tokens=12000,
            messages=[
                {"role": "system", "content": INFERENCE_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "fields": fields_metadata(fields),
                            "facts": facts,
                            "reference_date": datetime.now(UTC).date().isoformat(),
                        }
                    ),
                },
            ],
        )
        try:
            return FieldAnswers.model_validate_json(await self._request(request)).answers
        except Exception as exc:
            if self.last_error is None:
                self.record_error(request.operation, "invalid_structured_output")
            raise ApplicationError(
                Code.AGENT_FAILED, "Answer inference failed or returned invalid data."
            ) from exc

    async def _request(self, request: AgentRequest) -> str:
        raise NotImplementedError


class OllamaMappingAgent(StructuredMappingAgent):
    def __init__(self, model, endpoint="http://localhost:11434", **kwargs):
        super().__init__(model, endpoint, **kwargs)

    async def _request(self, request: AgentRequest) -> str:
        self._start_operation(request.operation)
        self._start_attempt(request.operation)
        payload = {
            "model": self.model,
            "stream": False,
            "format": request.schema,
            "options": {"temperature": 0},
            "messages": request.messages,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, trust_env=False, transport=self.transport
            ) as client:
                response = await client.post(f"{self.endpoint}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
            self.last_error = None
            return data["message"]["content"]
        except Exception:
            self.record_error(request.operation, "provider_error")
            raise
