import asyncio
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from wagecuck.evaluation import BrowserPool, Corpus, run_cases
from wagecuck.models import Code, FormField, Snapshot


def inspect_script():
    spec = importlib.util.spec_from_file_location(
        "inspect_live", Path(__file__).parents[1] / "scripts" / "inspect_live.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_parallel_inspection_is_bounded_ordered_and_isolated(profile, monkeypatch):
    inspection = inspect_script()
    original_name = profile.facts["first_name"]
    cases = [
        {"id": name, "url": f"https://example.test/{name}"}
        for name in ("slow", "fast", "typed-failure", "raised-failure", "last")
    ]
    active = maximum = 0
    instances, copies, artifact_roots, completion = [], [], [], []

    class Runner:
        def __init__(self):
            instances.append(self)

        async def run(self, url, case_profile, options):
            nonlocal active, maximum
            assert options.mode == "inspect" and not options.agent_fill
            assert case_profile is not profile
            assert case_profile.facts["first_name"] == original_name
            copies.append(case_profile)
            case_profile.facts["first_name"] = "Changed locally"
            artifact_roots.append(options.artifacts_dir)
            case_id = url.rsplit("/", 1)[-1]
            active += 1
            maximum = max(active, maximum)
            try:
                await asyncio.sleep(0.04 if case_id == "slow" else 0.002)
                if case_id == "raised-failure":
                    raise RuntimeError("Do not persist this diagnostic payload")
                directory = options.artifacts_dir / case_id
                directory.mkdir()
                code = Code.NAVIGATION_FAILED if case_id == "typed-failure" else Code.INSPECTED
                if code == Code.INSPECTED:
                    snapshot = Snapshot(
                        url=url,
                        title="Example",
                        fields=[
                            FormField(
                                id="name", frame=0, kind="text", label="First name", required=True
                            ),
                            FormField(
                                id="email", frame=0, kind="email", label="Email", required=True
                            ),
                        ],
                    )
                    (directory / "step-1.json").write_text(
                        snapshot.model_dump_json(), encoding="utf-8"
                    )
                return SimpleNamespace(
                    code=code,
                    message="Example outcome",
                    ats="generic",
                    final_url=url,
                    run_id=case_id,
                    artifact_dir=str(directory),
                )
            finally:
                active -= 1

    monkeypatch.setattr(inspection, "ApplicationRunner", Runner)

    async def worker(case):
        return await inspection.inspect_case(case, profile, ephemeral_artifacts=True)

    rows = await run_cases(
        cases, worker, concurrency=2, on_result=lambda index, row: completion.append(index)
    )
    assert maximum == 2 and active == 0
    assert completion[0] != 0
    assert [row["id"] for row in rows] == [case["id"] for case in cases]
    assert [row["code"] for row in rows] == [
        Code.INSPECTED,
        Code.INSPECTED,
        Code.NAVIGATION_FAILED,
        Code.INTERNAL_ERROR,
        Code.INSPECTED,
    ]
    assert all(row["identity_fields_detected"] for row in (rows[0], rows[1], rows[4]))
    assert "Do not persist" not in json.dumps(rows)
    assert len(instances) == len(cases) and len({id(value) for value in copies}) == len(cases)
    assert profile.facts["first_name"] == original_name
    assert len(set(artifact_roots)) == len(cases)
    assert all(not path.exists() for path in artifact_roots)


async def test_inspection_bad_artifact_is_local_and_holdout_output_stays_aggregate(
    profile, monkeypatch
):
    inspection = inspect_script()
    artifact_roots = []

    class Runner:
        async def run(self, url, case_profile, options):
            artifact_roots.append(options.artifacts_dir)
            directory = options.artifacts_dir / "case"
            directory.mkdir()
            (directory / "step-1.json").write_text("private question: not JSON", encoding="utf-8")
            return SimpleNamespace(
                code=Code.INSPECTED,
                message="Inspected",
                ats="generic",
                final_url=url,
                run_id="case",
                artifact_dir=str(directory),
            )

    monkeypatch.setattr(inspection, "ApplicationRunner", Runner)
    row = await inspection.inspect_case(
        {"id": "bad", "url": "https://example.test/bad"},
        profile,
        aggregate_only=True,
        ephemeral_artifacts=True,
    )
    assert row["code"] == Code.INTERNAL_ERROR
    assert row["diagnostic_error"] == {"stage": "snapshot", "type": "JSONDecodeError"}
    assert "fields" not in row and "required_answers_missing" not in row
    assert "private question" not in json.dumps(row)
    assert all(not path.exists() for path in artifact_roots)


@pytest.mark.parametrize("pool_enabled", [False, True])
async def test_inspection_main_collects_ordered_partial_reports(
    profile, tmp_path, monkeypatch, pool_enabled
):
    inspection = inspect_script()
    cases = tuple({"id": name, "url": f"https://example.test/{name}"} for name in ("a", "b"))
    report_path = tmp_path / "reports" / "unique-inspection.json"
    writes = []
    write_report = inspection.write_report
    monkeypatch.setattr(
        inspection, "load_corpus", lambda *args, **kwargs: Corpus("training", (), cases)
    )
    monkeypatch.setattr(inspection.Profile, "load", lambda path: profile)
    monkeypatch.setattr(inspection, "default_report_path", lambda prefix: report_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["inspect_live.py", "--concurrency", "2"] + (["--pool"] if pool_enabled else []),
    )
    started = 0
    both_started = asyncio.Event()

    async def inspect_case(case, case_profile, **kwargs):
        nonlocal started
        assert (kwargs["browser"] is not None) is pool_enabled
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=10)
        await asyncio.sleep(0.03 if case["id"] == "a" else 0)
        return {"id": case["id"], "code": Code.INSPECTED}

    def record(path, payload):
        write_report(path, payload)
        writes.append(json.loads(path.read_text(encoding="utf-8")))

    monkeypatch.setattr(inspection, "inspect_case", inspect_case)
    monkeypatch.setattr(inspection, "write_report", record)
    await inspection.main()
    assert [[row["id"] for row in payload["results"]] for payload in writes] == [
        [],
        ["b"],
        ["a", "b"],
    ]
    assert all(payload["concurrency"] == 2 for payload in writes)
    assert all(payload["pool_enabled"] is pool_enabled for payload in writes)
    assert all(payload["applicant_data_entered"] is False for payload in writes)
    assert all(payload["applications_submitted"] == 0 for payload in writes)
    assert json.loads(report_path.read_text(encoding="utf-8")) == writes[-1]


