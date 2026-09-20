from wagecuck import ApplicationRunner
from wagecuck.models import Code


async def test_final_diff_catches_later_mutation(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner().run(f"{base}/mutating", profile, options)
    assert result.code == Code.VALIDATION_FAILED
    assert not server.submissions


async def test_apply_modal_advances_to_account_gate(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner().run(f"{base}/modal", profile, options)
    assert result.code == Code.AUTH_REQUIRED
    assert not server.submissions


async def test_explicit_submit_wins_over_decorative_apply_button(portal, profile, options):
    base, server = portal
    result = await ApplicationRunner().run(f"{base}/decorative-apply", profile, options)
    assert result.code == Code.OK
    assert result.submitted and len(server.submissions) == 1


async def test_capsolver_missing_key_does_not_mark_submission_attempt(
    portal, profile, options, monkeypatch
):
    monkeypatch.delenv("CAPSOLVER_API_KEY", raising=False)
    base, server = portal
    result = await ApplicationRunner().run(f"{base}/captcha-form", profile, options)
    assert result.code == Code.CAPTCHA_KEY_MISSING
    assert not result.submission_attempted and not server.submissions
