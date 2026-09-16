import argparse
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from playwright.async_api import async_playwright

from wagecuck.agent_config import (
    OpenAIMappingAgent,
    add_agent_arguments,
    agent_arguments,
    create_agent,
)
from wagecuck.models import ApplicationError, Code, FormField


def response(data):
    return httpx.Response(
        200,
        json={
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": json.dumps(data)},
                    ],
                }
            ],
        },
    )


def test_dotenv_configuration_preserves_shell_and_literal_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in (
        "OPENAI_API_KEY",
        "WAGECUCK_AGENT_PROVIDER",
        "WAGECUCK_AGENT_MODEL",
        "WAGECUCK_AGENT_ENDPOINT",
    ):
        monkeypatch.setenv(name, "")  # Track absence too, since dotenv writes outside monkeypatch.
        monkeypatch.delenv(name)
    (tmp_path / ".env").write_text(
        'OPENAI_API_KEY="test-${LITERAL}"\nWAGECUCK_AGENT_MODEL=gpt-from-file\n'
    )
    monkeypatch.setenv("WAGECUCK_AGENT_MODEL", "gpt-from-shell")
    parser = argparse.ArgumentParser()
    add_agent_arguments(parser)
    args = parser.parse_args(["--agent-fill"])
    agent = create_agent(args)
    assert isinstance(agent, OpenAIMappingAgent)
    assert agent.api_key == "test-${LITERAL}"
    assert agent.model == "gpt-from-shell"
    assert agent_arguments(args) == [
        "--agent-model",
        "gpt-from-shell",
        "--agent-timeout",
        "60",
        "--agent-fill",
    ]


def test_corpus_runner_forwards_model_configuration(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "dry_run_model_test", Path(__file__).parents[1] / "scripts" / "dry_run_all.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    manifest, output = tmp_path / "jobs.json", tmp_path / "result.json"
    manifest.write_text(json.dumps({"cases": [{"id": "one"}]}))
    commands = []

    def run(command, *, check):
        assert check
        commands.append(command)
        if Path(command[1]).name == "probe_live.py":
            output.write_text(
                json.dumps(
                    {
                        "checked_at": "test",
                        "results": [
                            {"id": "one", "url": "https://example.com", "code": "JOB_CLOSED"}
                        ],
                    }
                )
            )

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "dry_run_all.py",
            "--manifests",
            str(manifest),
            "--output",
            str(output),
            "--agent-provider",
            "openai",
            "--agent-model",
            "gpt-test",
            "--agent-fill",
            "--agent-timeout",
            "42",
        ],
    )
    module.main()
    assert "--agent-provider" not in commands[0]  # Navigation does not use the model.
    probe = commands[-1]
    assert probe[probe.index("--agent-provider") + 1] == "openai"
    assert probe[probe.index("--agent-model") + 1] == "gpt-test"
    assert probe[probe.index("--agent-timeout") + 1] == "42.0"
    assert "--agent-fill" in probe
    assert "test-key" not in str(commands)


@pytest.mark.parametrize("separator,expected", [("space", " "), ("comma", ", "), ("newline", "\n")])
async def test_responses_mapping_uses_strict_schema_and_allowed_choices(separator, expected):
    def respond(request):
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["store"] is False
        schema = body["text"]["format"]["schema"]
        mapping = schema["$defs"]["Mapping"]
        assert set(mapping["required"]) == set(mapping["properties"])
        assert mapping["additionalProperties"] is False
        assert "fact_key" not in mapping["properties"]
        assert mapping["properties"]["fact_keys"]["items"]["enum"] == ["full_name"]
        assert mapping["properties"]["fact_keys"]["minItems"] == 1
        assert mapping["properties"]["separator"]["enum"] == ["space", "comma", "newline"]
        return response(
            {
                "mappings": [
                    {
                        "field_id": "0:x",
                        "fact_keys": ["full_name"],
                        "separator": separator,
                        "evidence": "Applicant identity",
                        "confidence": 1,
                    }
                ]
            }
        )

    agent = OpenAIMappingAgent(api_key="test-key", transport=httpx.MockTransport(respond))
    result = await agent.map(
        [FormField(id="x", frame=0, label="Applicant identity", kind="text")], ["full_name"]
    )
    assert result[0].fact_key == "full_name"
    assert result[0].separator == expected
    assert agent.calls == 1


@pytest.mark.parametrize("failure", ["http", "incomplete", "refusal", "invalid_json"])
async def test_model_failures_are_typed_without_exposing_response(failure):
    def respond(request):
        if failure == "http":
            return httpx.Response(401, json={"error": "sensitive detail"})
        if failure == "incomplete":
            return httpx.Response(200, json={"status": "incomplete"})
        if failure == "refusal":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}
                    ],
                },
            )
        return response({"unexpected": "data"})

    agent = OpenAIMappingAgent(api_key="test-key", transport=httpx.MockTransport(respond))
    with pytest.raises(ApplicationError) as caught:
        await agent.assess([])
    assert caught.value.code == Code.AGENT_FAILED
    assert "sensitive detail" not in str(caught.value)


async def test_offline_probe_invokes_hosted_mapping_and_drafting(profile):
    spec = importlib.util.spec_from_file_location(
        "probe_model_test", Path(__file__).parents[1] / "scripts" / "probe_live.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operations = []

    def respond(request):
        body = json.loads(request.content)
        name = body["text"]["format"]["name"]
        operations.append(name)
        fields = json.loads(body["input"][1]["content"])["fields"]
        if name == "Requirements":
            return response({"assessments": []})
        if name == "Mappings":
            target = next(f for f in fields if f["label"] == "Applicant identity")
            return response(
                {
                    "mappings": [
                        {
                            "field_id": target["field_id"],
                            "fact_keys": ["full_name"],
                            "separator": "space",
                            "evidence": "Applicant identity",
                            "confidence": 1,
                        }
                    ]
                }
            )
        return response(
            {
                "answers": [
                    {
                        "field_id": fields[0]["field_id"],
                        "value": "My skills include Python.",
                        "basis": "inferred",
                        "reason": "Uses declared skills.",
                        "fact_keys": ["skills"],
                    }
                ]
            }
        )

    agent = OpenAIMappingAgent(api_key="test-key", transport=httpx.MockTransport(respond))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(service_workers="block")
        await context.route_web_socket("**/*", lambda ws: ws.close())
        page = await context.new_page()
        await page.set_content("""<form>
          <label>Applicant identity<input required></label>
          <label>Describe your technical strengths<textarea required></textarea></label>
        </form>""")
        result = await module.probe_fields(page, profile, agent, agent_fill=True)
        assert operations == ["Requirements", "Mappings", "FieldAnswers"]
        assert result["filled_count"] == 2
        assert result["required_answers_missing"] == []
        assert result["completed_values_retained"]
        assert [f["source"] for f in result["fields"]] == ["agent:full_name", "agent_fill:skills"]
        assert await page.evaluate("navigator.onLine") is False
        await browser.close()