async def test_parallel_browser_inspection_keeps_artifacts_distinct_and_forms_empty(
    portal, profile, tmp_path, monkeypatch
):
    inspection = inspect_script()
    base, server = portal
    actual_runner = inspection.ApplicationRunner
    artifact_root = tmp_path / "artifacts"

    class LocalRunner(actual_runner):
        async def run(self, url, case_profile, options):
            options.artifacts_dir = artifact_root
            return await super().run(url, case_profile, options)

    monkeypatch.setattr(inspection, "ApplicationRunner", LocalRunner)
    cases = [{"id": name, "url": f"{base}/{name}"} for name in ("single", "radio", "missing")]

    async def worker(case):
        return await inspection.inspect_case(case, profile)

    rows = await run_cases(cases, worker, concurrency=3)
    assert [row["id"] for row in rows] == ["single", "radio", "missing"]
    assert [row["code"] for row in rows] == [Code.INSPECTED, Code.INSPECTED, Code.JOB_CLOSED]
    assert len({row["run_id"] for row in rows}) == 3
    assert len(list(artifact_root.iterdir())) == 3
    for row in rows[:2]:
        snapshots = list((artifact_root / row["run_id"]).glob("step-*.json"))
        assert snapshots
        for path in snapshots:
            fields = Snapshot.model_validate_json(path.read_text(encoding="utf-8")).fields
            assert fields and not any(field.filled for field in fields)
    assert server.submissions == []


async def test_inspection_pool_reuses_browser_with_fresh_empty_contexts(
    portal, profile, tmp_path, monkeypatch
):
    inspection = inspect_script()
    base, server = portal
    actual_runner = inspection.ApplicationRunner
    contexts, browser_ids = [], []

    class LocalRunner(actual_runner):
        async def run(self, url, case_profile, options, *, browser=None):
            options.artifacts_dir = tmp_path / "pooled-artifacts"
            return await super().run(url, case_profile, options, browser=browser)

    monkeypatch.setattr(inspection, "ApplicationRunner", LocalRunner)
    async with async_playwright() as playwright, BrowserPool(playwright.chromium, 1) as pool:
        async with pool.lease() as browser:
            original_new_context = browser.new_context

            async def new_context(**kwargs):
                context = await original_new_context(**kwargs)
                contexts.append(context)
                assert await context.cookies() == []
                page = await context.new_page()
                await page.goto(f"{base}/single")
                assert await page.evaluate("localStorage.getItem('previous')") is None
                await page.evaluate("localStorage.setItem('previous', 'case')")
                await context.add_cookies([{"name": "previous", "value": "case", "url": base}])
                await page.close()
                return context

            monkeypatch.setattr(browser, "new_context", new_context)

        async def worker(case):
            async with pool.lease() as browser:
                browser_ids.append(id(browser))
                assert browser.contexts == []
                row = await inspection.inspect_case(case, profile, browser=browser)
                assert browser.contexts == []
                return row

        cases = [{"id": name, "url": f"{base}/{name}"} for name in ("single", "missing", "radio")]
        rows = await run_cases(cases, worker, concurrency=1)
    assert [row["code"] for row in rows] == [Code.INSPECTED, Code.JOB_CLOSED, Code.INSPECTED]
    assert len(set(browser_ids)) == 1
    assert len(contexts) == len(cases) and len({id(context) for context in contexts}) == len(cases)
    assert len({row["run_id"] for row in rows}) == len(cases)
    assert server.submissions == []


async def test_inspection_pool_launch_failure_does_not_cancel_queued_cases(
    profile, tmp_path, monkeypatch
):
    inspection = inspect_script()
    cases = tuple({"id": name, "url": f"https://example.test/{name}"} for name in ("a", "b", "c"))
    report_path = tmp_path / "pool-report.json"
    monkeypatch.setattr(
        inspection, "load_corpus", lambda *args, **kwargs: Corpus("training", (), cases)
    )
    monkeypatch.setattr(inspection.Profile, "load", lambda path: profile)
    monkeypatch.setattr(
        sys, "argv", ["inspect_live.py", "--pool", "--aggregate-only", "--output", str(report_path)]
    )

    class FailingPool:
        def __init__(self, browser_type, size):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        @asynccontextmanager
        async def lease(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Sensitive startup details")
            yield object()

    async def inspect_case(case, case_profile, **kwargs):
        return {"id": case["id"], "code": Code.INSPECTED}

    monkeypatch.setattr(inspection, "BrowserPool", FailingPool)
    monkeypatch.setattr(inspection, "inspect_case", inspect_case)
    await inspection.main()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [row["id"] for row in report["results"]] == ["a", "b", "c"]
    assert [row["code"] for row in report["results"]] == [
        Code.BROWSER_ERROR,
        Code.INSPECTED,
        Code.INSPECTED,
    ]
    assert "fields" not in report["results"][0]
    assert "Sensitive startup details" not in json.dumps(report)
