import asyncio
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from wagecuck.evaluation import BrowserPool


def probe_module():
    spec = importlib.util.spec_from_file_location(
        "parallel_probe", Path(__file__).parents[1] / "scripts" / "probe_live.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def arguments(tmp_path, *, pool=False, aggregate_only=False, concurrency=2):
    return SimpleNamespace(
        output=tmp_path / "probe.json",
        concurrency=concurrency,
        pool=pool,
        aggregate_only=aggregate_only,
        dataset_split="validation" if aggregate_only else "training",
        implementation_fingerprint="fixture",
        agent_fill=True,
        agent_timeout=2,
    )


def cases(count):
    return [
        {
            "id": str(index),
            "final_url": f"https://example.test/{index}",
            "ats": "fixture",
            "code": "INSPECTED_NOT_SUBMITTED",
            "field_count": 1,
        }
        for index in range(count)
    ]


class FakeAgent:
    model = "isolated-test-model"

    def __init__(self):
        self.calls = 0
        self.data = {
            "operation_calls": {},
            "operation_attempts": {},
            "retries": 0,
            "failures": {},
            "input_tokens": 0,
            "output_tokens": 0,
            "request_ids": [],
            "last_error": None,
        }

    def metrics(self):
        return copy.deepcopy(self.data)


class FakeContext:
    def __init__(self, browser):
        self.browser = browser
        self.closed = False
        self.websocket_blocked = False

    async def route_web_socket(self, pattern, handler):
        assert pattern == "**/*"
        self.websocket_blocked = True
        if self.browser.fail_setup:
            raise RuntimeError("Fixture setup failure")

    def set_default_timeout(self, value):
        assert value == 2500

    def set_default_navigation_timeout(self, value):
        assert value == 30000

    async def new_page(self):
        return SimpleNamespace(context=self, url="")

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, owner, *, fail_setup=False):
        self.owner, self.fail_setup = owner, fail_setup
        self.contexts = []
        self.closed = False

    async def new_context(self, **kwargs):
        assert kwargs == {"service_workers": "block"}
        context = FakeContext(self)
        self.contexts.append(context)
        return context

    def is_connected(self):
        return not self.closed

    async def close(self):
        if not self.closed:
            self.owner.live -= 1
        self.closed = True


class FakeChromium:
    def __init__(self, *, fail_first_setup=False, fail_first_launch=False):
        self.browsers = []
        self.live = self.peak = 0
        self.fail_first_setup = fail_first_setup
        self.fail_first_launch = fail_first_launch

    async def launch(self):
        if self.fail_first_launch:
            self.fail_first_launch = False
            raise RuntimeError("Fixture launch failure")
        browser = FakeBrowser(self, fail_setup=self.fail_first_setup and not self.browsers)
        self.browsers.append(browser)
        self.live += 1
        self.peak = max(self.peak, self.live)
        return browser


def install_case_fakes(module, monkeypatch, profile):
    agents, profiles, finish_order = [], [], []

    def create(_):
        agent = FakeAgent()
        agents.append(agent)
        return agent

    async def reload(page, case):
        page.url = case["final_url"]
        return page

    async def fill(page, local_profile, agent, **_):
        index = int(page.url.rsplit("/", 1)[1])
        assert local_profile is not profile
        assert local_profile.facts == profile.facts
        assert local_profile.application == profile.application
        assert not agent.calls and not agent.data["request_ids"]
        assert page.context.websocket_blocked
        profiles.append(local_profile)
        local_profile.facts["first_name"] = f"case-{index}"
        local_profile.application.notice_period_days = 100 + index
        agent.calls = 3 if index == 0 else 1
        agent.data.update(
            operation_calls={"mapping": 1},
            operation_attempts={"mapping": agent.calls},
            retries=2 if index == 0 else 0,
            failures={"rate_limited": 2} if index == 0 else {},
            input_tokens=10 + index,
            output_tokens=5,
            request_ids=[f"request-{index}"],
            last_error={"category": "invalid_structured_output"} if index == 0 else None,
        )
        await asyncio.sleep(0.06 if index == 0 else 0.01)
        finish_order.append(str(index))
        return {
            "field_count": 1,
            "mapped_count": 1,
            "filled_count": int(index != 0),
            "completed_values_retained": index != 0,
            "required_answers_missing": [],
            "page_validation_error_count": 0,
            "required_fill_pass": index != 0,
            "agent_warnings": [{"message": "private bad-case warning"}] if index == 0 else [],
            "fields": [{"label": f"private field {index}"}],
        }

    monkeypatch.setattr(module, "create_agent", create)
    monkeypatch.setattr(module, "reload_form", reload)
    monkeypatch.setattr(module, "probe_fields", fill)
    return agents, profiles, finish_order


