import asyncio

import pytest
from playwright.async_api import BrowserType
from pydantic import ValidationError

from wagecuck import ApplicationRunner, RunOptions
from wagecuck.models import Code


@pytest.fixture
def manual_options(options, monkeypatch):
    # Check the visible launch request, but avoid requiring a display in CI.
    launch = BrowserType.launch

    async def test_launch(self, **kwargs):
        assert kwargs["headless"] is False
        return await launch(self, **{**kwargs, "headless": True})

    monkeypatch.setattr(BrowserType, "launch", test_launch)
    return RunOptions(**{**options.model_dump(), "mode": "fill", "wait_for_user": True})


@pytest.mark.parametrize("route", ["single", "multi", "captcha-form"])
async def test_waits_then_observes_user(portal, profile, manual_options, route):
    base, server = portal
    runner = ApplicationRunner()
    wait = runner._wait_for_user

    async def user_handoff(page, result, event, baseline):
        assert not result.submission_attempted

        async def user():
            await asyncio.sleep(0.7)
            assert not server.submissions
            if route != "multi":
                assert await page.locator("[name=email]").input_value() == profile.facts["email"]
                assert await page.locator("[name=resume]").evaluate("e => e.files.length") == 1
                await page.locator("[name=first_name]").fill("Edited by user")
            await page.get_by_role("button", name="Submit application").click()

        task = asyncio.create_task(user())
        try:
            await wait(page, result, event, baseline)
            await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    runner._wait_for_user = user_handoff
    result = await asyncio.wait_for(runner.run(f"{base}/{route}", profile, manual_options), 45)
    assert result.success and result.user_handoff
    assert result.code == Code.OK
    assert len(server.submissions) == 1
    if route != "multi":
        assert b"Edited by user" in server.submissions[0][1]
    duplicate = await runner.run(f"{base}/{route}", profile, manual_options)
    assert duplicate.code == Code.ALREADY_SUBMITTED


async def test_close_browser_ends_wait(portal, profile, manual_options):
    base, server = portal
    runner = ApplicationRunner()
    wait = runner._wait_for_user

    async def close(page, result, event, baseline):
        await page.close()
        await wait(page, result, event, baseline)

    runner._wait_for_user = close
    result = await runner.run(f"{base}/single", profile, manual_options)
    assert result.status == "unknown" and result.code == Code.USER_SUBMISSION_UNCONFIRMED
    assert result.user_handoff and not result.submission_attempted and not result.retryable
    assert not server.submissions
    duplicate = await runner.run(f"{base}/single", profile, manual_options)
    assert duplicate.code == Code.PRIOR_SUBMISSION_UNCERTAIN


def test_wait_requires_fill_and_visible_browser():
    assert RunOptions(wait_for_user=True).headless is False
    for mode in ("inspect", "submit"):
        with pytest.raises(ValidationError):
            RunOptions(mode=mode, wait_for_user=True)


def test_cli_rejects_headless_wait(monkeypatch, capsys):
    from wagecuck.cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "wagecuck",
            "run",
            "http://example.com",
            "--profile",
            "unused.json",
            "--wait-for-user",
            "--headless",
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "cannot be combined with --headless" in capsys.readouterr().err


async def test_wait_removes_automatic_timeout(portal, profile, manual_options, monkeypatch):
    import wagecuck.runner as module

    timeouts = []
    original_timeout = asyncio.timeout

    def track_timeout(seconds):
        budget = original_timeout(seconds)
        timeouts.append(budget)
        return budget

    monkeypatch.setattr(module.asyncio, "timeout", track_timeout)
    runner = ApplicationRunner()

    async def close(page, result, event, baseline):
        assert timeouts[0].when() is None
        await page.close()

    runner._wait_for_user = close
    result = await runner.run(f"{portal[0]}/single", profile, manual_options)
    assert result.user_handoff
