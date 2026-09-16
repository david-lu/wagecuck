import argparse
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from wagecuck.evaluation import (
    MAX_CONCURRENCY,
    BrowserPool,
    add_concurrency_argument,
    default_report_path,
    run_cases,
    write_report,
)


async def test_workers_overlap_with_bound_and_return_input_order():
    active = peak = 0
    completed = []
    first_wave = asyncio.Event()
    started = []

    async def worker(case):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append(case)
        if len(started) == 3:
            first_wave.set()
        await asyncio.wait_for(first_wave.wait(), 1)
        await asyncio.sleep((2 - case % 3) * .01)
        active -= 1
        return {"case": case}

    results = await run_cases(
        list(range(100)), worker, concurrency=3,
        on_result=lambda index, result: completed.append(index),
    )
    assert peak == 3 and active == 0
    assert [row["case"] for row in results] == list(range(100))
    assert completed != list(range(100)) and sorted(completed) == list(range(100))


async def test_unexpected_failure_cancels_and_awaits_other_workers():
    second_started = asyncio.Event()
    cleaned = []

    async def worker(case):
        try:
            if case == 0:
                await second_started.wait()
                raise RuntimeError("diagnostic failed")
            second_started.set()
            await asyncio.Event().wait()
        finally:
            cleaned.append(case)

    with pytest.raises(ExceptionGroup, match="TaskGroup"):
        await asyncio.wait_for(run_cases([0, 1, 2], worker, concurrency=2), 1)
    assert sorted(cleaned) == [0, 1]


@pytest.mark.parametrize("value", [0, -1, MAX_CONCURRENCY + 1, 100, True, 1.5])
async def test_hard_limit_cannot_be_bypassed_programmatically(value):
    async def worker(case):
        raise AssertionError("Worker must not start")

    with pytest.raises(ValueError, match="between 1 and 8"):
        await run_cases([1], worker, concurrency=value)
    with pytest.raises(ValueError, match="between 1 and 8"):
        BrowserPool(None, value)


@pytest.mark.parametrize("value", ["0", "-1", "9", "100", "1.5", "lots"])
def test_cli_rejects_invalid_concurrency_before_launch(value):
    parser = argparse.ArgumentParser()
    add_concurrency_argument(parser)
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["--concurrency", value, "--pool"])
    assert error.value.code == 2


class FakeBrowser:
    def __init__(self):
        self.connected = True
        self.closed = False

    def is_connected(self):
        return self.connected

    async def close(self):
        self.closed = True
        self.connected = False


class FakeLauncher:
    def __init__(self):
        self.browsers = []

    async def launch(self):
        browser = FakeBrowser()
        self.browsers.append(browser)
        return browser


async def test_pool_reuses_fixed_slots_and_closes_all_browsers():
    launcher = FakeLauncher()
    leased = []
    async with BrowserPool(launcher, 2) as pool:
        async def worker(case):
            async with pool.lease() as browser:
                leased.append(browser)
                await asyncio.sleep(.01)
                return case

        assert await run_cases(list(range(7)), worker, concurrency=4) == list(range(7))
        assert len(launcher.browsers) == 2
        assert len(set(leased)) == 2
        assert not any(browser.closed for browser in launcher.browsers)
    assert all(browser.closed for browser in launcher.browsers)


async def test_pool_recovers_disconnected_browser_and_releases_after_failure():
    launcher = FakeLauncher()
    async with BrowserPool(launcher, 1) as pool:
        with pytest.raises(RuntimeError, match="case failed"):
            async with pool.lease() as browser:
                browser.connected = False
                raise RuntimeError("case failed")
        async with pool.lease() as replacement:
            assert replacement is not browser and replacement.is_connected()
        assert len(launcher.browsers) == 2
    assert all(browser.closed for browser in launcher.browsers)


async def test_pool_releases_lease_after_cancelled_case():
    launcher = FakeLauncher()
    ready = asyncio.Event()
    async with BrowserPool(launcher, 1) as pool:
        async def hang():
            async with pool.lease():
                ready.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(hang())
        await asyncio.wait_for(ready.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with asyncio.timeout(1):
            async with pool.lease() as browser:
                assert browser is launcher.browsers[0]
    assert launcher.browsers[0].closed


def test_atomic_report_failure_preserves_last_complete_snapshot(tmp_path, monkeypatch):
    report = tmp_path / "report.json"
    write_report(report, {"results": ["first"]})

    def reject_replace(self, target):
        assert json.loads(report.read_text()) == {"results": ["first"]}
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", reject_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_report(report, {"results": ["second"]})
    assert json.loads(report.read_text()) == {"results": ["first"]}
    assert list(tmp_path.iterdir()) == [report]


def test_separate_evaluations_have_distinct_default_and_intermediate_reports(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "parallel_dry_run_cli", Path(__file__).parents[1] / "scripts/dry_run_all.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = tmp_path / "jobs.json"
    manifest.write_text(json.dumps({"split": "training", "cases": [
        {"id": "one", "url": "https://example.test/job"}
    ]}))
    commands = []

    def fake_run(command, *, check):
        assert check
        commands.append(command)
        assert command[command.index("--concurrency") + 1] == "3"
        assert "--pool" in command
        if Path(command[1]).name == "probe_live.py":
            write_report(Path(command[command.index("--output") + 1]), {
                "checked_at": "test", "results": [
                    {"id": "one", "url": "https://example.test/job", "code": "JOB_CLOSED"}
                ],
            })

    monkeypatch.setattr(module, "create_agent", lambda args: None)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "default_report_path", lambda prefix, **kwargs:
        default_report_path(prefix, directory=tmp_path))
    monkeypatch.setattr(module.sys, "argv", [
        "dry_run_all.py", "--manifests", str(manifest), "--concurrency", "3", "--pool"
    ])
    module.main()
    module.main()
    outputs = [cmd[cmd.index("--output") + 1] for cmd in commands]
    assert len(outputs) == len(set(outputs)) == 4
    assert all(Path(path).parent == tmp_path for path in outputs)