@pytest.mark.parametrize("pool_enabled", [False, True])
async def test_bounded_probe_reuses_only_browsers_and_isolates_agent_profile_state(
    tmp_path,
    profile,
    monkeypatch,
    pool_enabled,
):
    module = probe_module()
    args = arguments(tmp_path, pool=pool_enabled)
    chromium = FakeChromium()
    agents, profiles, finish_order = install_case_fakes(module, monkeypatch, profile)
    snapshots = []
    real_write = module.write_report

    def record(path, payload):
        snapshots.append(copy.deepcopy(payload))
        real_write(path, payload)

    monkeypatch.setattr(module, "write_report", record)
    template = module.agent_metadata(FakeAgent(), agent_fill=True)
    if pool_enabled:
        async with BrowserPool(chromium, 2) as pool:
            rows = await module.probe_cases(cases(5), chromium, profile, args, template, pool=pool)
    else:
        rows = await module.probe_cases(cases(5), chromium, profile, args, template)
    assert finish_order[0] == "1"
    assert [row["id"] for row in rows] == list(map(str, range(5)))
    assert len(agents) == len(profiles) == 5
    assert len({id(item) for item in agents}) == 5
    assert len({id(item.application) for item in profiles}) == 5
    assert chromium.peak == 2 and chromium.live == 0
    assert len(chromium.browsers) == (2 if pool_enabled else 5)
    assert all(context.closed for browser in chromium.browsers for context in browser.contexts)
    assert sum(len(browser.contexts) for browser in chromium.browsers) == 5
    assert rows[0]["code"] == "FIELD_FILL_FAILED"
    assert all(row["code"] == "MAPPED_FIELDS_VERIFIED" for row in rows[1:])
    assert rows[0]["agent_warnings"] and all(not row["agent_warnings"] for row in rows[1:])
    for partial in snapshots:
        ids = [int(row["id"]) for row in partial["results"]]
        assert ids == sorted(ids)
        assert partial["agent"]["calls_attempted"] == sum(
            row["agent_calls_attempted"] for row in partial["results"]
        )
    report = json.loads(args.output.read_text(encoding="utf-8"))
    assert report["pool_enabled"] is pool_enabled
    assert report["agent"]["calls_attempted"] == 7
    assert report["agent"]["metrics"] == {
        "operation_calls": {"mapping": 5},
        "operation_attempts": {"mapping": 7},
        "failures": {"rate_limited": 2},
        "retries": 2,
        "input_tokens": 60,
        "output_tokens": 25,
        "request_ids": [f"request-{index}" for index in range(5)],
        "last_error": {"category": "invalid_structured_output"},
    }
    assert profile.facts["first_name"] == "Alex"


async def test_parallel_heldout_report_does_not_persist_or_print_field_details(
    tmp_path,
    profile,
    monkeypatch,
    capsys,
):
    module = probe_module()
    args = arguments(tmp_path, aggregate_only=True)
    install_case_fakes(module, monkeypatch, profile)
    rows = await module.probe_cases(
        cases(2),
        FakeChromium(),
        profile,
        args,
        module.agent_metadata(FakeAgent()),
    )
    assert all("fields" not in row and "agent_warnings" not in row for row in rows)
    assert "private" not in args.output.read_text(encoding="utf-8")
    assert "private" not in capsys.readouterr().out


