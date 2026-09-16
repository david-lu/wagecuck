"""Local application portal for repeatable tests; never contacts an employer."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

CONTACT = """<label>First Name*<input name="first_name" required></label>
<label>Last Name*<input name="last_name" required></label>
<label>Email*<input name="email" type="email" required></label>"""
DOCUMENT = '<label>Resume/CV*<input name="resume" type="file" accept=".pdf" required></label>'
SUCCESS = "<h1>Your application has been received</h1><p>Fixture receipt WC-12345</p>"


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, Handler)
        self.submissions = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, content, status=200):
        data = (
            "<!doctype html><html><head><title>Wagecuck fixture</title></head><body>"
            + content
            + "</body></html>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/delayed-entry":
            return self.respond("""<input type="search" placeholder="Search jobs">
            <button onclick="setTimeout(()=>location.href='/single',1500)">I'm interested</button>""")
        if path == "/search-only":
            return self.respond(
                '<input placeholder="Search..."><input type="search"><form role="search"><input placeholder="Keywords"></form>'
            )
        if path == "/closed":
            return self.respond("<h1>This position has been filled</h1>")
        if path == "/missing":
            return self.respond("Not found", 404)
        if path == "/auth":
            return self.respond(
                '<label>Email<input type="email"></label><label>Password<input type="password"></label><button>Sign in</button>'
            )
        if path == "/entry":
            return self.respond(
                '<h1>Software Engineer</h1><a href="/single" target="_blank">Apply now</a>'
            )
        if path == "/iframe":
            return self.respond(
                '<h1>Careers</h1><iframe src="/single" style="width:900px;height:800px"></iframe>'
            )
        if path == "/review":
            return self.respond(
                '<h1>Review application</h1><form method="post" action="/submit"><button>Submit application</button></form>'
            )
        if path == "/thank-you":
            return self.respond(SUCCESS)
        if path == "/captcha":
            return self.respond(
                'Verify you are human <div class="g-recaptcha" data-sitekey="fixture-key"></div>'
            )
        if path == "/modal":
            return self.respond("""<button onclick="document.getElementById('choices').hidden=false">Apply</button>
            <div id="choices" role="dialog" hidden><a href="/auth">Apply Manually</a></div>""")
        fields = CONTACT + DOCUMENT
        if path == "/named-profile":
            fields += """<label>What is your current notice period?<input name="notice" required></label>
            <label>What is your desired annual salary (CAD)?<input name="salary" type="number" required></label>
            <label>Work History<textarea name="history" required></textarea></label>
            <label>Are you legally authorized to work in Canada?<select name="authorize" required><option value="">Choose</option><option value="authorize_canada_yes">Yes</option><option value="authorize_canada_no">No</option></select></label>
            <label>I consent to processing my application data<input name="consent" type="checkbox" required></label>"""
        if path == "/agent-pipeline":
            fields = fields.replace("Email*", "Reply destination*")
            fields += '<label>Applicant identity<input name="identity" required></label><label>Describe your technical strengths<textarea name="essay" required></textarea></label><label>Portfolio commentary (optional)<textarea name="commentary"></textarea></label>'
        if path == "/agent-required":
            fields += '<div class="form-field"><label for="license">Professional license identifier</label><p>You must provide this identifier to process your application.</p><input id="license"></div>'
        if path == "/screening":
            fields += """<label>Are you legally authorized to work in the United States?<select name="authorized" required><option value="">Choose</option><option value="yes">Yes</option><option value="no">No</option></select></label>
            <label>What is your current visa type?<select name="visa" required><option value="">Choose</option><option value="tn">TN (NAFTA)</option><option value="h1b">H-1B</option></select></label>
            <label>Will you now or in the future require visa sponsorship?<select name="sponsorship" required><option value="">Choose</option><option value="yes">Yes</option><option value="no">No</option></select></label>
            <fieldset><legend>Veteran status *</legend><label>I am a protected veteran<input type="radio" name="veteran" value="protected" required></label><label>I am not a protected veteran<input type="radio" name="veteran" value="not-protected" required></label><label>I don't wish to answer<input type="radio" name="veteran" value="decline" required></label></fieldset>
            <label>Disability status<input name="disability" role="combobox" required onclick="document.getElementById('disability-options').hidden=false"></label>
            <div id="disability-options" role="listbox" hidden><div role="option" onclick="document.querySelector('[name=disability]').value=this.innerText;this.parentElement.hidden=true">No, I do not have a disability and have not had one in the past</div><div role="option">Yes, I have a disability</div><div role="option">I do not wish to answer</div></div>"""
        if path == "/mutating":
            fields += """<label>City<input name="city" onchange="document.querySelector('[name=first_name]').value='Wrong name'"></label>"""
        extras = ""
        if path == "/unknown":
            fields += '<label>What is your professional license number?*<input name="license" required></label>'
        if path == "/validation":
            extras = '<div role="alert">The application contains an invalid answer.</div>'
        if path == "/radio":
            fields += """<fieldset><legend>Do you require sponsorship?</legend>
            <label>Yes<input type="radio" name="sponsor" value="yes" required></label>
            <label>No<input type="radio" name="sponsor" value="no" required></label></fieldset>"""
        if path == "/controls":
            fields += """<label>Country*<select name="country" required><option value="">Choose</option><option value="CA">Canada</option><option value="US">United States</option></select></label>
            <label>I consent to processing my application data<input name="consent" type="checkbox" required></label>
            <label>City<input name="city" role="combobox" oninput="document.getElementById('options').hidden=false"></label>
            <div id="options" role="listbox" hidden><div role="option" onclick="document.querySelector('[name=city]').value='Toronto';this.parentElement.hidden=true">Toronto</div></div>"""
        if path == "/conditional":
            fields += """<label>Country*<select name="country" required onchange="document.getElementById('state').hidden=false"><option value="">Choose</option><option value="CA">Canada</option></select></label>
            <label id="state" hidden>Province*<input name="state" required></label>"""
        if path == "/shadow":
            fields = '<div id="host"></div>' + DOCUMENT
            extras = (
                '<script>document.getElementById("host").attachShadow({mode:"open"}).innerHTML='
                + repr(CONTACT)
                + ";</script>"
            )
        if path == "/captcha-form":
            fields += '<div class="g-recaptcha" data-sitekey="fixture-key"></div><textarea name="g-recaptcha-response" hidden></textarea>'
        if path == "/multi":
            fields = CONTACT
            return self.respond(
                '<form action="/stage-two">' + fields + "<button>Next</button></form>"
            )
        if path == "/stage-two":
            return self.respond(
                '<form action="/review">' + DOCUMENT + "<button>Review application</button></form>"
            )
        action = "/uncertain" if path == "/uncertain" else "/submit"
        return self.respond(
            '<h1>Apply for Software Engineer</h1><form method="post" enctype="multipart/form-data" action="'
            + action
            + '">'
            + fields
            + "<button>Submit application</button></form>"
            + extras
        )

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.submissions.append((self.path, body))
        if self.path == "/uncertain":
            return self.respond("<h1>Processing</h1><p>Please wait.</p>")
        return self.respond(SUCCESS)


def serve(port=8765):
    server = FixtureServer(("127.0.0.1", port))
    print(f"Local fixture application: http://127.0.0.1:{server.server_port}/single", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
