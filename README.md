# wagecuck

One job URL + one profile -> a JSON result. Playwright navigates and fills the form. Optional model assistance maps unfamiliar questions to profile fields, then drafts answers for anything still unresolved.

## Install

From the repository directory, with Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m playwright install chromium
```

## Run one application

Fill a job application and watch the browser:

```powershell
.venv\Scripts\wagecuck.exe run "JOB_APPLICATION_URL" --profile ryan --show-browser
```

`--profile ryan` loads `profiles/ryan/profile.json`. You can also pass a JSON file path. The default mode fills supported steps, verifies the answers, stops before final submission, and closes the browser.

To fill everything and **wait for you to review and submit**:

```powershell
.venv\Scripts\wagecuck.exe run "JOB_APPLICATION_URL" --profile ryan --wait-for-user
```

With a configured model, add `--agent-fill` to infer unresolved answers from the profile. Unsupported invented answers are recorded as `made_up: true`.

```powershell
.venv\Scripts\wagecuck.exe run "JOB_APPLICATION_URL" --profile ryan --wait-for-user --agent-fill
```

| Option | Behavior |
|---|---|
| `--dry-run` | Inspect the form without entering profile data or submitting. |
| `--mode fill` | Fill and verify; stop before final submission. This is the default. |
| `--mode submit` | Fill, verify, submit once, and check for confirmation. |
| `--wait-for-user` | Fill, open a visible browser, and wait for manual submission. Only works in fill mode. |
| `--show-browser` | Show Playwright's browser. Otherwise it runs headlessly. |
| `--slow-mo 250` | Add 250 ms between browser actions. |
| `--timeout 240` | Allow 240 seconds for automated work. Manual review has no time limit. |
| `--agent-fill` | Enable inferred and explicitly marked invented answers for unresolved questions. |

For example, inspect the included fictional profile without filling anything:

```powershell
.venv\Scripts\wagecuck.exe run "JOB_APPLICATION_URL" --profile demo --dry-run
```

Fill mode can upload documents and send data during intermediate steps. It is not network-isolated. Synthetic profiles can submit or hand off for manual submission only on local test pages.

## Profile and keys

Use [profiles/demo/profile.json](profiles/demo/profile.json) as the structure for your profile. Replace the fictional values and resume; set `synthetic: false` for a real applicant. Document paths are relative to the profile JSON.

- `facts`: contact details and links.
- `address`, `employment`, `education`: structured address and history.
- `application`: availability, compensation, pronouns, and application preferences.
- `screening`: explicit work authorization, sponsorship, disability, veteran, and demographic declarations.
- `experience`, `consents`: skill experience and separately declared consent choices.

Dates use `YYYY-MM-DD`. `null` means unknown; `false` and `0` are valid answers. Composite fields such as full address and work history are derived automatically. `application.randomize_source` enables random choices for "How did you hear about us?".

Create `.env` from [.env.example](.env.example), then set the keys you use:

```dotenv
OPENAI_API_KEY=your-openai-key
WAGECUCK_AGENT_PROVIDER=openai
CAPSOLVER_API_KEY=your-capsolver-key
```

The CLI loads `.env` from the working directory; shell variables take precedence. `WAGECUCK_AGENT_MODEL` or `--agent-model` selects the model. Without a model, deterministic profile mappings still work. Agent-fill sends resolved profile facts to the configured model provider; answer logs record their source and whether an answer was inferred or made up.

CapSolver is used for supported CAPTCHAs in submit mode. The adapter supports discoverable reCAPTCHA v2 and Turnstile widgets; missing credentials return `CAPTCHA_KEY_MISSING`. Manual review leaves CAPTCHA completion to you. hCaptcha, reCAPTCHA v3, and full-page challenges are not supported.

## Results

Each run prints JSON and saves `result.json`, `events.jsonl`, and field analysis under `runs/<run_id>/`.

- `succeeded`: the page confirmed receipt; `success: true`.
- `ready` / `inspected`: filling or inspection completed; nothing was submitted automatically.
- `failed`: check `code` and `unresolved` for the reason.
- `unknown`: submission or manual handoff occurred without a confirmed receipt. Check the employer portal before retrying.

`--sensitive-artifacts` also saves screenshots and a Playwright trace. Generated outputs and `.env` are ignored by Git.

## Test

Run the local regression suite:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src tests scripts
```

To try a complete local submission, start the test server in one terminal:

```powershell
.venv\Scripts\wagecuck.exe demo-server
```

Then run:

```powershell
.venv\Scripts\wagecuck.exe run "http://127.0.0.1:8765/single" --profile demo --mode submit
```

Run the training corpus without submitting applications:

```powershell
.venv\Scripts\python.exe scripts/dry_run_all.py --split training --pool --concurrency 4
```

Training examples are in `examples/jobs.json`; held-out cases are in
`examples/validation-jobs.json`. The validation corpus is evaluated separately and must not be
used to tune field behavior.

Use `--pool --concurrency 4` to reuse up to four browser workers during both navigation and form probing. Extra jobs wait in the queue. **The hard limit is 8 active jobs per evaluation**, with or without pooling; larger values are rejected. Pooling is optional, and concurrency defaults to `1`.

Each job gets a fresh browser context, planner, and model client; cookies and profile state are not shared. Results stay in corpus order. Browsers close when the evaluation ends. Without `--pool`, each job launches its own browser, subject to the same concurrency limit.

Add `--agent-provider openai --agent-fill` for model assistance. Each evaluation writes uniquely named reports under `runs/reports/` and prints the final path. Use `--output runs/reports/my-evaluation.json` to choose a filename. Give separate invocations different output filenames. The corpus probe blocks browser networking before filling and does not click Next or Submit. A required-field pass means the discovered required questions are satisfied by verified values in the loaded form step; it does not prove server acceptance or later-step compatibility.

`--split validation` evaluates the held-out corpus and saves aggregate results only.

Account creation, OTP/MFA, automatic creation of repeated history rows, and arbitrary custom controls remain limitations. ATS rules help navigation and parsing; they do not guarantee every posting on an ATS will work.
