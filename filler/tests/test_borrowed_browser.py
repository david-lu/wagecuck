import asyncio
import json
from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from wagecuck import ApplicationRunner
from wagecuck.models import Code


@pytest.fixture
async def browser():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        yield browser
        await browser.close()


async def test_parallel_runs_borrow_browser_with_isolated_contexts_and_artifacts(
    browser, portal, profile, options, monkeypatch
):
    base, server = portal
    original_workflow = ApplicationRunner._workflow
    started = []
    filled = {}
    both_started = asyncio.Event()

    async def workflow(runner, page, profile, *args):
        context = page.context
        await context.add_cookies([{"name": "case", "value": profile.id, "url": base}])
        started.append(context)
        if len(started) == 2:
            both_started.set()
        await both_started.wait()
        assert len(browser.contexts) == 2
        assert (await context.cookies())[0]["value"] == profile.id
        await original_workflow(runner, page, profile, *args)
        filled[profile.id] = await page.get_by_label("Email", exact=False).input_value()

    monkeypatch.setattr(ApplicationRunner, "_workflow", workflow)
    profiles = [
        profile.model_copy(deep=True, update={"id": f"parallel-{index}"}) for index in range(2)
    ]
    for index, applicant in enumerate(profiles):
        applicant.facts["email"] = f"parallel-{index}@example.com"
    options = options.model_copy(update={"mode": "fill", "capture_sensitive_artifacts": True})
    async with asyncio.timeout(20):
        results = await asyncio.gather(
            *[
                ApplicationRunner().run(f"{base}/single", applicant, options, browser=browser)
                for applicant in profiles
            ]
        )
    assert [result.code for result in results] == [Code.READY, Code.READY]
    assert len({id(context) for context in started}) == 2
    assert len({result.run_id for result in results}) == 2
    for result, applicant in zip(results, profiles, strict=True):
        assert filled[applicant.id] == applicant.facts["email"]
        directory = Path(result.artifact_dir)
        stored = json.loads((directory / "result.json").read_text())
        assert stored["run_id"] == result.run_id and stored["profile_id"] == applicant.id
        assert (directory / "events.jsonl").is_file()
        assert (directory / "final.png").is_file() and (directory / "trace.zip").is_file()
        assert not result.submission_attempted
    assert browser.is_connected() and not browser.contexts and not server.submissions


@pytest.mark.parametrize("path,code", [("/single", Code.INSPECTED), ("/missing", Code.JOB_CLOSED)])
async def test_borrowed_browser_keeps_other_contexts_on_success_or_http_failure(
    browser, portal, profile, options, path, code
):
    base, server = portal
    existing = await browser.new_context()
    page = await existing.new_page()
    result = await ApplicationRunner().run(
        f"{base}{path}", profile, options.model_copy(update={"mode": "inspect"}), browser=browser
    )
    assert result.code == code
    assert browser.is_connected() and browser.contexts == [existing]
    assert await page.evaluate("1 + 1") == 2
    assert not server.submissions
    await existing.close()


async def test_navigation_failure_closes_only_borrowed_run_context(
    browser, profile, options, monkeypatch
):
    new_context = browser.new_context

    async def blocked_context(**kwargs):
        context = await new_context(**kwargs)
        await context.route("**/*", lambda route: route.abort())
        return context

    monkeypatch.setattr(browser, "new_context", blocked_context)
    result = await ApplicationRunner().run(
        "https://example.invalid/application",
        profile,
        options.model_copy(update={"mode": "fill"}),
        browser=browser,
    )
    assert result.code == Code.NAVIGATION_FAILED
    assert browser.is_connected() and not browser.contexts


async def test_context_setup_failure_does_not_leak_context_or_close_borrowed_browser(
    browser, portal, profile, options, monkeypatch
):
    new_context = browser.new_context

    async def broken_context(**kwargs):
        context = await new_context(**kwargs)

        async def fail_page():
            raise PlaywrightError("Test page creation failure")

        monkeypatch.setattr(context, "new_page", fail_page)
        return context

    monkeypatch.setattr(browser, "new_context", broken_context)
    base, server = portal
    result = await ApplicationRunner().run(
        f"{base}/single", profile, options.model_copy(update={"mode": "fill"}), browser=browser
    )
    assert result.code == Code.BROWSER_ERROR
    assert browser.is_connected() and not browser.contexts and not server.submissions


async def test_cancellation_closes_run_context_and_preserves_borrowed_browser_and_trace(
    browser, portal, profile, options, monkeypatch
):
    base, server = portal
    entered = asyncio.Event()

    async def wait_for_cancellation(*args):
        entered.set()
        await asyncio.Event().wait()

    runner = ApplicationRunner()
    monkeypatch.setattr(runner, "_workflow", wait_for_cancellation)
    options = options.model_copy(update={"mode": "fill", "capture_sensitive_artifacts": True})
    task = asyncio.create_task(runner.run(f"{base}/single", profile, options, browser=browser))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert browser.is_connected() and not browser.contexts and not server.submissions
    results = list(options.artifacts_dir.glob("*/result.json"))
    assert len(results) == 1
    assert (results[0].parent / "trace.zip").is_file()
    assert (results[0].parent / "final.png").is_file()


async def test_workflow_timeout_preserves_borrowed_browser(
    browser, portal, profile, options, monkeypatch
):
    async def hang(*args):
        await asyncio.Event().wait()

    base, server = portal
    runner = ApplicationRunner()
    monkeypatch.setattr(runner, "_workflow", hang)
    result = await runner.run(
        f"{base}/single",
        profile,
        options.model_copy(update={"mode": "fill", "timeout_seconds": 0.5}),
        browser=browser,
    )
    assert result.code == Code.TIMEOUT
    assert browser.is_connected() and not browser.contexts and not server.submissions
