"""Shared model configuration for application runs and corpus probes."""

import copy
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .agent import OllamaMappingAgent, StructuredMappingAgent

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
        **kwargs,
    ):
        super().__init__(model, endpoint, **kwargs)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI requires OPENAI_API_KEY in your environment.")

    async def _request(self, payload):
        self.calls += 1
        schema = strict_schema(payload["format"])
        messages = copy.deepcopy(payload["messages"])
        if schema["title"] == "Mappings":
            # Use one representation on the wire so a model cannot select both
            # mutually exclusive fact_key and fact_keys. The planner still
            # receives its existing internal Mapping contract.
            mapping_schema = schema["$defs"]["Mapping"]
            del mapping_schema["properties"]["fact_key"]
            mapping_schema["required"].remove("fact_key")
            mapping_schema["properties"]["fact_keys"]["minItems"] = 1
            messages[0]["content"] = messages[0]["content"].replace(
                "Use fact_key for one direct value OR fact_keys and separator for a simple "
                "ordered combination of text values. Never use both.",
                "Use fact_keys with one key for a direct value, or multiple keys and separator "
                "for a simple ordered combination of text values. Omit unmappable fields "
                "entirely; never return an empty list of keys.",
            )
            # OpenAI's strict schema rejects control characters in enum literals.
            schema["$defs"]["Mapping"]["properties"]["separator"]["enum"] = [
                "space",
                "comma",
                "newline",
            ]
            messages[0]["content"] += (
                " Use separator names: space for a single space, comma for comma-space, "
                "or newline for a line break. For single-key mappings use space."
            )
        body = {
            "model": self.model,
            "input": messages,
            "store": False,
            "max_output_tokens": 12000,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": payload["format"]["title"],
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        async with httpx.AsyncClient(
            timeout=self.timeout, trust_env=False, transport=self.transport
        ) as client:
            response = await client.post(
                f"{self.endpoint}/responses",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
        data = response.json()
        if data.get("status") != "completed":
            raise ValueError("Model response did not complete.")
        content = [
            part
            for item in data.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
        ]
        if any(part.get("type") == "refusal" for part in content):
            raise ValueError("Model declined the request.")
        texts = [part["text"] for part in content if part.get("type") == "output_text"]
        if len(texts) != 1:
            raise ValueError("Expected one structured model response.")
        if schema["title"] == "Mappings":
            data = json.loads(texts[0])
            separators = {"space": " ", "comma": ", ", "newline": "\n"}
            for mapping in data["mappings"]:
                if "fact_key" in mapping:
                    raise ValueError("Unexpected direct-key property in model response.")
                mapping["separator"] = separators[mapping["separator"]]
                if len(mapping["fact_keys"]) == 1:
                    mapping["fact_key"] = mapping["fact_keys"].pop()
            return json.dumps(data)
        return texts[0]


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
        help="Draft unresolved prose using supplied career facts; requires a model",
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
    return {
        "provider": "openai"
        if isinstance(agent, OpenAIMappingAgent)
        else "ollama"
        if agent
        else "none",
        "model": agent.model if agent else None,
        "agent_fill": agent_fill,
        "calls_attempted": agent.calls if agent else 0,
    }
