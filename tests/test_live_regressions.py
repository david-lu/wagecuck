import asyncio
import importlib.util
from pathlib import Path

from playwright.async_api import async_playwright

from wagecuck import ApplicationRunner
from wagecuck.browser import blocker
from wagecuck.models import Code, Snapshot


async def test_delayed_apply_navigation_ignores_career_search(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner().run(f"{base}/delayed-entry", profile, options)
    assert result.success
    assert len(server.submissions) == 1


async def test_search_box_is_not_an_application(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner().run(
        f"{base}/search-only", profile, options.model_copy(update={"mode": "inspect"})
    )
    assert result.code == Code.FORM_NOT_FOUND
    assert not server.submissions


def test_expired_listing_variants():
    assert (
        blocker(
            Snapshot(
                url="https://jobs.smartrecruiters.com/a/1", title="", text="This job has expired"
            )
        )
        == Code.JOB_CLOSED
    )
    assert (
        blocker(Snapshot(url="https://jobs.jobvite.com/careers/a/jobs?error=404", title=""))
        == Code.JOB_CLOSED
    )


async def test_offline_probe_blocks_autosave_and_submission(portal, profile):
    module_path = Path(__file__).parents[1] / "scripts" / "probe_live.py"
    spec = importlib.util.spec_from_file_location("probe_live", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base, server = portal
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(service_workers="block")
        await context.route_web_socket("**/*", lambda ws: ws.close())
        page = await context.new_page()
        await page.goto(f"{base}/single")
        await page.evaluate("""() => {
          document.querySelector('form').addEventListener('input', () => {
            fetch('/autosave', {method:'POST', body:'profile-data'}).catch(()=>{});
            navigator.sendBeacon('/beacon', 'profile-data');
          });
        }""")
        result = await module.probe_fields(page, profile)
        assert result["filled_count"] == 4
        assert result["completed_values_retained"]
        assert await page.evaluate("navigator.onLine") is False
        # Even if a page callback tries to submit, the transport stays blocked.
        await page.evaluate("document.querySelector('form').requestSubmit()")
        await asyncio.sleep(0.3)
        assert not server.submissions
        await browser.close()
