# wagecuck

A job application runner: **one URL + one profile → one structured outcome**. Python owns the workflow. Playwright owns navigation, page inspection, uploads and form actions. A bounded agent can map unfamiliar fields to profile facts and infer remaining answers from the profile and create explicitly marked invented answers; it never controls the browser.

Implemented and tested locally with Chromium. Includes ATS rules for Lever, Greenhouse, Ashby and Workday entry/navigation, a generic labeled-form fallback, CapSolver integration, a synthetic profile, a real résumé PDF, and local application portals. This is an initial supported-control implementation, not universal ATS coverage.

## Install

Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m playwright install chromium
```

If Windows fails during `venv`'s pip bootstrap, use `python -m pip --python .venv\Scripts\python.exe install -e ".[dev]"`. For the exact dependency versions used during development, install `requirements.lock` first, then `-e . --no-deps`.

## Run a complete local application

The generated [profile](profiles/demo/profile.json) and [résumé PDF](profiles/demo/alex-morgan-resume.pdf) are intentionally fictional. Regenerate them with:

```powershell
.venv\Scripts\python.exe -m wagecuck demo-profile
.venv\Scripts\python.exe -m wagecuck demo-server
```

In another terminal:

```powershell
.venv\Scripts\python.exe -m wagecuck run http://127.0.0.1:8765/single --profile profiles/demo/profile.json --mode submit
```

This actually uploads the PDF and submits to the local fixture server. A second submission of the same URL/profile is blocked by the SQLite journal. Try `/multi`, `/iframe`, `/controls`, `/radio`, `/conditional`, `/shadow`, `/unknown`, `/auth`, `/closed`, and `/uncertain` for different outcomes. `/screening` exercises eligibility and demographic controls; its required sponsorship question remains unresolved until the profile supplies an explicit answer.

## Run against a job link

```powershell
.venv\Scripts\python.exe -m wagecuck run "JOB_APPLICATION_URL" --profile profiles/demo/profile.json --mode inspect
.venv\Scripts\python.exe -m wagecuck run "JOB_APPLICATION_URL" --profile profiles/private/me.json --mode fill
.venv\Scripts\python.exe -m wagecuck run "JOB_APPLICATION_URL" --profile profiles/private/me.json --mode submit
```

- `inspect`: navigate to the form and record field metadata without entering applicant data.
- `fill` (default): fill and validate, stopping before final submission. Intermediate steps and uploads may send data to the employer. This is not a network-isolated dry run.
- `submit`: fill, validate, submit once and require a new receipt message. Synthetic profiles can only submit locally.

### Watch the browser run

Use `--show-browser` (or `--headed`) to open a visible Chromium window. Add `--slow-mo 250` to slow Playwright operations so you can follow the navigation and form filling:

```powershell
.venv\Scripts\python.exe -m wagecuck run "JOB_APPLICATION_URL" --profile profiles/private/me.json --mode fill --show-browser --slow-mo 250
```

Omit `--show-browser`, or pass `--headless`, to run without a window. Visibility works with inspect, fill and submit modes and does not change whether submission is enabled. From Python, use `RunOptions(headless=False, slow_mo_ms=250)`.

Use `--timeout 240` for slow sites/CAPTCHA tasks or deliberately slowed runs, and `--storage-state path.json` for an existing Playwright login session. By default the browser closes when the run ends.

### Fill and wait for you to submit

```powershell
.venv\Scripts\python.exe -m wagecuck run "JOB_APPLICATION_URL" --profile profiles/private/me.json --wait-for-user
```

`--wait-for-user` fills and validates all supported steps, then keeps a visible browser open on the final application page. Review or edit the answers, complete any CAPTCHA, and click Submit yourself. Wagecuck performs no further form actions after handing over and watches for a confirmation. It closes the browser and returns success after observing receipt. Closing the browser without a detected receipt returns `USER_SUBMISSION_UNCONFIRMED`, with no automatic retry.

The automated filling stage still uses `--timeout`; there is **no time limit on your review**. This option enables a visible browser automatically and works only in `fill` mode (the default); it cannot be combined with `--headless`, `--mode inspect`, or `--mode submit`. It does not run CapSolver callbacks at handoff because those can submit a form. From Python, use `RunOptions(wait_for_user=True)`. Demo profiles can hand off on the local test portal.

The real profile has the same schema as the demo, `synthetic: false`, your own facts/documents and named declarations. Document paths are relative to the profile JSON. A `false` value is a real answer; `null` means unknown. The demo contains fictional availability, compensation, experience and placeholder social links; replace these with your own data. Without `--agent-fill`, missing declarations remain unresolved. With `--agent-fill`, unsupported answers can be invented and are marked `made_up: true` in the run log.

## CapSolver

```powershell
$env:CAPSOLVER_API_KEY = "your-key"
.venv\Scripts\python.exe -m wagecuck run "JOB_APPLICATION_URL" --profile profiles/private/me.json --mode submit --timeout 240
```

Implemented: reCAPTCHA v2 (including enterprise/invisible parameters detected in anchor URLs) and Turnstile widgets with discoverable site keys. The Python client calls `createTask`, polls `getTaskResult` with bounded time/attempts, then delivers a token through Playwright to the response field or an explicitly named callback. The API key never enters the browser, prompt or run artifacts. A token is not proof of application acceptance.

Solving occurs in submit mode. Unsupported widget integrations return a specific failure. reCAPTCHA v3 execute interception, hCaptcha, Arkose and Cloudflare full-page challenges are not implemented. CapSolver's request/poll/delivery paths are covered by mock-service tests; no paid live solve has been performed without credentials. `.env.example` documents variables; `.env` is not auto-loaded.

Without a key, ordinary navigation/filling still works. A supported CAPTCHA in submit mode returns `CAPTCHA_KEY_MISSING` before any provider request or final submission. Set the variable in the same PowerShell session before starting the runner; an existing Python `ApplicationRunner` reads the key when it is constructed. In `--wait-for-user` mode, CAPTCHA completion remains manual. The live corpus also contains hCaptcha, which this adapter detects but does not solve; adding a key alone does not remove that limitation.

## Profile structure and helpers

The [demo profile](profiles/demo/profile.json) groups address, employment and education separately. `facts` holds contact details and links; `application`, `experience`, `consents` and `screening` hold named declarations. Employment and education are ordered arrays, so multiple entries can be represented:

```json
"employment": [
  {
    "company": "Example Systems (fictional)",
    "title": "Software Engineer",
    "start_date": "2023-06-01",
    "end_date": null,
    "current": true,
    "location": "Toronto, Ontario, Canada",
    "summary": "Built Python services and automated browser workflows."
  }
]
```

Education entries use `school`, `degree`, `field`, `start_date`, `end_date`, `current`, `location` and `summary`. Dates use `YYYY-MM-DD`; unknown dates are `null`. Set `current: true` explicitly for an ongoing entry and leave its end date `null`. Reversed date ranges and current entries with end dates are rejected.

`address` contains `line1`, `line2`, `city`, `state`, `postal_code` and `country`. Python helpers resolve these components without requiring the agent to format them:

```python
profile.get_full_name()
profile.get_address()                         # Full address on one line
profile.get_address(multiline=True)           # Postal address with line breaks
profile.get_address(include_country=False)
profile.get_street_address()                  # Lines 1 and 2 only
profile.get_address_parts().postal_code
profile.get_current_employment()              # Entry, or None if absent/ambiguous
profile.values()                             # Available atomic and composite values
```

Both the deterministic parser and agent use the same resolved values. Agent choices include `full_name`, `full_address`, `full_address_multiline`, `street_address`, `location`, address components such as `address.line1`, and history fields such as `employment.0.company` or `education.0.degree`. The agent also receives descriptions of composite options, without their values. Empty strings and unknown dates are omitted from choices; `false` remains a valid value. Current-company/title shortcuts are available only when exactly one structured employment entry is marked current.

Existing flat profiles remain readable, including indexed history keys in `facts`. Structured sections take precedence over overlapping flat fields. The legacy `address` mapping key means the first street line; use `full_address` for the composite. The parser distinguishes full-address, street-address and individual address-line labels. Automatic creation of repeated employment/education rows on employer forms remains unimplemented.

### Named answers instead of question strings

The demo no longer has an `answers` dictionary keyed by employer questions. Normal assignment selects a profile field, then renders its value into the actual input/choice. Different phrasings can select the same field:

| Profile field / option | Used for |
|---|---|
| `screening.work_authorization.CA.authorized` / `authorized_canada` | Explicit Canadian work authorization |
| `screening.work_authorization.US.authorized` / `authorized_us` | Explicit U.S. work authorization |
| `screening.work_authorization.CA.requires_sponsorship` | Canadian sponsorship requirement, if declared |
| `application.available_start_date`, `application.notice_period_days` | Start date and notice period |
| `availability`, `notice_period` | Formatted availability/notice composites |
| `application.compensation.annual_target`, `compensation_expectations` | Annual numeric target or formatted pay expectations with currency |
| `application.personal_summary`, `application.headline`, `work_history` | Summary, headline and combined employment history |
| `application.preferred_name`, `application.pronouns` | Explicit name/pronoun preferences |
| `application.ai_usage`, `application.role_interest`, `application.cover_letter_text` | Supplied narratives; company-specific interest is not inferred from generic role interest |
| `experience.python.years`, `experience.golang.years` | Explicit skill-specific years; never calculated from job tenure |
| `education.0.result`, `education.0.expected_graduation_date` | Declared grade/result and anticipated graduation |
| `consents.application_processing`, `consents.future_opportunities`, `consents.sms` | Separately declared processing, future-contact and SMS choices |

`authorized_canada` is a computed alias, not another value to maintain: edit `screening.work_authorization.CA.authorized` once. Both deterministic rules and the optional mapper use these named values. The mapper may select an explicit screening declaration, but guards reject substitutions across countries, currencies and meanings. Expected compensation does not answer current salary; processing consent does not assert that a privacy policy was read. Unknown or empty fields are omitted from selectable values. A declared `false` or `0` remains usable.

Legacy `answers` question-to-value overrides are still accepted for old profiles and exceptional exact questions. They are no longer the default profile format, and the demo does not use them. Wording rules in code identify meanings; applicant answers live in profile data.

For “How did you hear about …?”, `application.randomize_source: true` selects a random available option from dropdowns, radio groups and common comboboxes. No source is preferred. Disabled options and placeholders are excluded. Free-text source fields receive a random common channel. The demo enables this at the user's request; set it to `false` to use `facts.source` instead. Actions are logged as `random:source` and shown as `random_choice` in field analysis. Follow-up questions, such as a referrer's name, still need their own answer.

## Optional agent

The CLI and dry-run scripts load `.env` from the working directory. Existing shell variables
take precedence. Copy `.env.example` to `.env` and set `OPENAI_API_KEY` locally. A configured
OpenAI key enables the hosted fallback by default; `--agent-provider openai` selects it explicitly.
The default hosted model is `gpt-5.6-terra`, configurable through `--agent-model` or
`WAGECUCK_AGENT_MODEL`. Requests use the Responses API with strict JSON schemas and `store=false`.

```powershell
.venv\Scripts\python.exe scripts/dry_run_all.py --agent-provider openai --agent-fill --output docs/dry-run-openai.json
```

Navigation is deterministic. The subsequent offline DOM probe runs the configured model for
requirement assessment, unresolved profile-field mapping, and (with `--agent-fill`) profile
inference followed by explicitly marked invented answers. Browser networking is blocked before filling; Python model requests remain available.
The JSON report records provider, model, attempted call counts per case, field routes and
profile source keys. Model failures produce `AGENT_FAILED`, never silently disable the fallback.
`--agent-timeout` bounds each request (60 seconds by default); the probe allows additional time
for the three model stages. No application is submitted.

To re-probe already inspected URLs without repeating the navigation pass:

```powershell
.venv\Scripts\python.exe scripts/probe_live.py --reports docs/navigation-1.json docs/navigation-2.json --agent-provider openai --agent-fill --output docs/dry-run-openai.json
```

Or use an already installed local Ollama model:

```powershell
.venv\Scripts\python.exe -m wagecuck run "JOB_APPLICATION_URL" --profile profiles/private/me.json --agent-provider ollama --agent-model "YOUR_INSTALLED_MODEL"
```

Every discovered editable application field is considered, including optional fields. The flow is:

1. Parse native/ARIA labels, question groups, adjacent help text, input types and available options. Match named profile fields, declarations, documents and deterministic contact aliases first; legacy exact-question overrides remain supported.
2. With a configured model, assess unclear requiredness using quoted page evidence and map unresolved fields by choosing from an enumerated list of profile field names. A mapping can select one fact or combine several text facts. Unknown keys, unsupported combinations and invalid evidence are rejected; mappings below 95% confidence are skipped. The model cannot override native required constraints.
3. With **`--agent-fill`**, send every remaining supported input to the model with the full raw and derived profile facts. It first infers an answer from that information; when unsupported, it supplies a plausible invented answer marked **`made_up: true`**. This covers text, numeric/date fields, dropdowns, checkboxes and radio groups, including missing personal facts. File paths and credentials are not invented. Explicit supplied facts should not be contradicted. Invalid mapping responses fall through to this stage and are logged as warnings.

```powershell
.venv\Scripts\python.exe -m wagecuck run "JOB_APPLICATION_URL" --profile profiles/private/me.json --agent-provider openai --agent-fill --wait-for-user
```

Mapping and requirement assessment send field metadata (including nearby page text) and allowed fact **names** to the configured model endpoint. Agent-fill sends the full resolved profile fact **values**, including contact, address, employment, education, screening and consent declarations. Credential keys and document paths are excluded. It does not send résumé PDF bytes. Browser-rendered text can itself contain personal information. Answers record `answer_basis` (`profile`, `inferred`, or `made_up`), `made_up`, source keys and a reason. Unsupported numbers are allowed in explicitly invented answers. These labels rely on model classification with additional structural checks; they do not prove semantic correctness. Generated answers do not modify the profile JSON. Use the review option above to inspect answers before submitting.

`result.json` includes an `answer_log` with the field, provenance and fill status, including failures. `events.jsonl` and dry-run field outcomes also include `made_up`. `analysis-NN.json` records every discovered field's label, type, `required`/`optional`/`unknown` status, requirement evidence, chosen route and source keys. Unknown means the page did not provide conclusive evidence. Hidden, disabled, read-only and search controls are excluded; conditional fields are reconsidered when revealed. `inspect` produces this report without entering values or generating prose. No model is needed for deterministic mappings. Agent transport and decisions are tested with controlled responses. Real OpenAI corpus results are recorded in [the model-backed dry-run report](docs/dry-run-openai.md); a local Ollama model has not yet been evaluated in this environment.

### Explicit screening declarations

The demo profile now declares U.S. work authorization, TN visa, no veteran status and no disability/history. These are separate, configurable facts:

```json
"screening": {
  "default_work_country": "US",
  "work_authorization": {
    "US": {"authorized": true, "visa_type": "TN", "requires_sponsorship": null}
  },
  "veteran_status": "not_a_veteran",
  "disability_status": "no_disability",
  "disability_history": false
}
```

Sponsorship remains `null` until explicitly set to `true` or `false`; the app does not derive it from TN status. Country-specific declarations do not answer questions about other jurisdictions. The screening mapper handles native dropdowns, radio groups, common comboboxes and text questions with known equivalent wording. Disability history is separate so a declaration about current disability alone does not answer combined current/past questions.

## Python API

```python
import asyncio
from pathlib import Path
from wagecuck import ApplicationRunner, Profile, RunOptions

