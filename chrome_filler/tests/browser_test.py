"""Real unpacked-extension UI regression tests; no API key or paid network request.

Run from the repository root: filler/.venv/Scripts/python.exe chrome_filler/tests/browser_test.py
Requires the repository's Playwright dependency and its bundled Chromium browser.
Artifacts, screenshots, and a machine-readable report go to chrome_filler/.artifacts.
"""
from __future__ import annotations

import functools
import json
import re
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "chrome_filler"
FIXTURES = EXTENSION / "tests" / "fixtures"
ARTIFACTS = EXTENSION / ".artifacts"
ANSWER = (
    "I want to join Northstar Robotics because its focus on safer, accessible warehouses "
    "connects with my experience building dependable tools for operations teams. I would "
    "bring Python and JavaScript experience, thoughtful collaboration, and a commitment "
    "to testing to help deliver robotics software that works for the people using it."
)
KEY = "sk-test-synthetic-never-a-real-key"
WAITING = re.compile("Waiting for LLM|Writing your answer|Thinking about your answer|Reading page context", re.I)
SETTINGS = {
    "enabled": True,
    "apiKey": KEY,
    "model": "gpt-5-mini",
    "profile": "Jordan Example, a software engineer in Vancouver. Email jordan@example.test.",
    "writingInstructions": "Write professionally and warmly. Mention accessibility. Never invent credentials.",
    "resumeText": (FIXTURES / "resume.txt").read_text(encoding="utf-8"),
    "resumeFile": None,
    "debug": True,
}


class QuietHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            super().do_GET()

    def log_message(self, *_args):
        pass


@contextmanager
def fixture_server():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(FIXTURES))
    )
    second = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(FIXTURES))
    )
    threads = [threading.Thread(target=item.serve_forever, daemon=True) for item in (server, second)]
    for thread in threads:
        thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/application.html?crossPort={second.server_port}"
    finally:
        for item in (server, second):
            item.shutdown()
            item.server_close()
        for thread in threads:
            thread.join(timeout=2)


MOCK_FETCH = r"""({answer, mode, delay, email}) => {
    self.__qaRequests = [];
    self.__qaMode = mode;
    self.__qaDelay = delay;
    self.__qaAnswer = answer;
    self.__qaEmail = email;
    self.__qaAborts = 0;
    if (!self.__qaOriginalFetch) self.__qaOriginalFetch = self.fetch.bind(self);
    self.fetch = async (url, options = {}) => {
      if (!String(url).startsWith('https://api.openai.com/v1/responses')) {
        throw new Error('QA blocked unexpected external fetch: ' + String(url));
      }
      const payload = JSON.parse(options.body);
      self.__qaRequests.push({url: String(url), payload});
      const signal = options.signal;
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(resolve, self.__qaDelay);
        const abort = () => { self.__qaAborts++; clearTimeout(timeout); reject(new DOMException('Cancelled', 'AbortError')); };
        if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, {once: true});
      });
      if (self.__qaMode === 'error') {
        return new Response(JSON.stringify({error: {message: 'Synthetic rate limit. Try again.', type: 'rate_limit_error'}}),
          {status: 429, headers: {'Content-Type': 'application/json'}});
      }
      let output = self.__qaAnswer;
      const input = JSON.stringify(payload.input || '');
      if (/Email address/i.test(input) && !/Why do you want to work here/i.test(input)) output = self.__qaEmail;
      if (self.__qaMode === 'invalid') output = 'Not an email address';
      const structured = JSON.stringify({answer:output, missingInformation:''});
      const completed = {id:'resp_synthetic', object:'response', status:'completed', output:[
        {type:'message', role:'assistant', content:[{type:'output_text',text:structured,annotations:[]}]}]};
      if (payload.stream) {
        const events = [
          {type:'response.created',response:{id:'resp_synthetic',status:'in_progress'}},
          {type:'response.output_text.delta',delta:structured},
          {type:'response.output_text.done',text:structured},
          {type:'response.completed',response:completed},
        ];
        const body = events.map(event => 'event: ' + event.type + '\ndata: ' + JSON.stringify(event) + '\n\n').join('');
        return new Response(body, {status:200,headers:{'Content-Type':'text/event-stream'}});
      }
      return new Response(JSON.stringify(completed), {status:200,headers:{'Content-Type':'application/json'}});
    };
}"""