async def test_case_setup_failure_closes_context_and_does_not_abort_peers(
    tmp_path, profile, monkeypatch
):
    module = probe_module()
    args = arguments(tmp_path)
    install_case_fakes(module, monkeypatch, profile)
    chromium = FakeChromium(fail_first_setup=True)
    rows = await module.probe_cases(
        cases(2), chromium, profile, args, module.agent_metadata(FakeAgent())
    )
    assert [row["code"] for row in rows] == ["BROWSER_ERROR", "MAPPED_FIELDS_VERIFIED"]
    assert all(context.closed for browser in chromium.browsers for context in browser.contexts)
    assert all(browser.closed for browser in chromium.browsers)


async def test_cancelled_case_closes_context_during_setup(tmp_path, profile, monkeypatch):
    module = probe_module()
    monkeypatch.setattr(module, "create_agent", lambda _: FakeAgent())
    chromium = FakeChromium()
    browser = await chromium.launch()
    entered = asyncio.Event()

    async def block_setup(self, *_):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(FakeContext, "route_web_socket", block_setup)
    task = asyncio.create_task(
        module.probe_case(cases(1)[0], browser, profile, arguments(tmp_path))
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert browser.contexts[0].closed
    await browser.close()


@pytest.mark.parametrize("pool_enabled", [False, True])
async def test_browser_start_failure_is_one_case_result(
    tmp_path, profile, monkeypatch, pool_enabled
):
    module = probe_module()
    args = arguments(tmp_path, pool=pool_enabled)
    install_case_fakes(module, monkeypatch, profile)
    chromium = FakeChromium(fail_first_launch=True)
    template = module.agent_metadata(FakeAgent())
    if pool_enabled:
        async with BrowserPool(chromium, 2) as pool:
            rows = await module.probe_cases(cases(3), chromium, profile, args, template, pool=pool)
    else:
        rows = await module.probe_cases(cases(3), chromium, profile, args, template)
    assert [row["code"] for row in rows] == [
        "BROWSER_ERROR",
        "MAPPED_FIELDS_VERIFIED",
        "MAPPED_FIELDS_VERIFIED",
    ]
    assert rows[0]["agent_calls_attempted"] == 0
    assert all(browser.closed for browser in chromium.browsers)


async def test_parallel_local_probe_keeps_offline_guards_and_closes_all_contexts(
    tmp_path,
    portal,
    profile,
    monkeypatch,
):
    module = probe_module()
    monkeypatch.setattr(module, "create_agent", lambda _: None)
    args = arguments(tmp_path, pool=True)
    args.agent_fill = False
    base, server = portal
    local_cases = [{**case, "final_url": f"{base}/single", "field_count": 4} for case in cases(2)]
    original = module.probe_fields
    seen_contexts = []

    async def observed_probe(page, local_profile, agent, **kwargs):
        await page.evaluate("""() => document.querySelector('form').addEventListener('input', () => {
          fetch('/autosave', {method:'POST', body:'profile-data'}).catch(()=>{});
          navigator.sendBeacon('/beacon', 'profile-data');
        })""")
        result = await original(page, local_profile, agent, **kwargs)
        assert not await page.evaluate("navigator.onLine")
        seen_contexts.append(page.context)
        return result

    monkeypatch.setattr(module, "probe_fields", observed_probe)
    async with async_playwright() as playwright, BrowserPool(playwright.chromium, 2) as pool:
        rows = await module.probe_cases(
            local_cases,
            playwright.chromium,
            profile,
            args,
            module.agent_metadata(None),
            pool=pool,
        )
        assert len({id(context) for context in seen_contexts}) == 2
        assert all(not context.pages for context in seen_contexts)
    assert all(row["code"] == "MAPPED_FIELDS_VERIFIED" for row in rows)
    assert not server.submissions
