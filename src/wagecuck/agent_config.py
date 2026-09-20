"""Model provider configuration for application runs and corpus probes."""

import asyncio
import copy
import json
import os
import random
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .agent_client import OllamaMappingAgent, StructuredMappingAgent
from .agent_prompts import OPENAI_MAPPING_PROMPT
from .agent_types import AgentOperation, AgentRequest

DEFAULT_OPENAI_MODEL = "gpt-5.6-terra"


def strict_schema(schema):
    schema = copy.deepcopy(schema)

    def visit(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


class OpenAIMappingAgent(StructuredMappingAgent):
    def __init__(
        self,
        model=DEFAULT_OPENAI_MODEL,
        endpoint="https://api.openai.com/v1",
        *,
        api_key=None,
        max_attempts=3,
        retry_backoff=0.25,
        **kwargs,
    ):
        super().__init__(model, endpoint, **kwargs)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI requires OPENAI_API_KEY in your environment.")
        if max_attempts < 1:
            raise ValueError("OpenAI max_attempts must be positive.")
        self.max_attempts = max_attempts
        self.retry_backoff = retry_backoff

    @staticmethod
    def _contract(request: AgentRequest):
        """Adapt a known operation to OpenAI's strict structured-output contract."""
        schema = strict_schema(request.schema)
        messages = copy.deepcopy(request.messages)
        if request.operation == AgentOperation.MAPPING:
            mapping_schema = schema["$defs"]["Mapping"]
            del mapping_schema["properties"]["fact_key"]
            mapping_schema["required"].remove("fact_key")
            mapping_schema["properties"]["fact_keys"]["minItems"] = 1
            mapping_schema["properties"]["separator"]["enum"] = [
                "space",
                "comma",
                "newline",
            ]
            messages[0]["content"] = OPENAI_MAPPING_PROMPT
        return schema, messages

    async def _request(self, request: AgentRequest):
        self._start_operation(request.operation)
        schema, messages = self._contract(request)
        body = {
            "model": self.model,
            "input": messages,
            "store": False,
            "max_output_tokens": request.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema["title"],
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        data, request_id = await self._post_with_retries(request.operation, body)
        request_id = request_id or (str(data["id"]) if data.get("id") else None)
        if data.get("status") != "completed":
            self.record_error(request.operation, "incomplete", request_id=request_id)
            raise ProviderResponseError("incomplete")
        content = [
            part
            for item in data.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
        ]
        if any(part.get("type") == "refusal" for part in content):
            self.record_error(request.operation, "refusal", request_id=request_id)
            raise ProviderResponseError("refusal")
        texts = [part["text"] for part in content if part.get("type") == "output_text"]
        if len(texts) != 1:
            self.record_error(request.operation, "invalid_response", request_id=request_id)
            raise ProviderResponseError("invalid_response")
        self.record_usage(data, request_id)
        if request.operation == AgentOperation.MAPPING:
            parsed = json.loads(texts[0])
            separators = {"space": " ", "comma": ", ", "newline": "\n"}
            for mapping in parsed["mappings"]:
                if "fact_key" in mapping:
                    raise ProviderResponseError("invalid_response")
                mapping["separator"] = separators[mapping["separator"]]
                if len(mapping["fact_keys"]) == 1:
                    mapping["fact_key"] = mapping["fact_keys"].pop()
            return json.dumps(parsed)
        return texts[0]

    async def _post_with_retries(self, operation: AgentOperation, body):
        for attempt in range(self.max_attempts):
            self._start_attempt(operation)
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout, trust_env=False, transport=self.transport
                ) as client:
                    response = await client.post(
                        f"{self.endpoint}/responses",
                        json=body,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
            except httpx.TransportError as exc:
                if attempt + 1 < self.max_attempts:
                    self.retries += 1
                    self.failures["network_error"] += 1
                    await asyncio.sleep(self.retry_backoff * 2**attempt)
                    continue
                self.record_error(operation, "network_error")
                raise ProviderResponseError("network_error") from exc

            request_id = response.headers.get("x-request-id")
            if request_id:
                self._record_request_id(request_id)
            if response.status_code >= 400:
                provider_type, provider_code = self._provider_error(response)
                category = self._http_error_category(
                    response.status_code, provider_code=provider_code, provider_type=provider_type
                )
                retry_after = self._retry_after(response)
                retryable = category in ("rate_limited", "server_error")
                if retry_after is not None and retry_after > self.timeout:
                    retryable = False
                if retryable and attempt + 1 < self.max_attempts:
                    self.retries += 1
                    self.failures[category] += 1
                    delay = retry_after
                    if delay is None:
                        base = self.retry_backoff * 2**attempt
                        delay = base + random.uniform(0, base / 4) if base else 0
                    await asyncio.sleep(delay)
                    continue
                self.record_error(
                    operation,
                    category,
                    status_code=response.status_code,
                    request_id=request_id,
                    provider_code=provider_code,
                    provider_type=provider_type,
                    retry_after_seconds=retry_after,
                )
                raise ProviderResponseError(category, provider_code=provider_code)
            try:
                return response.json(), request_id
            except (ValueError, TypeError) as exc:
                self.record_error(operation, "invalid_response", request_id=request_id)
                raise ProviderResponseError("invalid_response") from exc
        raise AssertionError("retry loop did not return or raise")

    @staticmethod
    def _provider_error(response):
        try:
            error = response.json().get("error", {})
        except (TypeError, ValueError):
            return None, None
        if not isinstance(error, dict):
            return None, None

        def safe(value):
            if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", value):
                return None
            return value

        return safe(error.get("type")), safe(error.get("code"))

    @staticmethod
    def _retry_after(response):
        value = response.headers.get("retry-after")
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            return None
        return seconds if 0 <= seconds else None

    @staticmethod
    def _http_error_category(status_code, *, provider_code=None, provider_type=None):
        if status_code == 429:
            if provider_type == "insufficient_quota" or provider_code in {
                "credit_balance_exhausted",
                "organization_spend_limit_exceeded",
                "organization_usage_limit_exceeded",
                "project_spend_limit_exceeded",
            }:
                return "quota_exhausted"
            return "rate_limited"
        if status_code >= 500:
            return "server_error"
        if status_code in (401, 403):
            return "authentication"
        if status_code == 400:
            return "bad_request"
        return "http_error"


class ProviderResponseError(Exception):
    """Sanitized provider failure; response bodies never enter exception text."""

    def __init__(self, category, *, provider_code=None):
        self.category = category
        self.provider_code = provider_code
        suffix = f" ({provider_code})" if provider_code else ""
        super().__init__(f"Model provider failed: {category}{suffix}.")


def add_agent_arguments(parser):
    # Explicit path: read the caller's working directory, never search parents.
    # Shell values take precedence; interpolation cannot alter literal API keys.
    load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)
    parser.add_argument(
        "--agent-provider",
        choices=("openai", "ollama"),
        default=os.environ.get("WAGECUCK_AGENT_PROVIDER"),
    )
    parser.add_argument("--agent-model", default=os.environ.get("WAGECUCK_AGENT_MODEL"))
    parser.add_argument("--agent-endpoint", default=os.environ.get("WAGECUCK_AGENT_ENDPOINT"))
    parser.add_argument("--agent-timeout", type=float, default=60)
    parser.add_argument(
        "--agent-fill",
        action="store_true",
        help="Infer all unmapped answers from the profile; mark unsupported invented answers made_up",
    )


def create_agent(args):
    if args.agent_timeout <= 0:
        raise ValueError("--agent-timeout must be positive")
    provider = args.agent_provider or (
        "openai"
        if (args.agent_model or "").startswith("gpt-")
        else "ollama"
        if args.agent_model
        else "openai"
        if os.environ.get("OPENAI_API_KEY")
        else None
    )
    if provider is None:
        if args.agent_fill:
            raise ValueError("--agent-fill requires --agent-provider or --agent-model")
        return None
    if provider == "openai":
        return OpenAIMappingAgent(
            args.agent_model or DEFAULT_OPENAI_MODEL,
            args.agent_endpoint or "https://api.openai.com/v1",
            timeout=args.agent_timeout,
        )
    if not args.agent_model:
        raise ValueError("Ollama requires --agent-model or WAGECUCK_AGENT_MODEL")
    return OllamaMappingAgent(
        args.agent_model,
        args.agent_endpoint or "http://localhost:11434",
        timeout=args.agent_timeout,
    )


def agent_arguments(args):
    result = []
    for name in ("provider", "model", "endpoint", "timeout"):
        value = getattr(args, f"agent_{name}")
        if value is not None:
            result.extend([f"--agent-{name}", str(value)])
    if args.agent_fill:
        result.append("--agent-fill")
    return result


def agent_metadata(agent, *, agent_fill=False):
    metadata = {
        "provider": "openai"
        if isinstance(agent, OpenAIMappingAgent)
        else "ollama"
        if agent
        else "none",
        "model": agent.model if agent else None,
        "agent_fill": agent_fill,
        "calls_attempted": agent.calls if agent else 0,
    }
    if agent and hasattr(agent, "metrics"):
        metadata["metrics"] = agent.metrics()
    return metadata
