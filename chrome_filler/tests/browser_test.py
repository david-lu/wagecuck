"""Real unpacked-extension UI regression tests; no API key or paid network request.

Run from the repository root: filler/.venv/Scripts/python.exe chrome_filler/tests/browser_test.py
Requires the repository's Playwright dependency and its bundled Chromium browser.
Artifacts, screenshots, and a machine-readable report go to chrome_filler/.artifacts.
"""
from __future__ import annotations

import base64
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
      const contextText = payload.input?.[0]?.content?.find(part => part.type === 'input_text')?.text || '{}';
      const context = JSON.parse(contextText);
      const target = context.targetField || {};
      const choice = (field, batch = false) => field.type === 'checkbox_group' ? (field.options || []).filter(option => /accessible interfaces|design systems|I utilize Terraform daily|I prefer not to answer/i.test(option.label)).map(option => option.value)
        : field.type === 'checkbox' ? /accessible interfaces|open to remote work|open to hybrid work|open to relocation/i.test(field.label || '')
        : field.type === 'select' ? (field.options || []).find(option => option.value === self.__qaAnswer)?.value || (field.options || []).find(option => option.value === 'frontend')?.value || field.options?.[0]?.value || ''
        : field.type === 'radio' ? (field.options || []).find(option => option.value === 'Prefer not to answer')?.value || field.options?.[0]?.value || ''
        : field.type === 'number' && batch ? 5 : field.type === 'email' ? self.__qaEmail : self.__qaAnswer;
      if (target.type) output = choice(target);
      if (self.__qaMode === 'invalid') output = 'Not an email address';
      const structured = JSON.stringify(Array.isArray(context.targetFields)
        ? Object.fromEntries(context.targetFields.map(field => [field.fieldId, {
            answer: choice(field, true), missingInformation: '',
          }]))
        : {answer:output, missingInformation:''});
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
        expect(self.page.get_by_role("button", name=re.compile("Fill with AI: .*Why do you want to work here", re.I), include_hidden=True)).to_have_count(1)
        expect(self.button("Why do you want to work here")).to_be_visible()

    def fresh_labels(self):
        self.seed()
        self.mock()
        self.page.goto(self.url.replace("application.html", "labels.html"))
        expect(self.page.locator("#wc-ai-root")).to_have_count(1)
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(self.page.get_by_role("button", name=re.compile("Fill with AI: .*Legal first name", re.I), include_hidden=True)).to_have_count(1)
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
        button = scope.get_by_role("button", name=re.compile(r"Fill with AI: .*" + re.escape(label), re.I), include_hidden=True)
        if button.count() == 1:
            button.evaluate("node => node.dispatchEvent(new PointerEvent('pointerenter'))")
        return button

    def resume_button(self, label):
        button = self.page.get_by_role("button", name=f"Attach resume: {label}", exact=True, include_hidden=True)
        if button.count() == 1:
            button.evaluate("node => node.dispatchEvent(new PointerEvent('pointerenter'))")
        return button

    def undo_button(self, label):
        button = self.page.get_by_role("button", name=f"Undo generated answer: {label}", exact=True, include_hidden=True)
        if button.count() == 1:
            button.evaluate("node => node.dispatchEvent(new PointerEvent('pointerenter'))")
        return button

    @staticmethod
    def saved_resume():
        payload = (FIXTURES / "resume.pdf").read_bytes()
        return {"name": "resume.pdf", "type": "application/pdf", "size": len(payload),
                "dataUrl": "data:application/pdf;base64," + base64.b64encode(payload).decode("ascii")}

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
        assert self.page.locator("#wc-ai-root .wc-field-outline").count() == 0
        assert self.page.locator("#name").evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"
        for label in ("Full name", "Email address", "Brief professional biography", "Years of experience", "Which kinds of work have you done?", "Which area best matches your background", "Preferred engineering area", "How do you collaborate with operators"):
            expect(self.button(label)).to_have_count(1)
        assert self.page.locator("#work-interests").evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"
        for label in ("Accessible interfaces", "Backend systems"):
            expect(self.button(label)).to_have_count(0)
        for label in ("Account password", "Search openings", "Disabled field", "Read-only field", "Consent checkbox", "Custom focus dropdown", "One-time verification code", "Credit card number", "Hidden parent field", "Disabled fieldset input", "Zero-length input"):
            expect(self.button(label)).to_have_count(0)
        expect(self.resume_button("Resume file")).to_have_count(1)
        expect(self.resume_button("Upload CV")).to_have_count(1)
        expect(self.resume_button("Resume/CV")).to_have_count(1)
        assert self.page.locator('#wc-ai-root .wc-fill-button[aria-label^="Attach resume:"]').count() == 4
        expect(self.resume_button("Portfolio attachment")).to_have_count(0)
        expect(self.resume_button("Cover Letter")).to_have_count(0)
        count = self.page.locator(".wc-fill-button").count()
        self.page.evaluate("document.body.appendChild(document.createElement('div'))")
        self.page.wait_for_timeout(400)
        assert self.page.locator(".wc-fill-button").count() == count, "Rescan created duplicate controls"
        self.page.locator("#add-field").click()
        expect(self.button("What project are you proud of")).to_have_count(1)
        assert self.page.locator(".wc-fill-button").count() == count + 1
        self.page.locator("#dynamic").evaluate("node => node.remove()")
        expect(self.page.get_by_role("button", name=re.compile("Fill with AI: .*What project are you proud of", re.I), include_hidden=True)).to_have_count(0)
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
        assert self.page.locator("#name").evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"

    def manual_scan_gate(self):
        self.seed()
        self.mock()
        self.page.goto(self.url)
        self.page.locator("#name").evaluate("node => { node.style.outline = '1px dashed red'; node.style.outlineOffset = '4px'; }")
        scan = self.page.locator("#wc-ai-root .wc-scan-button")
        expect(scan).to_be_visible()
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
        expect(self.page.locator("#wc-ai-root .wc-field-outline")).to_have_count(0)
        assert self.page.locator("#name").evaluate("node => getComputedStyle(node).outlineColor") == "rgb(255, 0, 0)"
        scan.evaluate("node => node.click()")
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
        popup = self.context.new_page()
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#rescan-button")).to_contain_text("SCAN PAGE")
            expect(popup.locator("#page-status")).to_contain_text("Scan page to highlight")
            popup.locator("#rescan-button").click()
            expect(self.button("Why do you want to work here")).to_be_visible()
            expect(popup.locator("#page-status")).to_contain_text("ready to write")
            count = self.page.locator("#wc-ai-root .wc-fill-button").count()
            assert count > 0
            expect(self.page.locator("#wc-ai-root .wc-field-outline")).to_have_count(0)
            color = self.page.locator("#name").evaluate("node => getComputedStyle(node).outlineColor")
            assert color == "rgb(139, 92, 246)", color
            assert self.page.locator("#name").evaluate("node => getComputedStyle(node).outlineOffset") == "2px"
            self.page.reload()
            expect(self.page.locator("#wc-ai-root .wc-scan-button")).to_be_visible()
            expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
            expect(self.page.locator("#wc-ai-root .wc-field-outline")).to_have_count(0)
            self.page.locator("#wc-ai-root .wc-scan-button").click()
            expect(self.button("Why do you want to work here")).to_be_visible()
            self.page.locator("#name").evaluate("node => { node.style.outlineColor = 'blue'; }")
            self.page.evaluate("history.pushState({}, '', '?newApplication=1'); document.body.appendChild(document.createElement('div'))")
            expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
            expect(self.page.locator("#wc-ai-root .wc-field-outline")).to_have_count(0)
            assert self.page.locator("#name").evaluate("node => getComputedStyle(node).outlineColor") == "rgb(0, 0, 255)"
        finally:
            popup.close()

    def direct_highlight_restores_page_styles(self):
        self.seed()
        self.page.goto(self.url)
        self.page.locator("#name").evaluate("node => { node.style.outline = '1px dashed red'; node.style.outlineOffset = '4px'; }")
        self.page.locator("#motivation").evaluate("node => { node.style.borderColor = 'green'; }")
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(self.button("Full name")).to_have_count(1)
        assert self.page.locator("#name").evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"
        assert self.page.locator("#motivation").evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"
        self.page.evaluate("history.pushState({}, '', '?nextApplication=1'); document.body.appendChild(document.createElement('div'))")
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(0)
        assert self.page.locator("#name").evaluate("node => ({color:getComputedStyle(node).outlineColor, style:getComputedStyle(node).outlineStyle, offset:getComputedStyle(node).outlineOffset})") == {
            "color": "rgb(255, 0, 0)", "style": "dashed", "offset": "4px",
        }
        assert self.page.locator("#motivation").evaluate("node => node.style.getPropertyValue('outline')") == ""
        assert self.page.locator("#motivation").evaluate("node => getComputedStyle(node).borderColor") == "rgb(0, 128, 0)"

    def hover_only_field_buttons(self):
        self.seed()
        self.mock(answer="Jordan Example")
        self.page.goto(self.url)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        button = self.page.get_by_role("button", name="Fill with AI: Full name", exact=True, include_hidden=True)
        expect(button).to_have_count(1)
        assert not button.is_visible(), "Field button should be hidden until hover or focus"
        self.page.locator("#name").hover()
        expect(button).to_be_visible()
        self.page.mouse.move(0, 0)
        expect(button).to_be_hidden()
        self.page.locator("#name").hover()
        button.click()
        expect(self.page.locator("#name")).to_have_value("Jordan Example")
        expect(self.page.locator("#wc-ai-root .wc-toast")).to_have_count(0)

    def scan_state_and_frame_only_fill_all(self):
        self.seed()
        self.mock(delay=25)
        self.page.goto(self.url.replace("application.html", "frame_only.html"))
        scan = self.page.locator("#wc-ai-root .wc-scan-button")
        expect(scan).to_be_visible()
        self.page.evaluate("""() => {
          window.scanButtonStates = [];
          window.scanFeedbackStates = [];
          const button = document.querySelector('#wc-ai-root').shadowRoot.querySelector('.wc-scan-button');
          const panel = button.closest('.wc-control-panel');
          new MutationObserver(() => {
            window.scanButtonStates.push(button.textContent);
            window.scanFeedbackStates.push({busy: button.getAttribute('aria-busy'), spinner: getComputedStyle(button, '::before').content, progress: panel.dataset.busy});
          }).observe(button, {attributes:true,childList:true,characterData:true,subtree:true});
        }""")
        scan.click()
        expect(scan).to_have_text("Scan again")
        assert "Scanning…" in self.page.evaluate("window.scanButtonStates")
        assert any(state == {"busy": "true", "spinner": '""', "progress": "true"} for state in self.page.evaluate("window.scanFeedbackStates"))
        assert self.page.locator("#wc-ai-root .wc-fill-button").count() == 0
        expect(self.page.locator("#wc-ai-root .wc-panel-status")).to_have_text("1 input found")
        expect(self.page.locator("#wc-ai-root .wc-panel-count")).to_have_text("1")
        fill_all = self.page.locator("#wc-ai-root .wc-do-all-button")
        expect(fill_all).to_be_visible()
        fill_all.click()
        expect(self.page.frame_locator('iframe[title="Application questions"]').locator("#frame-answer")).to_have_value(ANSWER)
        expect(self.page.locator("#wc-ai-root .wc-toast")).to_have_count(0)

    def popup_scan_updates_page_count(self):
        self.seed()
        self.page.goto(self.url.replace("application.html", "frame_only.html"))
        badge = self.page.locator("#wc-ai-root .wc-panel-count")
        expect(badge).to_be_hidden()
        popup = self.context.new_page()
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            popup.locator("#rescan-button").click()
            expect(badge).to_have_text("1")
            expect(self.page.locator("#wc-ai-root .wc-panel-status")).to_have_text("1 input found")
        finally:
            popup.close()

    def do_all_fills_scanned_empty_fields(self):
        self.seed()
        self.mock(delay=25)
        self.page.goto(self.url)
        do_all = self.page.locator("#wc-ai-root .wc-do-all-button")
        expect(do_all).to_be_hidden()
        do_all.evaluate("node => node.click()")
        assert self.worker.evaluate("self.__qaRequests") == []
        self.page.locator("#name").fill("My own name")
        self.page.locator("#existing-work").uncheck()
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(do_all).to_be_visible()
        expect(do_all).to_have_text("FILL")
        popup = self.context.new_page()
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#do-all-button")).to_be_visible()
            expect(popup.locator("#do-all-button")).to_contain_text("FILL")
            popup.locator("#do-all-button").click()
            expect(popup.locator("#page-status")).to_contain_text("FILL started")
            expect(self.page.locator("#email")).to_have_value("jordan@example.test")
            expect(self.page.locator("#motivation")).to_have_value(ANSWER)
            expect(self.page.locator("#bio")).to_have_js_property("innerText", ANSWER)
            expect(self.page.frame_locator('iframe[title="Same origin questions"]').locator("#frame-answer")).to_have_value(ANSWER)
            expect(self.page.frame_locator("#cross-frame").locator("#frame-answer")).to_have_value(ANSWER)
            expect(self.page.locator("#name")).to_have_value("My own name")
            expect(self.page.locator("#experience")).to_have_value("5")
            expect(self.page.locator("#accessible-work")).to_be_checked()
            expect(self.page.locator("#backend-work")).not_to_be_checked()
            expect(self.page.locator("#existing-work")).to_be_checked()
            expect(self.page.locator("#focus-area")).to_have_value("frontend")
            expect(self.page.locator(".select-shell .select__single-value")).to_have_text("frontend")
            expect(self.page.locator("#existing-focus")).to_have_value("frontend")
            expect(self.page.locator('input[name="survey-age"][value="Prefer not to answer"]')).to_be_checked()
            expect(self.page.locator('input[name="interview-time"][value="morning"]')).to_be_checked()
            expect(self.page.locator("#wc-ai-root .wc-toast").last).to_contain_text("Add a PDF resume")
            assert self.page.locator("#upload").evaluate("node => node.files.length") == 0
            assert self.page.locator("#consent").is_checked() is False
            assert self.page.evaluate("window.fixtureSubmitted") is False
            requests = self.worker.evaluate("self.__qaRequests")
            assert len(requests) == 1, "FILL ALL must make one API request across the page and frames"
            context = json.loads(next(part["text"] for part in requests[0]["payload"]["input"][0]["content"] if part["type"] == "input_text"))
            schema = requests[0]["payload"]["text"]["format"]["schema"]
            assert len(context["targetFields"]) == len(schema["required"])
            assert set(schema["required"]) == set(schema["properties"])
            assert any(field["type"] == "email" for field in context["targetFields"])
            checkbox_groups = [field for field in context["targetFields"] if field["type"] == "checkbox_group" and field["label"] == "Which kinds of work have you done?"]
            assert len(checkbox_groups) == 1
            assert schema["properties"][checkbox_groups[0]["fieldId"]]["properties"]["answer"]["type"] == "array"
            assert any(field["type"] == "select" for field in context["targetFields"])
            assert any(field["label"] == "Preferred engineering area" and [option["value"] for option in field["options"]] == ["frontend", "design-systems", "backend"] for field in context["targetFields"])
            assert len([field for field in context["targetFields"] if field["type"] == "radio" and field["label"] == "What is your age range?"]) == 1
            assert all(field["label"] != "Consent checkbox" for field in context["targetFields"])
            assert all("answer" in entry["properties"] for entry in schema["properties"].values())
            expect(do_all).to_be_enabled()
            self.page.locator("#motivation").fill("")
            do_all.click()
            expect(self.page.locator("#motivation")).to_have_value(ANSWER)
            assert len(self.worker.evaluate("self.__qaRequests")) == 2
            second_context = json.loads(next(part["text"] for part in self.worker.evaluate("self.__qaRequests")[1]["payload"]["input"][0]["content"] if part["type"] == "input_text"))
            assert all(field["type"] != "checkbox_group" for field in second_context["targetFields"]), "An answered checkbox group should be preserved by FILL ALL"
            assert self.page.evaluate("window.fixtureSubmitted") is False
        finally:
            popup.close()

    def fill_all_skips_invalid_choice_and_continues(self):
        self.seed()
        self.mock(delay=25)
        self.page.goto(self.url)
        self.page.evaluate("""() => {
          const label = document.createElement('label');
          label.htmlFor = 'malformed-choice';
          label.textContent = 'Preferred test track';
          const select = document.createElement('select');
          select.id = 'malformed-choice';
          select.innerHTML = '<option value="">Choose a track</option>';
          const option = document.createElement('option');
          option.value = 'x'.repeat(501);
          option.textContent = 'Long option';
          select.append(option);
          document.querySelector('#application').prepend(label, select);
        }""")
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(self.button("Preferred test track")).to_have_count(1)
        self.page.locator("#wc-ai-root .wc-do-all-button").click()
        expect(self.page.locator("#motivation")).to_have_value(ANSWER)
        expect(self.page.locator("#email")).to_have_value("jordan@example.test")
        expect(self.page.locator("#malformed-choice")).to_have_value("")
        expect(self.toast()).to_contain_text("1 scanned field")
        expect(self.toast()).to_contain_text("1 skipped")
        requests = self.worker.evaluate("self.__qaRequests")
        assert len(requests) == 1, "Valid fields should still use one batch request"
        context = json.loads(next(part["text"] for part in requests[0]["payload"]["input"][0]["content"] if part["type"] == "input_text"))
        assert all(field["label"] != "Preferred test track" for field in context["targetFields"])
        assert self.page.evaluate("window.fixtureSubmitted") is False

    def resume_upload_single_and_all(self):
        self.seed(resumeFile=self.saved_resume())
        self.mock(delay=25)
        self.page.goto(self.url)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.page.locator("#upload").scroll_into_view_if_needed()
        expect(self.resume_button("Resume file")).to_be_visible()
        expect(self.resume_button("Upload CV")).to_have_count(1)
        self.resume_button("Resume file").click()
        self.page.wait_for_function("() => document.querySelector('#upload').files.length === 1")
        assert self.page.locator("#upload").evaluate("node => node.files[0]?.name") == "resume.pdf"
        assert self.page.locator("#upload").evaluate("node => node.files[0]?.size") == self.saved_resume()["size"]
        assert [event["type"] for event in self.page.evaluate("window.fixtureEvents.filter(event => event.id === 'upload')")] == ["input", "change"]
        assert not self.worker.evaluate("self.__qaRequests"), "Attaching a resume should not call the model"
        self.resume_button("Resume file").click()
        assert self.page.locator("#upload").evaluate("node => node.files.length") == 1
        self.page.locator("#wc-ai-root .wc-do-all-button").click()
        self.page.wait_for_function("() => document.querySelector('#hidden-resume').files.length === 1")
        assert self.page.locator("#hidden-resume").evaluate("node => node.files[0]?.name") == "resume.pdf"
        assert self.page.locator("#button-resume").evaluate("node => node.files[0]?.name") == "resume.pdf"
        expect(self.page.locator('[aria-labelledby="upload-label-greenhouse-resume"]')).to_contain_text("resume.pdf")
        assert self.page.locator("#greenhouse-resume").count() == 0, "Greenhouse replaces the file input after upload"
        assert self.page.locator("#greenhouse-cover").evaluate("node => node.files.length") == 0
        assert self.page.locator("#portfolio-upload").evaluate("node => node.files.length") == 0
        assert self.page.locator("#upload").evaluate("node => node.files.length") == 1
        assert self.page.evaluate("window.fixtureSubmitted") is False
        expect(self.page.locator("#motivation")).to_have_value(ANSWER)
        requests = self.worker.evaluate("self.__qaRequests")
        assert len(requests) == 1
        context = json.loads(next(part["text"] for part in requests[0]["payload"]["input"][0]["content"] if part["type"] == "input_text"))
        assert all(field["type"] != "file" for field in context["targetFields"])

    def greenhouse_resume_upload(self):
        self.seed(apiKey="", resumeFile=self.saved_resume())
        self.page.goto(self.url)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.page.locator('[aria-labelledby="upload-label-greenhouse-resume"]').scroll_into_view_if_needed()
        self.page.evaluate("window.dispatchEvent(new Event('scroll'))")
        expect(self.resume_button("Resume/CV")).to_be_visible()
        assert self.page.locator('[aria-labelledby="upload-label-greenhouse-resume"] button').first.evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"
        self.resume_button("Resume/CV").click()
        expect(self.page.locator('[aria-labelledby="upload-label-greenhouse-resume"]')).to_contain_text("resume.pdf")
        assert [event["type"] for event in self.page.evaluate("window.fixtureEvents.filter(event => event.id === 'greenhouse-resume')")] == ["input", "change"]
        assert self.page.locator("#greenhouse-cover").evaluate("node => node.files.length") == 0
        assert self.page.evaluate("window.fixtureSubmitted") is False

    def agreement_dropdowns_stay_manual(self):
        self.seed()
        self.page.goto(self.url)
        self.page.evaluate("""() => {
          const form = document.querySelector('#application');
          form.insertAdjacentHTML('afterbegin', `
            <label for="ai-policy">AI Policy for Application</label>
            <select id="ai-policy"><option value="">Select...</option><option value="yes">Yes</option><option value="no">No</option></select>
            <label for="arbitration">Agreement to Arbitrate</label>
            <select id="arbitration"><option value="">Select...</option><option value="agree">I agree</option></select>
            <label for="work-location">Are you open to working in-person?</label>
            <select id="work-location"><option value="">Select...</option><option value="yes">Yes</option><option value="no">No</option></select>
          `);
        }""")
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(self.button("AI Policy for Application")).to_have_count(0)
        expect(self.button("Agreement to Arbitrate")).to_have_count(0)
        expect(self.button("Are you open to working in-person?")).to_have_count(1)
        assert self.page.locator("#ai-policy").evaluate("node => getComputedStyle(node).outlineStyle") != "solid"
        assert self.page.locator("#arbitration").evaluate("node => getComputedStyle(node).outlineStyle") != "solid"

    def resume_upload_without_pdf(self):
        self.fresh()
        self.page.locator("#upload").scroll_into_view_if_needed()
        self.resume_button("Resume file").click()
        expect(self.toast()).to_contain_text("Add a PDF resume")
        assert self.page.locator("#upload").evaluate("node => node.files.length") == 0
        assert not self.worker.evaluate("self.__qaRequests")

    def resume_upload_without_api_key(self):
        self.seed(apiKey="", resumeFile=self.saved_resume())
        self.mock()
        self.page.goto(self.url)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.page.locator("#upload").scroll_into_view_if_needed()
        self.resume_button("Resume file").click()
        self.page.wait_for_function("() => document.querySelector('#upload').files.length === 1")
        assert not self.worker.evaluate("self.__qaRequests")

    def react_select_dropdown(self):
        self.fresh()
        self.page.locator("#react-dropdown").scroll_into_view_if_needed()
        self.button("Preferred engineering area").click()
        expect(self.page.locator(".select__single-value")).to_have_text("frontend")
        assert self.page.locator("#react-dropdown").input_value() == "", "React Select keeps its choice outside the text input"
        request = self.worker.evaluate("self.__qaRequests")[-1]["payload"]
        context = json.loads(next(part["text"] for part in request["input"][0]["content"] if part["type"] == "input_text"))
        assert context["targetField"]["type"] == "select"
        assert [option["value"] for option in context["targetField"]["options"]] == ["frontend", "design-systems", "backend"]
        assert request["text"]["format"]["schema"]["properties"]["answer"]["enum"] == ["frontend", "design-systems", "backend"]
        assert self.page.evaluate("window.fixtureSubmitted") is False

    def do_all_stop_cancels_every_frame(self):
        self.seed()
        self.mock(delay=3000)
        self.page.goto(self.url)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.page.locator("#wc-ai-root .wc-do-all-button").click()
        deadline = time.monotonic() + 5
        while len(self.worker.evaluate("self.__qaRequests")) < 1 and time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
        assert len(self.worker.evaluate("self.__qaRequests")) == 1
        stop = self.page.locator("#wc-ai-root .wc-do-all-button")
        expect(stop).to_have_text("Stop")
        expect(stop).to_have_attribute("aria-busy", "true")
        expect(self.page.locator("#wc-ai-root .wc-control-panel")).to_have_attribute("data-busy", "true")
        assert stop.evaluate("node => getComputedStyle(node, '::before').content") == '""'
        expect(self.page.locator("#wc-ai-root .wc-panel-status")).to_have_text("Writing answers…")
        self.page.evaluate("""() => {
          window.fillAllStates = [];
          const button = document.querySelector('#wc-ai-root').shadowRoot.querySelector('.wc-do-all-button');
          new MutationObserver(() => window.fillAllStates.push(button.textContent)).observe(button, {childList:true,characterData:true,subtree:true});
        }""")
        stop.click()
        expect(stop).to_have_text("FILL")
        expect(stop).to_have_attribute("aria-busy", "false")
        assert any(state.startswith("Stopping") for state in self.page.evaluate("window.fillAllStates"))
        expect(self.page.locator("#wc-ai-root .wc-toast")).to_have_count(0)
        expect(self.page.locator("#name")).to_have_value("")
        expect(self.page.locator("#email")).to_have_value("")
        expect(self.page.frame_locator('iframe[title="Same origin questions"]').locator("#frame-answer")).to_have_value("")
        expect(self.page.frame_locator("#cross-frame").locator("#frame-answer")).to_have_value("")
        assert self.page.evaluate("window.fixtureSubmitted") is False

    def do_all_preserves_edits_during_generation(self):
        self.seed()
        self.mock(delay=600)
        self.page.goto(self.url)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.page.locator("#wc-ai-root .wc-do-all-button").click()
        deadline = time.monotonic() + 5
        while not self.worker.evaluate("self.__qaRequests") and time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
        assert len(self.worker.evaluate("self.__qaRequests")) == 1
        self.page.locator("#motivation").fill("My own answer")
        self.page.locator("#focus-area").select_option("design-systems")
        self.page.locator("#accessible-work").check()
        expect(self.page.locator("#email")).to_have_value("jordan@example.test")
        expect(self.page.locator("#motivation")).to_have_value("My own answer")
        expect(self.page.locator("#focus-area")).to_have_value("design-systems")
        expect(self.page.locator("#accessible-work")).to_be_checked()
        expect(self.page.frame_locator("#cross-frame").locator("#frame-answer")).to_have_value(ANSWER)
        assert len(self.worker.evaluate("self.__qaRequests")) == 1
        assert self.page.evaluate("window.fixtureSubmitted") is False

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
        self.mock(delay=1200)
        self.button("Why do you want to work here").click()
        busy = self.page.get_by_role("button", name=re.compile("Cancel writing: Why do you want to work here"), include_hidden=True)
        expect(busy).to_have_attribute("aria-busy", "true")
        expect(self.page.locator("#wc-ai-root .wc-toast")).to_have_count(0)
        self.screenshot("waiting-for-llm")
        expect(self.page.locator("#motivation")).to_have_value(ANSWER)
        expect(self.page.locator("#wc-ai-root .wc-toast")).to_have_count(0)
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
        self.undo_button("Why do you want to work here at Northstar Robotics?").click()
        expect(self.page.locator("#motivation")).to_have_value("")

    def email_and_contenteditable(self):
        self.fresh()
        self.mock(answer="jordan@example.test")
        self.button("Email address").click()
        expect(self.page.locator("#email")).to_have_value("jordan@example.test")
        expect(self.page.locator("#wc-ai-root .wc-toast")).to_have_count(0)
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

    def checkbox_and_dropdown_choices(self):
        self.fresh()
        self.page.locator("#accessible-work").scroll_into_view_if_needed()
        expect(self.button("Which kinds of work have you done?")).to_have_count(1)
        expect(self.button("Accessible interfaces")).to_have_count(0)
        self.button("Which kinds of work have you done?").click()
        expect(self.page.locator("#accessible-work")).to_be_checked()
        expect(self.page.locator("#backend-work")).not_to_be_checked()
        expect(self.page.locator("#existing-work")).to_be_checked()
        assert {event["type"] for event in self.page.evaluate("window.fixtureEvents") if event["id"] == "accessible-work"} >= {"input", "change"}
        request = self.worker.evaluate("self.__qaRequests")[0]["payload"]
        context = json.loads(request["input"][0]["content"][0]["text"])
        assert context["targetField"]["type"] == "checkbox_group"
        assert context["targetField"]["label"] == "Which kinds of work have you done?"
        assert [option["label"] for option in context["targetField"]["options"]] == ["Accessible interfaces", "Backend systems", "Design systems"]
        assert request["text"]["format"]["schema"]["properties"]["answer"]["type"] == "array"
        self.undo_button("Which kinds of work have you done?").click()
        expect(self.page.locator("#accessible-work")).not_to_be_checked()
        expect(self.page.locator("#existing-work")).to_be_checked()

        self.page.evaluate("""() => {
          const label = document.createElement('label');
          label.innerHTML = '<input id="standalone-checkbox" type="checkbox"> Open to remote work';
          document.querySelector('#application').prepend(label);
        }""")
        self.page.locator("#standalone-checkbox").scroll_into_view_if_needed()
        self.page.locator("#standalone-checkbox").hover()
        self.button("Open to remote work").click()
        expect(self.page.locator("#standalone-checkbox")).to_be_checked()
        standalone = self.worker.evaluate("self.__qaRequests")[-1]["payload"]
        assert json.loads(standalone["input"][0]["content"][0]["text"])["targetField"]["type"] == "checkbox"
        assert standalone["text"]["format"]["schema"]["properties"]["answer"]["anyOf"][1]["type"] == "boolean"

        self.mock()
        self.page.locator("#focus-area").scroll_into_view_if_needed()
        self.button("Which area best matches your background").click()
        expect(self.page.locator("#focus-area")).to_have_value("frontend")
        assert {event["type"] for event in self.page.evaluate("window.fixtureEvents") if event["id"] == "focus-area"} >= {"input", "change"}
        request = self.worker.evaluate("self.__qaRequests")[0]["payload"]
        context = json.loads(request["input"][0]["content"][0]["text"])
        assert context["targetField"]["type"] == "select"
        assert context["targetField"]["options"] == [
            {"value": "frontend", "label": "Frontend engineering"},
            {"value": "design-systems", "label": "Design systems"},
        ]
        options = request["text"]["format"]["schema"]["properties"]["answer"]["enum"]
        assert options == ["frontend", "design-systems"]
        self.undo_button("Which area best matches your background?").click()
        expect(self.page.locator("#focus-area")).to_have_value("")
        assert self.page.locator("#consent").is_checked() is False
        assert self.page.evaluate("window.fixtureSubmitted") is False

    def choice_groups_from_saved_runs(self):
        self.seed()
        self.mock(delay=25)
        self.page.goto(self.url.replace("application.html", "choice_groups.html"))
        self.page.locator('input[name="wrapper_two"]').evaluate("node => { node.checked = true; }")
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(8)
        for selector in ("#workable-question", "#greenhouse-question", "#ashby-question", "#custom-radio-question", "#custom-checkbox-question"):
            assert self.page.locator(selector).evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"
        assert self.page.locator("#hybrid-choice-label").evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"
        self.page.locator('input[name="wrapper_one"]').scroll_into_view_if_needed()
        self.button("Authorized to work for any employer").click()
        expect(self.page.locator('input[name="wrapper_one"]')).to_be_checked()
        expect(self.page.locator('input[name="wrapper_two"]')).not_to_be_checked()
        request = self.worker.evaluate("self.__qaRequests")[0]["payload"]
        radio = json.loads(request["input"][0]["content"][0]["text"])["targetField"]
        assert radio["label"] == "Authorized to work for any employer in the US without sponsorship"
        assert [option["value"] for option in radio["options"]] == ["YES", "NO"]

        self.page.locator('#custom-radio-question [role="radio"]').first.scroll_into_view_if_needed()
        self.button("Preferred interview window").click()
        expect(self.page.locator('#custom-radio-question [role="radio"]').first).to_have_attribute("aria-checked", "true")
        self.page.locator('#custom-checkbox-question [role="checkbox"]').first.scroll_into_view_if_needed()
        self.button("Engineering interests").click()
        expect(self.page.locator('#custom-checkbox-question [role="checkbox"]').first).to_have_attribute("aria-checked", "true")
        self.page.locator("#custom-single-checkbox").scroll_into_view_if_needed()
        self.button("Open to remote work").click()
        expect(self.page.locator("#custom-single-checkbox")).to_have_attribute("aria-checked", "true")
        self.page.locator("#custom-switch").scroll_into_view_if_needed()
        self.button("Open to relocation").click()
        expect(self.page.locator("#custom-switch")).to_have_attribute("aria-checked", "true")

        self.page.reload()
        self.mock(delay=25)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.page.locator("#wc-ai-root .wc-do-all-button").click()
        expect(self.page.locator('input[name="wrapper_one"]')).to_be_checked()
        expect(self.page.locator('input[name="terraform_group"]').first).to_be_checked()
        expect(self.page.locator('input[name="Prefer not"]')).to_be_checked()
        expect(self.page.locator('#custom-radio-question [role="radio"]').first).to_have_attribute("aria-checked", "true")
        expect(self.page.locator('#custom-checkbox-question [role="checkbox"]').first).to_have_attribute("aria-checked", "true")
        expect(self.page.locator("#custom-single-checkbox")).to_have_attribute("aria-checked", "true")
        expect(self.page.locator("#custom-switch")).to_have_attribute("aria-checked", "true")
        expect(self.page.locator("#hybrid-choice")).to_be_checked()
        requests = self.worker.evaluate("self.__qaRequests")
        assert len(requests) == 1
        context = json.loads(next(part["text"] for part in requests[0]["payload"]["input"][0]["content"] if part["type"] == "input_text"))
        assert [field["type"] for field in context["targetFields"]] == ["radio", "checkbox_group", "checkbox_group", "radio", "checkbox_group", "checkbox", "checkbox", "checkbox"]

    def mixed_choice_and_controlled_combobox(self):
        self.seed()
        self.mock(delay=25)
        self.page.goto(self.url.replace("application.html", "edge_controls.html"))
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(3)
        expect(self.button("How did you hear about ElevenLabs?")).to_have_count(1)
        expect(self.button("If other, please specify below")).to_have_count(1)
        expect(self.button("Country of residence")).to_have_count(1)
        expect(self.button("Unsupported dropdown")).to_have_count(0)
        assert self.page.locator("#ashby-referral").evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"
        self.button("How did you hear about ElevenLabs?").click()
        expect(self.page.locator('input[name="referral"]').first).to_be_checked()
        radio_request = self.worker.evaluate("self.__qaRequests")[-1]["payload"]
        radio = json.loads(radio_request["input"][0]["content"][0]["text"])["targetField"]
        assert [option["value"] for option in radio["options"]] == ["user", "other"]
        self.button("Country of residence").click()
        expect(self.page.locator('[data-automation-id="selectedItem"]')).to_have_text("Canada")
        country_request = self.worker.evaluate("self.__qaRequests")[-1]["payload"]
        country = json.loads(country_request["input"][0]["content"][0]["text"])["targetField"]
        assert country["type"] == "select"
        assert [option["value"] for option in country["options"]] == ["Canada", "United States"]
        answer_schema = country_request["text"]["format"]["schema"]["properties"]["answer"]
        assert answer_schema["anyOf"][1]["enum"] == ["Canada", "United States"]

        self.page.reload()
        self.mock(delay=25)
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.page.locator("#wc-ai-root .wc-do-all-button").click()
        expect(self.page.locator('input[name="referral"]').first).to_be_checked()
        expect(self.page.locator("#other-detail")).to_have_value(ANSWER)
        expect(self.page.locator('[data-automation-id="selectedItem"]')).to_have_text("Canada")
        requests = self.worker.evaluate("self.__qaRequests")
        assert len(requests) == 1
        context = json.loads(next(part["text"] for part in requests[0]["payload"]["input"][0]["content"] if part["type"] == "input_text"))
        assert [field["type"] for field in context["targetFields"]] == ["radio", "text", "select"]

    def ashby_live_markup_and_control_panel(self):
        self.seed()
        self.mock(delay=25, answer="Canada")
        self.page.goto(self.url.replace("application.html", "ashby_controls.html"))
        panel = self.page.locator("#wc-ai-root .wc-control-panel")
        expect(panel).to_be_visible()
        assert panel.evaluate("node => getComputedStyle(node).borderTopColor") == "rgb(227, 24, 0)"
        assert panel.locator(".wc-panel-logo").evaluate("node => node.complete && node.naturalWidth > 0")
        expect(panel.locator(".wc-do-all-button")).to_be_hidden()
        panel.locator(".wc-scan-button").click()
        expect(panel.locator(".wc-panel-status")).to_have_text("4 inputs found")
        expect(panel.locator(".wc-panel-count")).to_have_text("4")
        expect(self.page.locator("#wc-ai-root .wc-fill-button")).to_have_count(4)
        for label in (
            "Have you written and maintained code used in a product?",
            "Are you authorized to work in this location?",
            "Which city and country do you intend to work from?",
            "Which communities do you belong to? Please select all that apply.",
        ):
            expect(self.button(label)).to_have_count(1)
        expect(self.button("Start typing...")).to_have_count(0)
        expect(panel.locator(".wc-do-all-button")).to_be_visible()
        self.screenshot("ashby-control-panel")
        self.button("Are you authorized to work in this location?").click()
        expect(self.page.locator('button[data-option="yes"]')).to_have_attribute("aria-pressed", "true")
        self.button("Which city and country do you intend to work from?").click()
        expect(self.page.locator(".ashby-application-form-input-autocomplete")).to_have_value("Canada")
        assert self.page.locator(".ashby-application-form-input-autocomplete").get_attribute("aria-expanded") == "false"

        self.page.reload()
        self.mock(delay=25, answer="Atlantis")
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.button("Which city and country do you intend to work from?").click()
        expect(self.toast()).to_contain_text("No exact location suggestion")
        expect(self.page.locator(".ashby-application-form-input-autocomplete")).to_have_value("")

        self.page.reload()
        self.mock(delay=25, answer="No")
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.button("Are you authorized to work in this location?").click()
        expect(self.page.locator('button[data-option="no"]')).to_have_attribute("aria-pressed", "true")
        expect(self.page.locator('button[data-option="yes"]')).to_have_attribute("aria-pressed", "false")
        expect(self.page.locator('.ashby-application-form-input-yesno input')).not_to_be_checked()

        self.page.reload()
        self.mock(delay=25, answer="Canada")
        self.page.locator("#wc-ai-root .wc-scan-button").click()
        self.page.locator("#wc-ai-root .wc-do-all-button").click()
        expect(self.page.locator("#experience-no")).to_be_checked()
        expect(self.page.locator('button[data-option="yes"]')).to_have_attribute("aria-pressed", "true")
        expect(self.page.locator(".ashby-application-form-input-autocomplete")).to_have_value("Canada")
        expect(self.page.locator("#community-question input").first).to_be_checked()
        requests = self.worker.evaluate("self.__qaRequests")
        assert len(requests) == 1
        context = json.loads(next(part["text"] for part in requests[0]["payload"]["input"][0]["content"] if part["type"] == "input_text"))
        assert [field["type"] for field in context["targetFields"]] == ["radio", "select", "text", "checkbox_group"]
        yes_no = context["targetFields"][1]
        assert yes_no["label"] == "Are you authorized to work in this location?"
        assert [option["value"] for option in yes_no["options"]] == ["Yes", "No"]

    def lever_radio_group(self):
        self.fresh()
        self.page.locator('input[name="survey-age"]').first.scroll_into_view_if_needed()
        expect(self.button("What is your age range?")).to_have_count(1)
        question = self.page.locator(".application-question")
        assert question.evaluate("node => getComputedStyle(node).outlineColor") == "rgb(139, 92, 246)"
        self.button("What is your age range?").click()
        expect(self.page.locator('input[name="survey-age"][value="Prefer not to answer"]')).to_be_checked()
        assert self.page.locator('input[name="survey-age"]:checked').count() == 1
        assert {event["type"] for event in self.page.evaluate("window.fixtureEvents") if event["id"] == "" and event["value"] == "Prefer not to answer"} >= {"input", "change"}
        request = self.worker.evaluate("self.__qaRequests")[0]["payload"]
        context = json.loads(request["input"][0]["content"][0]["text"])
        radio = context["targetField"]
        assert radio["type"] == "radio" and radio["label"] == "What is your age range?"
        assert [option["value"] for option in radio["options"]] == ["17 or younger", "18-20", "21-29", "30-39", "40-49", "50-59", "60 or older", "Prefer not to answer"]
        assert request["text"]["format"]["schema"]["properties"]["answer"]["anyOf"][1]["enum"] == [option["value"] for option in radio["options"]]
        self.undo_button("What is your age range?").click()
        assert self.page.locator('input[name="survey-age"]:checked').count() == 0
        expect(self.page.locator('input[name="interview-time"][value="morning"]')).to_be_checked()

    def cancellation(self):
        self.fresh()
        self.mock(delay=5000)
        self.button("Why do you want to work here").click()
        busy = self.page.get_by_role("button", name=re.compile("Cancel writing: Why do you want to work here"), include_hidden=True)
        expect(busy).to_have_attribute("aria-busy", "true")
        busy.click()
        expect(self.page.locator("#wc-ai-root .wc-toast")).to_have_count(0)
        expect(self.page.locator("#motivation")).to_have_value("")
        expect(self.button("Why do you want to work here")).to_be_enabled()
        self.page.wait_for_timeout(800)
        expect(self.page.locator("#motivation")).to_have_value("")
        self.screenshot("cancelled-request")

    def edit_conflicts(self):
        self.fresh()
        self.mock(delay=1200)
        self.button("Why do you want to work here").click()
        expect(self.page.get_by_role("button", name=re.compile("Cancel writing: Why do you want to work here"), include_hidden=True)).to_have_attribute("aria-busy", "true")
        self.page.locator("#motivation").fill("My own answer while waiting.")
        expect(self.page.locator("#motivation")).to_have_value("My own answer while waiting.")
        expect(self.page.locator("#wc-ai-root .wc-toast")).to_have_count(0)
        self.fresh()
        self.button("Why do you want to work here").click()
        expect(self.page.locator("#motivation")).to_have_value(ANSWER)
        self.page.locator("#motivation").fill("A deliberate edit after the generated answer.")
        expect(self.page.get_by_role("button", name="Undo generated answer: Why do you want to work here at Northstar Robotics?", exact=True, include_hidden=True)).to_have_count(0)
        expect(self.page.locator("#motivation")).to_have_value("A deliberate edit after the generated answer.")
        self.fresh()
        self.mock(delay=1200)
        self.button("Why do you want to work here").click()
        expect(self.page.get_by_role("button", name=re.compile("Cancel writing: Why do you want to work here"), include_hidden=True)).to_have_attribute("aria-busy", "true")
        self.page.locator("label[for=motivation]").evaluate("node => node.textContent = 'What salary do you expect?'")
        expect(self.page.locator("#motivation")).to_have_value("")
        expect(self.page.locator("#wc-ai-root .wc-toast")).to_have_count(0)

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
        self.mock(delay=12000)
        assert self.worker.evaluate("self.__qaDelay") == 12000
        self.page.locator("#motivation").fill("My original draft.")
        self.button("Why do you want to work here").click()
        deadline = time.monotonic() + 5
        while not self.worker.evaluate("self.__qaRequests") and time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
        assert len(self.worker.evaluate("self.__qaRequests")) == 1
        expect(self.page.get_by_role("button", name=re.compile("Cancel writing: Why do you want to work here"), include_hidden=True)).to_have_attribute("aria-busy", "true")
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
            expect(popup.locator("#home-screen")).to_be_visible()
            expect(popup.locator("#panel-profile")).to_be_hidden()
            popup.locator("#open-profile").click()
            expect(popup.locator("#panel-profile")).to_be_visible()
            expect(popup.locator("#profile")).to_have_value(SETTINGS["profile"])
            popup.locator("#fact-fullName").fill("Jordan Example")
            popup.locator("#fact-email").fill("jordan@example.test")
            popup.locator("#fact-phone").fill("+1 604 555 0100")
            popup.locator("#fact-location").fill("Vancouver, Canada")
            popup.locator("#fact-veteranStatus").select_option("No")
            popup.locator("#profile").fill("Jordan Example. I enjoy accessible robotics.")
            popup.locator("#writing-instructions").fill("Use a warm professional voice. Mention teamwork.")
            self.upload(popup, "resume-upload", str(FIXTURES / "resume.txt"))
            expect(popup.locator("#resume-text")).to_have_value(re.compile("Jordan Example"))
            expect(popup.locator("#save-label")).to_have_text("Save profile")
            popup.locator("#save-button").click()
            expect(popup.locator("#save-status")).to_contain_text(re.compile("saved", re.I))
            settings = self.stored_settings()
            assert settings["profile"] == "Jordan Example. I enjoy accessible robotics."
            assert settings["profileFacts"]["location"] == "Vancouver, Canada"
            assert settings["profileFacts"]["veteranStatus"] == "No"
            assert settings["writingInstructions"] == "Use a warm professional voice. Mention teamwork."
            assert "Jordan Example" in settings["resumeText"]
            self.screenshot("popup-profile", popup)

            popup.locator("#back-profile").click()
            expect(popup.locator("#home-screen")).to_be_visible()
            popup.locator("#open-connection").click()
            expect(popup.locator("#panel-connection")).to_be_visible()
            expect(popup.locator("#api-key")).to_have_attribute("type", "password")
            popup.locator("#reveal-key").click()
            expect(popup.locator("#api-key")).to_have_attribute("type", "text")
            expect(popup.locator("#api-key")).to_have_value(KEY)
            popup.locator("#reveal-key").click()
            expect(popup.locator("#model-preset")).to_have_value("custom")
            expect(popup.locator("#model")).to_have_value("gpt-5-mini")
            assert {"gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "gpt-4.1", "gpt-4.1-mini", "custom"}.issubset(set(popup.locator("#model-preset option").evaluate_all("options => options.map(option => option.value)")))
            popup.locator("#model-preset").select_option("gpt-6.1-sol")
            expect(popup.locator("#model-custom")).to_be_hidden()
            popup.locator("#model-preset").select_option("custom")
            expect(popup.locator("#model")).to_have_value("gpt-5-mini")
            popup.locator("#model-preset").select_option("gpt-6.1-sol")
            expect(popup.locator("#save-label")).to_have_text("Save settings")
            popup.locator("#save-button").click()
            expect(popup.locator("#save-status")).to_contain_text(re.compile("saved", re.I))
            assert self.stored_settings()["model"] == "gpt-6.1-sol"
            self.screenshot("popup-connection", popup)
            popup.reload()
            expect(popup.locator("#home-screen")).to_be_visible()
            popup.locator("#open-profile").click()
            expect(popup.locator("#fact-fullName")).to_have_value("Jordan Example")
            expect(popup.locator("#fact-veteranStatus")).to_have_value("No")
            popup.locator("#back-profile").click()
            popup.locator("#open-connection").click()
            expect(popup.locator("#model-preset")).to_have_value("gpt-6.1-sol")
            self.expand(popup, "advanced-settings")
            self.expand(popup, "troubleshooting")
            popup.locator("#log-refresh").click()
            assert KEY not in popup.locator("#log-output").inner_text(), "Log UI exposed API key"
            popup.locator("#log-clear").click()
        finally:
            popup.close()
        self.page.bring_to_front()
        self.mock()
        self.button("Why do you want to work here").click()
        expect(self.page.locator("#motivation")).to_have_value(ANSWER)
        payload = self.worker.evaluate("self.__qaRequests")[0]["payload"]
        assert payload["model"] == "gpt-6.1-sol"
        context = json.loads(next(part["text"] for part in payload["input"][0]["content"] if part["type"] == "input_text"))
        assert context["profileFacts"]["veteranStatus"] == "No"
        assert context["profileFacts"]["location"] == "Vancouver, Canada"

    def popup_validation_and_resume(self):
        self.fresh()
        popup = self.context.new_page()
        popup.set_viewport_size({"width":400,"height":600})
        popup.set_default_timeout(7000)
        try:
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            popup.locator("#open-connection").click()
            expect(popup.locator("#model-preset")).to_have_value("custom")
            expect(popup.locator("#model-custom")).to_be_visible()
            popup.locator("#model").fill("")
            popup.locator("#save-button").click()
            expect(popup.locator("#save-status")).to_contain_text(re.compile("model|required|enter", re.I))
            popup.locator("#model").fill("gpt-5-mini")
            popup.locator("#back-connection").click()
            popup.locator("#open-profile").click()
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
            popup.locator("#open-profile").click()
            expect(popup.locator("#profile")).to_have_value(SETTINGS["profile"])
            expect(popup.locator("#back-profile")).to_be_focused()
            popup.locator("#tone-casual").click()
            expect(popup.locator("#writing-instructions")).to_have_value(re.compile("friendly|conversational|casual", re.I))
            popup.locator("#tone-professional").click()
            expect(popup.locator("#writing-instructions")).to_have_value(re.compile("professional", re.I))
            imported = {"facts":{"first_name":"Jordan", "last_name":"Example", "email":"jordan@example.test", "phone":"+1 604 555 0100"},
                "employment":[{"company":"Synthetic Operations", "role":"Software engineer", "apiKey":"TOP_SECRET_KEY"}],
                "password":"TOP_SECRET_PASSWORD", "resume":r"C:\private\resume.pdf", "cover_letter":"/home/private/letter.txt",
                "nested":{"token":"TOP_SECRET_TOKEN", "privateKey":"TOP_SECRET_PRIVATE", "note":"sk-notarealkey1234567890"},
                "dataPath":"/Users/private/data.json"}
            self.upload(popup, "profile-import", {"name":"profile.json","mimeType":"application/json","buffer":json.dumps(imported).encode()})
            expect(popup.locator("#profile-import-status")).to_contain_text(re.compile("imported", re.I))
            expect(popup.locator("#fact-fullName")).to_have_value("Jordan Example")
            expect(popup.locator("#fact-email")).to_have_value("jordan@example.test")
            clean = popup.locator("#profile").input_value()
            for safe in ("Jordan", "jordan@example.test", "Synthetic Operations", "Software engineer"):
                assert safe in clean
            for forbidden in ("TOP_SECRET", r"C:\private", "/home/private", "/Users/private", "sk-notareal"):
                assert forbidden not in clean, f"Profile import exposed {forbidden}"
            self.upload(popup, "resume-upload", str(FIXTURES / "resume.pdf"))
            expect(popup.locator("#resume-status")).to_contain_text(re.compile("PDF attached", re.I))
            expect(popup.locator("#resume-file-name")).to_contain_text("resume.pdf")
            popup.locator("#save-button").click()
            expect(popup.locator("#save-status")).to_contain_text(re.compile("saved", re.I))
            settings = self.stored_settings()
            assert settings["resumeFile"]["dataUrl"].startswith("data:application/pdf;base64,JVBERi0")
            assert settings["profileFacts"]["fullName"] == "Jordan Example"
            self.screenshot("popup-pdf-profile", popup)
            popup.locator("#back-profile").click()
            expect(popup.locator("#home-screen")).to_be_visible()
            popup.locator("#open-connection").click()
            expect(popup.locator("#panel-connection")).to_be_visible()
            popup.locator("#back-connection").click()
            expect(popup.locator("#home-screen")).to_be_visible()
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
            expect(popup.locator("#home-screen")).to_be_visible()
            expect(popup.locator("#setup-banner")).to_be_visible()
            expect(popup.locator("#setup-banner")).to_contain_text(re.compile("key|connect|OpenAI", re.I))
            self.screenshot("popup-guided-setup", popup)
            popup.locator("#setup-action").click()
            expect(popup.locator("#panel-connection")).to_be_visible()
            popup.locator("#api-key").fill(KEY)
            self.wait_stored("apiKey", KEY)
            popup.locator("#back-connection").click()
            expect(popup.locator("#setup-action")).to_have_text("Edit profile")
            popup.locator("#setup-action").click()
            expect(popup.locator("#panel-profile")).to_be_visible()
            profile = "Jordan Example. I build accessible robotics tools with operations teams."
            popup.locator("#profile").fill(profile)
            self.wait_stored("profile", profile)
            popup.locator("#back-profile").click()
            expect(popup.locator("#setup-banner")).to_be_hidden()
            popup.close()
            popup = self.context.new_page()
            popup.set_viewport_size({"width":400,"height":600})
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            expect(popup.locator("#home-screen")).to_be_visible()
            popup.locator("#open-profile").click()
            expect(popup.locator("#profile")).to_have_value(profile)
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
            popup.locator("#open-profile").click()
            expect(popup.locator("#profile")).to_have_value(SETTINGS["profile"])
            draft = "Jordan Example. This change must survive closing the popup immediately."
            popup.locator("#profile").fill(draft)
            popup.wait_for_timeout(120)
            popup.close()
            popup = self.context.new_page()
            popup.set_viewport_size({"width":400,"height":600})
            popup.goto(f"chrome-extension://{self.extension_id}/popup.html")
            popup.locator("#open-profile").click()
            expect(popup.locator("#profile")).to_have_value(draft)
            self.wait_stored("profile", draft)
            popup.locator("#back-profile").click()
            popup.locator("#open-connection").click()
            popup.locator("#model").fill("")
            popup.locator("#back-connection").click()
            popup.locator("#open-profile").click()
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
            expect(popup.locator("#enable-toggle")).not_to_be_checked()
            popup.locator("#open-profile").click()
            expect(popup.locator("#profile")).to_have_value(second)
            popup.locator("#back-profile").click()
            popup.locator("#open-connection").click()
            expect(popup.locator("#model")).to_have_value("")
            popup.locator("#model").fill("gpt-5-mini")
            self.wait_stored("profile", second)
            popup.locator("#back-connection").click()
            popup.locator("#open-profile").click()
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
                "manual_scan_gate", "direct_highlight_restores_page_styles", "hover_only_field_buttons", "scan_state_and_frame_only_fill_all", "popup_scan_updates_page_count", "do_all_fills_scanned_empty_fields", "fill_all_skips_invalid_choice_and_continues", "react_select_dropdown", "resume_upload_single_and_all", "greenhouse_resume_upload", "agreement_dropdowns_stay_manual", "resume_upload_without_pdf", "resume_upload_without_api_key", "do_all_stop_cancels_every_frame", "do_all_preserves_edits_during_generation", "discovery", "root_scroll_container_buttons", "synthetic_click_does_not_generate", "placement_and_scroll", "paragraph_and_context", "email_and_contenteditable", "checkbox_and_dropdown_choices", "choice_groups_from_saved_runs", "mixed_choice_and_controlled_combobox", "ashby_live_markup_and_control_panel", "lever_radio_group",
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