result = asyncio.run(ApplicationRunner().run(
    "https://employer.example/job/123",
    Profile.load(Path("profiles/private/me.json")),
    RunOptions(mode="submit"),
))
print(result.model_dump_json(indent=2))
```

`success` means confirmed submission only. `ready` and `inspected` have `success: false` because no application was submitted. `unknown / SUBMISSION_UNCONFIRMED` means the final action may have reached the employer; it is deliberately distinct from a retryable failure. See [result semantics and error codes](docs/architecture.md).

Each run saves `result.json`, an event journal, normalized step snapshots and per-field analysis under `runs/<run_id>/`. Default snapshots omit entered values, adjacent context and full page text. `--sensitive-artifacts` also saves a screenshot and trace; these contain applicant data and should be kept private.

## Verification and research

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src tests scripts
.venv\Scripts\python.exe scripts/inspect_live.py
```

The local browser suite submits only to the loopback fixture server and checks actual HTTP submissions/PDF bytes. Live inspection uses the [researched posting corpus](examples/live-jobs.json), writes [live results](docs/live-report.json), and never enters applicant data or submits. Listings can close between search and browser inspection.

The [expanded corpus](examples/expanded-jobs.json) adds 20 URLs spanning nine more ATS families. To repeat the entire 31-URL diagnostic:

```powershell
.venv\Scripts\python.exe scripts/dry_run_all.py
```

This navigates every URL, records closed/auth/access failures, then exercises the real field planner and executor on reachable forms with networking disabled before entering the fictional profile. It blocks service workers and WebSockets and never clicks Next or Submit after filling. See the [per-application report](docs/dry-run-all.md) and [field-level results](docs/dry-run-all.json). Offline checks cannot prove server validation, uploaded-document acceptance, network-backed autocomplete, later application pages, or CAPTCHA acceptance. No universal ATS support is claimed.

The [unmapped-field review](docs/unmapped-field-audit.md) audits all 173 previously unmapped controls (105 distinct question/type/requirement groups). With the updated profile, 54 controls have a deterministic plan; the report also lists missing declarations and unresolved choices/questions. Reproduce it with `.venv\Scripts\python.exe scripts/audit_unmapped.py`. This uses saved metadata only and does not constitute a new live browser test. The original dry-run results are preserved.

Field parsing follows ARIA references (including open shadow roots), native labels, scoped question/group headings, and input metadata. Mapping normalizes required markers and common contact-field aliases; legal and employer-specific answers must exist in the profile. Radio selection uses the parent question, React Select verification checks the rendered selected value, and telephone verification accounts for a separately displayed dial code. Rules identify a host family; an empty selector rule is not a claim of complete support for that family.

Read the [source/behavior research](docs/research.md) and [architecture](docs/architecture.md). Authentication/account creation, OTP/MFA, automatic repeated history rows, closed shadow roots, and arbitrary custom controls remain explicit limitations. Workday rules currently cover entry/navigation and basic field selectors, not a complete Workday account/application lifecycle.

The service is shaped for a future batch queue: run-scoped browser contexts/artifacts, stable profile/job keys, durable claims, bounded workflows and machine-readable retry guidance. A production distributed queue and cross-machine deduplication are not included.