class BrowserSuite:
    def __init__(self, context, worker, url):
        self.context = context
        self.worker = worker
        self.url = url
        self.page = context.new_page()
        self.page.set_default_timeout(7000)
        self.extension_id = worker.url.split("/")[2]
        self.failures = []
        self.results = []
        self.console_errors = []
        context.on("console", self.capture_console)

    def capture_console(self, message):
        if message.type == "error":
            self.console_errors.append(message.text)

    def seed(self, **changes):
        settings = dict(SETTINGS, **changes)
        self.worker.evaluate("() => chrome.storage.session.remove('wcPopupDraft')")
        self.worker.evaluate("settings => chrome.storage.local.set({wcSettings: settings})", settings)

    def mock(self, mode="success", delay=600, answer=ANSWER):
        self.worker.evaluate(MOCK_FETCH, {
            "answer": answer, "mode": mode, "delay": delay, "email": "jordan@example.test"
        })

    def fresh(self, **settings):
        self.seed(**settings)
        self.mock()
        self.page.goto(self.url)
        expect(self.page.locator("#wc-ai-root")).to_have_count(1)
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(self.button("Why do you want to work here")).to_be_visible()

    def fresh_labels(self):
        self.seed()
        self.mock()
        self.page.goto(self.url.replace("application.html", "labels.html"))
        expect(self.page.locator("#wc-ai-root")).to_have_count(1)
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(self.button("Legal first name")).to_be_visible()

    @staticmethod
    def expand(popup, details_id):
        details = popup.locator(f"#{details_id}")
        if details.count() and details.get_attribute("open") is None:
            details.locator("summary").first.click()

    @staticmethod
    def upload(popup, input_id, files):
        button = popup.locator(f"#{input_id}-button")
        if button.count():
            with popup.expect_file_chooser() as chooser:
                button.click()
            chooser.value.set_files(files)
        else:
            popup.locator(f"#{input_id}").set_input_files(files)

    def stored_settings(self):
        return self.worker.evaluate("async () => (await chrome.storage.local.get('wcSettings')).wcSettings")

    def wait_stored(self, key, expected, timeout=5):
        deadline = time.monotonic() + timeout
        actual = None
        while time.monotonic() < deadline:
            actual = self.stored_settings().get(key)
            if actual == expected:
                return
            self.page.wait_for_timeout(100)
        raise AssertionError(f"Saved {key} mismatch: expected {expected!r}, got {actual!r}")

    def button(self, label, frame=None):
        scope = frame or self.page
        return scope.get_by_role("button", name=re.compile(r"Fill with AI: .*" + re.escape(label), re.I), include_hidden=True)

    def toast(self):
        return self.page.locator("#wc-ai-root .wc-toast").last

    def screenshot(self, name, page=None):
        (page or self.page).screenshot(path=str(ARTIFACTS / f"{name}.png"), full_page=True)

    def run(self, name, method):
        started = time.monotonic()
        try:
            method()
            self.results.append({"name": name, "passed": True, "seconds": round(time.monotonic() - started, 2)})
            print(f"PASS {name}", flush=True)
        except Exception as error:
            self.failures.append(name)
            self.results.append({"name": name, "passed": False, "error": str(error), "traceback":traceback.format_exc(), "seconds": round(time.monotonic() - started, 2)})
            print(f"FAIL {name}: {error}", flush=True)
            try:
                diagnostics = self.page.evaluate("""() => {
                  const field = document.getElementById('shadow-question')?.shadowRoot?.querySelector('textarea');
                  return {buttons:[...(document.getElementById('wc-ai-root')?.shadowRoot?.querySelectorAll('button') || [])].map(node => ({label:node.getAttribute('aria-label'), text:node.textContent, style:node.getAttribute('style')})),
                    shadowField:field ? {labels:[...(field.labels||[])].map(node=>node.textContent),rect:field.getBoundingClientRect().toJSON(),css:{display:getComputedStyle(field).display,visibility:getComputedStyle(field).visibility,opacity:getComputedStyle(field).opacity},disabled:field.disabled,readOnly:field.readOnly,matchesDisabled:field.matches(':disabled')} : null,
                    frames:[...document.querySelectorAll('iframe')].map(node=>node.src)};
                }""")
                (ARTIFACTS / f"failure-{name}.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
                self.screenshot(f"failure-{name}")
            except Exception:
                pass

    def discovery(self):
        self.fresh()
        assert self.page.locator("#wc-ai-root .wc-field-outline").count() == self.page.locator("#wc-ai-root .wc-fill-button").count()
        for label in ("Full name", "Email address", "Brief professional biography", "Years of experience", "How do you collaborate with operators"):
            expect(self.button(label)).to_have_count(1)
        for label in ("Account password", "Search openings", "Disabled field", "Read-only field", "Resume file", "Consent checkbox", "One-time verification code", "Credit card number", "Hidden parent field", "Disabled fieldset input", "Zero-length input"):
            expect(self.button(label)).to_have_count(0)
        count = self.page.locator(".wc-fill-button").count()
        self.page.evaluate("document.body.appendChild(document.createElement('div'))")
        self.page.wait_for_timeout(400)
        assert self.page.locator(".wc-fill-button").count() == count, "Rescan created duplicate controls"
        self.page.locator("#add-field").click()
        expect(self.button("What project are you proud of")).to_have_count(1)
        assert self.page.locator(".wc-fill-button").count() == count + 1
        self.page.locator("#dynamic").evaluate("node => node.remove()")
        expect(self.button("What project are you proud of")).to_have_count(0)
        self.screenshot("discovered-fields")

    def root_scroll_container_buttons(self):
        self.fresh()
        self.page.evaluate("""() => {
          document.body.style.height = '900px';
          document.body.style.overflow = 'auto';
          document.querySelector('main').style.paddingTop = '1600px';
        }""")
        self.page.locator("#name").scroll_into_view_if_needed()
        assert self.page.evaluate("window.scrollY > 0")
        expect(self.button("Full name")).to_be_visible()
        expect(self.page.locator("#wc-ai-root .wc-field-outline").first).to_be_visible()

    def manual_scan_gate(self):
        self.seed()
        self.mock()
        self.page.goto(self.url)
        scan = self.page.locator("#wc-ai-root .wc-scan-button")
        expect(scan).to_be_visible()
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
        expect(self.page.locator("#wc-ai-root .wc-field-outline")).to_have_count(0)
        scan.evaluate("node => node.click()")
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
        popup = self.context.new_page()
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#rescan-button")).to_have_text("Scan page")
            expect(popup.locator("#page-status")).to_contain_text("Scan page to highlight")
            popup.locator("#rescan-button").click()
            expect(self.button("Why do you want to work here")).to_be_visible()
            expect(popup.locator("#page-status")).to_contain_text("ready to write")
            count = self.page.locator("#wc-ai-root .wc-fill-button").count()
            expect(self.page.locator("#wc-ai-root .wc-field-outline")).to_have_count(count)
            color = self.page.locator("#wc-ai-root .wc-field-outline").first.evaluate("node => getComputedStyle(node).borderTopColor")
            assert color == "rgb(139, 92, 246)", color
            self.page.reload()
            expect(self.page.locator("#wc-ai-root .wc-scan-button")).to_be_visible()
            expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
            expect(self.page.locator("#wc-ai-root .wc-field-outline")).to_have_count(0)
            self.page.locator("#wc-ai-root .wc-scan-button").click()
            expect(self.button("Why do you want to work here")).to_be_visible()
            self.page.evaluate("history.pushState({}, '', '?newApplication=1'); document.body.appendChild(document.createElement('div'))")
            expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
            expect(self.page.locator("#wc-ai-root .wc-field-outline")).to_have_count(0)
        finally:
            popup.close()

    def synthetic_click_does_not_generate(self):
        self.fresh()
        self.button("Why do you want to work here").evaluate("node => node.click()")
        self.page.wait_for_timeout(1000)
        assert self.worker.evaluate("self.__qaRequests") == [], "Page-generated click started an AI request"
        expect(self.page.locator("#motivation")).to_have_value("")
        assert self.page.evaluate("window.fixtureSubmitted") is False

    def label_combinations(self):
        self.fresh_labels()
        labels = {
            "explicit":"Legal first name", "nested":"Preferred name", "multi-associated":"Your professional background",
            "aria-multi":"Why does our mission interest you", "aria-label":"Available start date",
            "sibling-p":"Which project are you proudest of", "sibling-span":"How do you collaborate with designers",
            "sibling-div":"What motivates your engineering work", "legend-only":"work authorization",
            "group-heading":"What would make this role meaningful", "table-question":"accessibility experience",
            "table-portfolio":"Portfolio website", "table-column":"How do you write reliable code",
            "definition-question":"How do you test software", "title-only":"Professional website",
            "placeholder-only":"preferred location", "name-fallback":"candidate first name",
            "motivation_statement":"motivation statement", "local-1":"dependable system you built",
            "local-2":"salary range", "search-story":"job search journey",
            "search-strategy":"job search strategy",
            "shadow-explicit":"mentoring approach", "shadow-aria":"Why do you enjoy small teams",
            "changing-question":"What do you value in teammates",
            "heading-with-hint":"How do you handle difficult feedback", "floating-after":"Preferred interview time",
            "short-answer":"Answer", "wrapper-editor":"Describe your communication style",
            "case-sensitive-aria":"What do you admire about our mission",
        }
        for field_id, label in labels.items():
            expect(self.button(label)).to_have_count(1)
        expected_count = len(labels)
        assert self.page.locator(".wc-fill-button").count() == expected_count
        for label in ("Search openings", "Financial contact", "Account details", "Account confirmation", "Confirmation input", "Connection details", "Disabled label combination", "Read only label combination"):
            expect(self.button(label)).to_have_count(0)
        assert self.page.locator("#wc-ai-root .wc-fill-button").evaluate_all("nodes => nodes.every(node => !node.getAttribute('aria-label').includes('GLOBAL_HERO_SENTINEL'))")
        assert "NESTED_FILLED_VALUE" not in self.button("Preferred name").get_attribute("aria-label")
        expect(self.button("What do you admire about our mission")).to_have_attribute("aria-label", re.compile("Include a concrete example"))
        assert self.button("What do you admire about our mission").get_attribute("aria-label").count("What do you admire") == 1
        self.page.locator("#changing-label").evaluate("node => node.textContent = 'What do you value in managers?'")
        expect(self.button("What do you value in managers")).to_have_count(1)
        expect(self.button("What do you value in teammates")).to_have_count(0)
        assert self.page.locator(".wc-fill-button").count() == expected_count
        self.page.wait_for_timeout(300)
        self.page.locator("#late-shadow-host").evaluate("""node => {
          const root = node.attachShadow({mode:'open'});
          root.innerHTML = '<label for="late-shadow-answer">What do you enjoy about accessible tools?</label><textarea id="late-shadow-answer" style="display:block;width:100%;height:90px"></textarea>';
        }""")
        expect(self.button("What do you enjoy about accessible tools")).to_have_count(1)
        assert self.page.locator(".wc-fill-button").count() == expected_count + 1
        self.screenshot("diverse-labels")

    def bounded_field_context(self):
        self.fresh_labels()
        scenarios = (
            ("aria-multi", "Why does our mission interest you", ("Describe your motivation", "ARIA_HELP_SENTINEL", "INLINE_DESCRIPTION_SENTINEL")),
            ("sibling-div", "What motivates your engineering work", ()),
            ("table-question", "accessibility experience", ()),
            ("definition-question", "How do you test software", ()),
            ("shadow-aria", "Why do you enjoy small teams", ("Share an example",)),
            ("local-1", "dependable system you built", ()),
            ("heading-with-hint", "How do you handle difficult feedback", ("Keep this under 100 words",)),
            ("short-answer", "Answer", ("How do you mentor junior teammates",)),
            ("wrapper-editor", "Describe your communication style", ()),
        )
        for field_id, label, contextual in scenarios:
            self.mock(answer="A grounded synthetic answer based on my saved profile.")
            field = self.page.locator(f"#{field_id}")
            field.evaluate("node => node.scrollIntoView({block:'center'})")
            self.button(label).click()
            if field.evaluate("node => node.isContentEditable"):
                expect(field).to_have_js_property("innerText", "A grounded synthetic answer based on my saved profile.")
            else:
                expect(field).to_have_value("A grounded synthetic answer based on my saved profile.")
            payload = self.worker.evaluate("self.__qaRequests")[0]["payload"]
            context_item = next(item for item in payload["input"][0]["content"] if item["type"] == "input_text")
            context = json.loads(context_item["text"])
            target = context["targetField"]
            assert label.lower() in target["label"].lower(), (field_id, target["label"])
            local_text = target["label"] + " " + target["context"]
            for expected in contextual:
                assert expected in local_text, (field_id, expected, local_text)
            assert "NEIGHBOR_FILLED_VALUE_MUST_NOT_LEAK" not in local_text
            if field_id == "local-1":
                assert "salary range" not in local_text.lower(), "Adjacent field question leaked into local context"
            assert "GLOBAL_HERO_SENTINEL" not in local_text
            assert "ENTIRE_LABELS_PAGE_SENTINEL" in context["entirePage"]["text"]
            assert "GLOBAL_HERO_SENTINEL" in context["entirePage"]["text"]
            assert self.page.evaluate("window.fixtureSubmitted") is False
            dismiss = self.page.get_by_role("button", name="Dismiss", exact=True)
            for _ in range(dismiss.count()):
                dismiss.first.click()

    def placement_and_scroll(self):
        self.fresh()
        self.page.locator("#motivation").scroll_into_view_if_needed()
        self.page.wait_for_timeout(200)
        field = self.page.locator("#motivation").bounding_box()
        button = self.button("Why do you want to work here").bounding_box()
        assert field and button
        assert abs((field["x"] + field["width"]) - (button["x"] + button["width"])) < 26
        assert abs(field["y"] - button["y"]) < 26
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        self.page.wait_for_timeout(250)
        assert not self.button("Why do you want to work here").is_visible() or self.button("Why do you want to work here").bounding_box()["y"] < 0
        self.page.locator("#motivation").scroll_into_view_if_needed()
        expect(self.button("Why do you want to work here")).to_be_visible()

    def paragraph_and_context(self):
        self.fresh()
        self.button("Why do you want to work here").click()
        expect(self.toast()).to_contain_text(WAITING)
        expect(self.button("Why do you want to work here")).to_have_attribute("aria-busy", "true")
        self.screenshot("waiting-for-llm")
        expect(self.page.locator("#motivation")).to_have_value(ANSWER)
        expect(self.toast()).to_contain_text(re.compile("filled|ready|review", re.I))
        self.screenshot("filled-paragraph")
        events = self.page.evaluate("window.fixtureEvents")
        assert {event["type"] for event in events if event["id"] == "motivation"} >= {"input", "change"}
        assert self.page.evaluate("window.fixtureSubmitted") is False
        requests = self.worker.evaluate("self.__qaRequests")
        assert len(requests) == 1
        payload_text = json.dumps(requests[0]["payload"])
        for context in ("WHOLE_PAGE_SENTINEL", "SHADOW_CONTEXT_SENTINEL", "Northstar Robotics", "Jordan Example", "accessibility", "Never invent"):
            assert context in payload_text, f"Missing request context: {context}"
        assert "never-send-this-password" not in payload_text
        assert "never-send-this-hidden-token" not in payload_text
        assert "never-send-otp" not in payload_text
        assert "never-send-card" not in payload_text
        assert "never-send-hidden-parent" not in payload_text
        assert "NEVER_SEND_HIDDEN_PAGE_TEXT" not in payload_text
        assert requests[0]["payload"].get("store") is False
        self.page.get_by_role("button", name="Undo", exact=True).click()
        expect(self.page.locator("#motivation")).to_have_value("")

    def email_and_contenteditable(self):
        self.fresh()
        self.mock(answer="jordan@example.test")
        self.button("Email address").click()
        expect(self.page.locator("#email")).to_have_value("jordan@example.test")
        self.page.get_by_role("button", name="Dismiss", exact=True).click()
        biography = "I build accessible, dependable software for operations teams.\n\nI collaborate with designers and operators."
        self.mock(answer=biography)
        self.page.locator("#bio").evaluate("node => node.scrollIntoView({block:'center'})")
        self.button("Brief professional biography").click()
        expect(self.page.locator("#bio")).to_have_js_property("innerText", biography)
        self.fresh()
        self.page.evaluate("""() => {
          window.overriddenSetterCalls = 0;
          const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
          Object.defineProperty(document.getElementById('name'), 'value', {
            configurable:true, get() { return descriptor.get.call(this); },
            set(_value) { window.overriddenSetterCalls++; }
          });
        }""")
        self.mock(answer="Jordan Example")
        self.button("Full name").click()
        expect(self.page.locator("#name")).to_have_value("Jordan Example")
        assert self.page.evaluate("window.overriddenSetterCalls") == 0
        assert {item["type"] for item in self.page.evaluate("window.fixtureEvents") if item["id"] == "name"} >= {"input", "change"}

    def cancellation(self):
        self.fresh()
        self.mock(delay=5000)
        self.button("Why do you want to work here").click()
        expect(self.toast()).to_contain_text(WAITING)
        self.page.get_by_role("button", name="Cancel", exact=True).click()
        expect(self.toast()).to_contain_text(re.compile("cancel", re.I))
        expect(self.page.locator("#motivation")).to_have_value("")
        expect(self.button("Why do you want to work here")).to_be_enabled()
        self.page.wait_for_timeout(800)
        expect(self.page.locator("#motivation")).to_have_value("")
        self.screenshot("cancelled-request")

    def edit_conflicts(self):
        self.fresh()
        self.mock(delay=1200)
        self.button("Why do you want to work here").click()
        expect(self.toast()).to_contain_text(WAITING)
        self.page.locator("#motivation").fill("My own answer while waiting.")
        expect(self.toast()).to_contain_text(re.compile("changed|edited|overwrite", re.I), timeout=7000)
        expect(self.page.locator("#motivation")).to_have_value("My own answer while waiting.")
        self.fresh()
        self.button("Why do you want to work here").click()
        expect(self.page.locator("#motivation")).to_have_value(ANSWER)
        self.page.locator("#motivation").fill("A deliberate edit after the generated answer.")
        undo = self.page.get_by_role("button", name="Undo", exact=True)
        if undo.count():
            undo.click()
        expect(self.page.locator("#motivation")).to_have_value("A deliberate edit after the generated answer.")
        self.fresh()
        self.mock(delay=1200)
        self.button("Why do you want to work here").click()
        expect(self.toast()).to_contain_text(WAITING)
        self.page.locator("label[for=motivation]").evaluate("node => node.textContent = 'What salary do you expect?'")
        expect(self.toast()).to_contain_text("The question changed")
        expect(self.page.locator("#motivation")).to_have_value("")

    def error_and_retry(self):
        self.fresh()
        self.mock(mode="error")
        self.button("Why do you want to work here").click()
        expect(self.toast()).to_contain_text(re.compile("rate limit|error|failed|try again", re.I))
        expect(self.page.locator("#motivation")).to_have_value("")
        expect(self.button("Why do you want to work here")).to_be_enabled()
        self.screenshot("api-error")
        self.mock()
        retry = self.page.get_by_role("button", name="Retry", exact=True)
        if retry.count():
            retry.click()
        else:
            self.button("Why do you want to work here").click()
        expect(self.page.locator("#motivation")).to_have_value(ANSWER)

    def framed_fields(self):
        self.fresh()
        for frame in self.page.frames[1:]:
            frame.frame_element().evaluate("node => node.scrollIntoView({block:'center'})")
            expect(frame.locator("#wc-ai-root")).to_have_count(1)
            expect(self.button("How would you improve robot accessibility", frame)).to_have_count(1)
            self.mock(answer="I would work with operators to test accessible robotics interfaces.")
            self.button("How would you improve robot accessibility", frame).click()
            expect(frame.locator("#frame-answer")).to_have_value("I would work with operators to test accessible robotics interfaces.")
            payload = json.dumps(self.worker.evaluate("self.__qaRequests")[0]["payload"])
            assert "WHOLE_PAGE_SENTINEL" in payload, "Frame request omitted parent page context"
            assert "FRAME_CONTEXT_SENTINEL" in payload, "Frame request omitted its own context"

    def shadow_and_numeric_validation(self):
        self.fresh()
        self.mock(answer="I collaborate with operators by testing accessible prototypes together.")
        self.page.locator("#shadow-answer").evaluate("node => node.scrollIntoView({block:'center'})")
        self.button("How do you collaborate with operators").click()
        expect(self.page.locator("#shadow-answer")).to_have_value("I collaborate with operators by testing accessible prototypes together.")
        payload = json.dumps(self.worker.evaluate("self.__qaRequests")[0]["payload"])
        assert "SHADOW_CONTEXT_SENTINEL" in payload, "Wrapped shadow text missing from request"
        assert "DIRECT_SHADOW_TEXT_SENTINEL" in payload, "Direct shadow text missing from request"
        self.fresh()
        self.mock(answer="25")
        self.page.locator("#experience").evaluate("node => node.scrollIntoView({block:'center'})")
        self.button("Years of experience").click()
        expect(self.toast()).to_contain_text(re.compile("format|insert|invalid|fit", re.I))
        expect(self.page.locator("#experience")).to_have_value("5")
        self.fresh()
        self.mock(mode="invalid")
        self.button("Email address").click()
        expect(self.toast()).to_contain_text(re.compile("valid email|email address", re.I))
        expect(self.page.locator("#email")).to_have_value("")

    def disable_during_generation(self):
        self.fresh()
        self.mock(delay=5000)
        self.page.locator("#motivation").fill("My original draft.")
        self.button("Why do you want to work here").click()
        expect(self.toast()).to_contain_text(WAITING)
        self.page.wait_for_timeout(600)
        self.seed(enabled=False)
        expect(self.page.locator(".wc-fill-button")).to_have_count(0)
        expect(self.page.locator("#motivation")).to_have_value("My original draft.")
        self.page.wait_for_timeout(1000)
        expect(self.page.locator("#motivation")).to_have_value("My original draft.")
        assert self.worker.evaluate("self.__qaAborts") >= 1, "Disabling did not abort the active fetch"

    def disabled_and_missing_key(self):
        self.fresh()
        self.seed(enabled=False)
        expect(self.page.locator(".wc-fill-button")).to_have_count(0)
        self.seed(enabled=True, apiKey="")
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(self.button("Why do you want to work here")).to_be_visible()
        self.button("Why do you want to work here").click()
        expect(self.toast()).to_contain_text(re.compile("key|settings|extension", re.I))
        assert not self.worker.evaluate("self.__qaRequests"), "Missing key issued a network request"

    def popup_settings(self):
        self.fresh()
        popup = self.context.new_page()
        popup.set_viewport_size({"width":400,"height":600})
        popup.set_default_timeout(7000)
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#profile")).to_have_value(SETTINGS["profile"])
            popup.locator("#profile").fill("Jordan Example. I enjoy accessible robotics.")
            popup.locator("#tab-writing").click()
            popup.locator("#writing-instructions").fill("Use a warm professional voice. Mention teamwork.")
            popup.locator("#tab-profile").click()
            self.upload(popup, "resume-upload", str(FIXTURES / "resume.txt"))
            expect(popup.locator("#resume-text")).to_have_value(re.compile("Jordan Example"))
            popup.locator("#tab-connection").click()
            expect(popup.locator("#api-key")).to_have_attribute("type", "password")
            popup.locator("#reveal-key").click()
            expect(popup.locator("#api-key")).to_have_attribute("type", "text")
            expect(popup.locator("#api-key")).to_have_value(KEY)
            popup.locator("#reveal-key").click()
            self.expand(popup, "advanced-settings")
            popup.locator("#model").fill("gpt-5-mini")
            popup.locator("#save-button").click()
            expect(popup.locator("#save-status")).to_contain_text(re.compile("saved", re.I))
            settings = self.worker.evaluate("async () => (await chrome.storage.local.get('wcSettings')).wcSettings")
            assert settings["profile"] == "Jordan Example. I enjoy accessible robotics."
            assert settings["writingInstructions"] == "Use a warm professional voice. Mention teamwork."
            assert "Jordan Example" in settings["resumeText"]
            self.screenshot("popup-connection", popup)
            popup.reload()
            popup.locator("#tab-writing").click()
            expect(popup.locator("#writing-instructions")).to_have_value(settings["writingInstructions"])
            self.screenshot("popup-writing", popup)
            popup.locator("#tab-profile").click()
            expect(popup.locator("#profile")).to_have_value(settings["profile"])
            self.screenshot("popup-profile", popup)
            popup.locator("#tab-connection").click()
            self.expand(popup, "advanced-settings")
            self.expand(popup, "troubleshooting")
            popup.locator("#log-refresh").click()
            assert KEY not in popup.locator("#log-output").inner_text(), "Log UI exposed API key"
            popup.locator("#log-clear").click()
        finally:
            popup.close()

    def popup_validation_and_resume(self):
        self.fresh()
        popup = self.context.new_page()
        popup.set_viewport_size({"width":400,"height":600})
        popup.set_default_timeout(7000)
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            popup.locator("#tab-connection").click()
            self.expand(popup, "advanced-settings")
            popup.locator("#model").fill("")
            popup.locator("#save-button").click()
            expect(popup.locator("#save-status")).to_contain_text(re.compile("model|required|enter", re.I))
            popup.locator("#model").fill("gpt-5-mini")
            popup.locator("#tab-profile").click()
            self.upload(popup, "resume-upload", {"name":"bad.exe", "mimeType":"application/octet-stream", "buffer":b"not a resume"})
            expect(popup.locator("#resume-status")).to_contain_text(re.compile("support|format|text|PDF|TXT", re.I))
            popup.locator("#resume-remove").click()
            expect(popup.locator("#resume-text")).to_have_value("")
            popup.locator("#enable-toggle").uncheck()
            popup.locator("#save-button").click()
            expect(popup.locator("#save-status")).to_contain_text(re.compile("saved", re.I))
            expect(self.page.locator(".wc-fill-button")).to_have_count(0)
        finally:
            popup.close()

    def popup_import_pdf_and_keyboard(self):
        self.fresh()
        popup = self.context.new_page()
        popup.set_viewport_size({"width":400,"height":600})
        popup.set_default_timeout(7000)
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#profile")).to_have_value(SETTINGS["profile"])
            popup.locator("#tab-profile").focus()
            popup.keyboard.press("ArrowRight")
            expect(popup.locator("#tab-writing")).to_be_focused()
            expect(popup.locator("#panel-writing")).to_be_visible()
            popup.locator("#tone-casual").click()
            expect(popup.locator("#writing-instructions")).to_have_value(re.compile("friendly|conversational|casual", re.I))
            popup.locator("#tone-professional").click()
            expect(popup.locator("#writing-instructions")).to_have_value(re.compile("professional", re.I))
            popup.locator("#tab-writing").focus()
            popup.keyboard.press("End")
            expect(popup.locator("#tab-connection")).to_be_focused()
            popup.keyboard.press("Home")
            expect(popup.locator("#tab-profile")).to_be_focused()
            imported = {"facts":{"name":"Jordan Example", "email":"jordan@example.test"},
                "employment":[{"company":"Synthetic Operations", "role":"Software engineer", "apiKey":"TOP_SECRET_KEY"}],
                "password":"TOP_SECRET_PASSWORD", "resume":"C:\\private\\resume.pdf", "cover_letter":"/home/private/letter.txt",
                "nested":{"token":"TOP_SECRET_TOKEN", "privateKey":"TOP_SECRET_PRIVATE", "note":"sk-notarealkey1234567890"},
                "dataPath":"/Users/private/data.json"}
            self.upload(popup, "profile-import", {"name":"profile.json","mimeType":"application/json","buffer":json.dumps(imported).encode()})
            expect(popup.locator("#profile-import-status")).to_contain_text(re.compile("imported", re.I))
            clean = popup.locator("#profile").input_value()
            for safe in ("Jordan Example", "jordan@example.test", "Synthetic Operations", "Software engineer"):
                assert safe in clean
            for forbidden in ("TOP_SECRET", "C:\\private", "/home/private", "/Users/private", "sk-notareal"):
                assert forbidden not in clean, f"Profile import exposed {forbidden}"
            self.upload(popup, "resume-upload", str(FIXTURES / "resume.pdf"))
            expect(popup.locator("#resume-status")).to_contain_text(re.compile("PDF attached", re.I))
            expect(popup.locator("#resume-file-name")).to_contain_text("resume.pdf")
            popup.locator("#save-button").click()
            expect(popup.locator("#save-status")).to_contain_text(re.compile("saved", re.I))
            settings = self.worker.evaluate("async () => (await chrome.storage.local.get('wcSettings')).wcSettings")
            assert settings["resumeFile"]["dataUrl"].startswith("data:application/pdf;base64,JVBERi0")
            self.screenshot("popup-pdf-profile", popup)
            popup.close()
            self.page.bring_to_front()
            self.mock()
            self.button("Why do you want to work here").click()
            expect(self.page.locator("#motivation")).to_have_value(ANSWER)
            payload = self.worker.evaluate("self.__qaRequests")[0]["payload"]
            file = next(item for item in payload["input"][0]["content"] if item["type"] == "input_file")
            assert file["filename"] == "resume.pdf"
            assert file["file_data"].startswith("data:application/pdf;base64,JVBERi0")
        finally:
            if not popup.is_closed():
                popup.close()

    def popup_guided_setup_and_autosave(self):
        self.fresh(apiKey="", profile="", resumeText="", resumeFile=None)
        popup = self.context.new_page()
        popup.set_viewport_size({"width":400,"height":600})
        popup.set_default_timeout(7000)
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#tab-connection")).to_have_attribute("aria-selected", "true")
            expect(popup.locator("#setup-banner")).to_be_visible()
            expect(popup.locator("#setup-banner")).to_contain_text(re.compile("key|connect|OpenAI", re.I))
            self.screenshot("popup-guided-setup", popup)
            popup.locator("#api-key").fill(KEY)
            self.wait_stored("apiKey", KEY)
            popup.locator("#setup-action").click()
            expect(popup.locator("#panel-profile")).to_be_visible()
            expect(popup.locator("#setup-banner")).to_contain_text(re.compile("profile|about you|experience", re.I))
            profile = "Jordan Example. I build accessible robotics tools with operations teams."
            popup.locator("#profile").fill(profile)
            self.wait_stored("profile", profile)
            popup.close()
            popup = self.context.new_page()
            popup.set_viewport_size({"width":400,"height":600})
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#profile")).to_have_value(profile)
            expect(popup.locator("#tab-profile")).to_have_attribute("aria-selected", "true")
            self.screenshot("popup-setup-complete", popup)
        finally:
            if not popup.is_closed():
                popup.close()

    def popup_draft_pause_and_tone_preferences(self):
        self.fresh()
        popup = self.context.new_page()
        popup.set_viewport_size({"width":400,"height":600})
        popup.set_default_timeout(7000)
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#profile")).to_have_value(SETTINGS["profile"])
            draft = "Jordan Example. This change must survive closing the popup immediately."
            popup.locator("#profile").fill(draft)
            popup.wait_for_timeout(120)
            popup.close()
            popup = self.context.new_page()
            popup.set_viewport_size({"width":400,"height":600})
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#profile")).to_have_value(draft)
            self.wait_stored("profile", draft)
            popup.locator("#tab-connection").click()
            self.expand(popup, "advanced-settings")
            popup.locator("#model").fill("")
            popup.locator("#tab-profile").click()
            second = "Jordan Example. Preserve this draft while paused and the model is invalid."
            popup.locator("#profile").fill(second)
            popup.locator("#enable-toggle").uncheck()
            self.wait_stored("enabled", False)
            expect(self.page.locator(".wc-fill-button")).to_have_count(0)
            expect(popup.locator("#profile")).to_have_value(second)
            assert self.stored_settings()["model"] == "gpt-5-mini", "Pause saved an invalid model"
            popup.wait_for_timeout(150)
            popup.close()
            popup = self.context.new_page()
            popup.set_viewport_size({"width":400,"height":600})
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#profile")).to_have_value(second)
            expect(popup.locator("#enable-toggle")).not_to_be_checked()
            popup.locator("#tab-connection").click()
            self.expand(popup, "advanced-settings")
            expect(popup.locator("#model")).to_have_value("")
            popup.locator("#model").fill("gpt-5-mini")
            self.wait_stored("profile", second)
            popup.locator("#tab-writing").click()
            preferences = "Mention my accessibility work. Avoid sales language."
            popup.locator("#writing-instructions").fill(preferences)
            popup.locator("#tone-casual").click()
            casual = popup.locator("#writing-instructions").input_value()
            assert "Mention my accessibility work." in casual
            assert "Avoid sales language." in casual
            assert re.search("friendly|conversational|casual", casual, re.I)
            popup.locator("#tone-professional").click()
            professional = popup.locator("#writing-instructions").input_value()
            assert "Mention my accessibility work." in professional
            assert "Avoid sales language." in professional
            assert re.search("professional", professional, re.I)
            self.wait_stored("writingInstructions", professional)
            self.screenshot("popup-pause-and-preferences", popup)
        finally:
            if not popup.is_closed():
                popup.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    expect.set_options(timeout=15000)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    profile = ARTIFACTS / ("profile-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    with fixture_server() as url, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=True, channel="chromium", viewport={"width":1280,"height":900},
            args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"]
        )
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker", timeout=15000)
            suite = BrowserSuite(context, worker, url)
            names = (
                "manual_scan_gate", "discovery", "root_scroll_container_buttons", "synthetic_click_does_not_generate", "placement_and_scroll", "paragraph_and_context", "email_and_contenteditable",
                "cancellation", "edit_conflicts", "error_and_retry", "framed_fields",
                "shadow_and_numeric_validation", "disable_during_generation",
                "disabled_and_missing_key", "popup_settings", "popup_validation_and_resume",
                "popup_import_pdf_and_keyboard",
                "label_combinations", "bounded_field_context",
                "popup_guided_setup_and_autosave", "popup_draft_pause_and_tone_preferences",
            )
            for name in (sys.argv[1:] or names):
                suite.run(name, getattr(suite, name))
            report = {
                "timestamp":datetime.now(timezone.utc).isoformat(),
                "browser":context.browser.version if context.browser else "Chromium",
                "passed":len(suite.results)-len(suite.failures), "failed":len(suite.failures),
                "results":suite.results, "console_errors":suite.console_errors,
            }
            report_name = "browser-report.json" if not sys.argv[1:] else "browser-report-" + "-".join(sys.argv[1:]) + ".json"
            report_path = ARTIFACTS / report_name
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"{report['passed']}/{len(suite.results)} passed. Report: {report_path}", flush=True)
            return 1 if suite.failures else 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
