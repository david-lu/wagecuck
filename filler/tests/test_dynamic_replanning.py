import pytest

from wagecuck import ApplicationRunner
from wagecuck.fixtures import Handler
from wagecuck.models import Code


@pytest.mark.parametrize("behavior", ["replacement", "options", "requirement"])
async def test_runner_replans_meaningful_dynamic_changes(
    portal, profile, options, monkeypatch, behavior
):
    base, server = portal
    old_handler = Handler.do_GET
    changes = {
        "replacement": "document.querySelector('#email').outerHTML='<input id=email type=email required>'",
        "options": "document.querySelector('#country').innerHTML='<option value=ca>Canada</option>'",
        "requirement": "document.querySelector('#extra').required=true",
    }
    extra = {
        "replacement": '<label>Email<input id="email" type="email" required></label>',
        "options": '<label>Email<input type="email" required></label><label>Country<select id="country" required><option value="">Choose</option></select></label>',
        "requirement": '<label>Email<input type="email" required></label><label>Unknown clearance<input id="extra"></label>',
    }

    def get(handler):
        if handler.path != "/dynamic":
            return old_handler(handler)
        change = changes[behavior]
        handler.respond(f"""<form><label>First Name<input required onblur="if(!window.changed){{window.changed=true;setTimeout(()=>{{{change}}},100)}}"></label>
          {extra[behavior]}<button type="submit">Submit application</button></form>""")

    monkeypatch.setattr(Handler, "do_GET", get)
    result = await ApplicationRunner().run(
        f"{base}/dynamic", profile, options.model_copy(update={"mode": "fill"})
    )
    assert result.code == (
        Code.REQUIRED_ANSWER_MISSING if behavior == "requirement" else Code.READY
    ), result.model_dump()
    assert result.steps <= 3
    assert not result.submission_attempted and not server.submissions


async def test_continuous_dom_replacement_stops_within_budget(
    portal, profile, options, monkeypatch
):
    base, server = portal
    old_handler = Handler.do_GET

    def get(handler):
        if handler.path != "/unstable":
            return old_handler(handler)
        handler.respond("""<form><label>First Name<input required></label>
          <label>Email<input type=email required></label><button>Submit application</button></form>
          <script>setInterval(()=>{for(const input of document.querySelectorAll('input')){const copy=input.cloneNode();delete copy.dataset.wagecuckId;copy.value='';input.replaceWith(copy)}},80)</script>""")

    monkeypatch.setattr(Handler, "do_GET", get)
    result = await ApplicationRunner().run(
        f"{base}/unstable", profile, options.model_copy(update={"mode": "fill", "max_steps": 4})
    )
    assert result.code in (Code.NO_PROGRESS, Code.STEP_LIMIT), result.model_dump()
    assert result.steps <= 4 and not server.submissions
