# wagecuck

One job URL + one profile -> a JSON result. Playwright navigates and fills the form. Optional model assistance resolves unfamiliar questions directly from the complete profile and the options found on the form.

## Install

From the `filler/` directory, with Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m playwright install chromium
```

## Run one application

Fill a job application and watch the browser:

```powershell
.venv\Scripts\wagecuck.exe run "JOB_APPLICATION_URL" --show-browser --output runs\application.json
```

Runs use the shared synthetic `../profiles/dummy/profile.json` unless `--profile` supplies another short profile name or JSON path. For example, `--profile actual` loads `../profiles/actual/profile.json`. The default mode fills supported steps, verifies the answers, stops before final submission, and closes the browser.

Optional resume autofill helpers are skipped. Required attachments are uploaded first; the app waits for upload/autofill activity to settle, rereads the form, then fills mapped answers from your profile and verifies them. A stored attachment is recognized even if the site replaces its file input. Processing waits are bounded; `UPLOAD_TIMEOUT` means the site did not settle. The offline corpus probe reports `UPLOAD_UNVERIFIED` when its network block prevents an upload from completing.

Filling runs in this order: **documents -> country selectors -> phone numbers -> remaining fields**. Country changes are allowed to settle and the form is rescanned for dependent fields. Phone entry tries national digits first, then the international form when validation rejects the first format; existing formatting is retained as a final fallback. The handlers use field metadata and widget state, without hostname-specific rules.

To fill everything and **wait for you to review and submit**:

```powershell
.venv\Scripts\wagecuck.exe run "JOB_APPLICATION_URL" --wait-for-user
```

With a configured model, add `--agent-fill` to infer unresolved answers from the profile. Unsupported invented answers are recorded as `made_up: true`. A profile value that is absent from a dropdown or selector goes to the model with the actual options. If inference cannot provide a usable choice, the fallback randomly selects an available option (or one option for a checkbox/radio group), excludes placeholders and disabled choices, and records the reason as made up.

```powershell
.venv\Scripts\wagecuck.exe run "JOB_APPLICATION_URL" --wait-for-user --agent-fill
```

| Option | Behavior |
|---|---|
| `--dry-run` | Run the live fill pipeline with a synthetic profile and stop before final submission. |
| `--mode fill` | Fill and verify; stop before final submission. This is the default. |
| `--mode submit` | Fill, verify, submit once, and check for confirmation. |
| `--wait-for-user` | Fill, open a visible browser, and wait for manual submission. Only works in fill mode. |
| `--show-browser` | Show Playwright's browser. Otherwise it runs headlessly. |
| `--slow-mo 250` | Add 250 ms between browser actions. |
| `--timeout 240` | Allow 240 seconds for automated work. Manual review has no time limit. |
| `--agent-fill` | Enable inferred and explicitly marked invented answers for unresolved questions. |
| `--output PATH` | Write the final fill or submission result JSON to an explicit path. |

For example, exercise the complete live workflow with the included fictional profile without
clicking the final submit control:

```powershell
.venv\Scripts\wagecuck.exe run "JOB_APPLICATION_URL" --profile dummy --dry-run
```

Dry-run and fill modes can upload documents and send data during intermediate steps. They keep
networking enabled so remote widgets and server validation behave normally. Dry-run requires a
synthetic profile. Synthetic profiles can submit or hand off for manual submission only on local
test pages.

## Profile and keys

`../profiles/dummy/profile.json` is the canonical synthetic testing profile used by the CLI and evaluation scripts. Real applicant details and résumés belong in the Git-ignored `../profiles/actual/` directory. Document paths are relative to the profile JSON. Pass `--profile actual` when using truthful applicant data.

The previous mixed-content file is preserved as `../profiles/actual/profile-draft.json`. Review its fictional details and save a verified `../profiles/actual/profile.json` before using `--profile actual` for live filling.

Generate or reset the shared dummy profile and résumé:

```powershell
.venv\Scripts\wagecuck.exe demo-profile
```

- `facts`: contact details and links.
- `address`, `employment`, `education`: structured address and history.
- `application`: availability, compensation, pronouns, and application preferences.
- `screening`: explicit work authorization, sponsorship, disability, veteran, and demographic declarations.
- `experience`, `consents`: skill experience and separately declared consent choices.

Dates use `YYYY-MM-DD`. `null` means unknown; `false` and `0` are valid answers. Composite fields such as full address and work history are derived automatically. `application.randomize_source` enables random choices for "How did you hear about us?".

Create `.env` from [.env.example](.env.example), or use the shared `.env` in the repository root, then set the keys you use:

```dotenv
OPENAI_API_KEY=your-openai-key
WAGECUCK_AGENT_PROVIDER=openai
CAPSOLVER_API_KEY=your-capsolver-key
```

The CLI loads `filler/.env` first and then the repository root `.env` when run from `filler/`; shell variables take precedence. `WAGECUCK_AGENT_MODEL` or `--agent-model` selects the model. Without a model, deterministic profile mappings still work. Agent-fill sends the complete resolved application profile to the configured model provider; answer logs record the supporting profile fields and whether an answer was inferred or made up. Credentials, API tokens, and document paths are excluded.

CapSolver is used for supported CAPTCHAs in submit mode. The adapter supports discoverable reCAPTCHA v2 and Turnstile widgets; missing credentials return `CAPTCHA_KEY_MISSING`. Manual review leaves CAPTCHA completion to you. hCaptcha, reCAPTCHA v3, and full-page challenges are not supported.

## Results

Each run prints JSON and saves `result.json`, `events.jsonl`, and field analysis under
`.artifacts/application/runs/<run_id>/`.

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
.venv\Scripts\wagecuck.exe run "http://127.0.0.1:8765/single" --profile dummy --mode submit
```

Run the training corpus without submitting applications:

```powershell
.venv\Scripts\python.exe scripts/run_all.py examples\jobs.json --mode dry-run --split training --pool --concurrency 4
```

For an ordinary batch dry run, pass one or more `job_search` CSV or JSON outputs directly:

```powershell
.venv\Scripts\python.exe scripts/run_all.py ..\job_search\results\RUN\04-selected.csv --mode dry-run --profile ..\profiles\dummy\profile.json --output runs\reports\dry-run.json --pool --concurrency 4
```

Use the same command for live fills or submissions by changing `--mode`. Live submission requires
a truthful, non-synthetic profile:

```powershell
.venv\Scripts\python.exe scripts/run_all.py ..\job_search\results\RUN\04-selected.csv --mode submit --profile ..\profiles\actual\profile.json --output runs\reports\submitted.json --agent-provider openai --agent-fill --pool --concurrency 2
```

`--mode dry-run` uses the same navigation, upload, dynamic-control, inference, and validation path
as a live fill. It stops when the application is ready for its final submit action. `--split` is
reserved for asserting the training and held-out validation corpora.

Training examples are in `examples/jobs.json`; held-out cases are in
`examples/validation-jobs.json`. The validation corpus is evaluated separately and must not be
used to tune field behavior.

Use `--pool --concurrency 4` to reuse up to four browser workers. Every job gets a fresh browser
context. Extra jobs wait in the queue. **The hard limit is 8 active jobs per evaluation**, with or
without pooling; larger values are rejected. Pooling is optional, and concurrency defaults to `1`.

Each job gets a fresh browser context, planner, and model client; cookies and profile state are not shared. Results stay in corpus order. Browsers close when the evaluation ends. Without `--pool`, each job launches its own browser, subject to the same concurrency limit.

Add `--agent-provider openai --agent-fill` for model assistance. Each evaluation writes uniquely named reports under `.artifacts/application/reports/` and prints the final path. Use `--output .artifacts/application/reports/my-evaluation.json` to choose a filename. Give separate invocations different output filenames. The corpus probe blocks browser networking before filling and does not click Next or Submit. A required-field pass means the discovered required questions are satisfied by verified values in the loaded form step; it does not prove server acceptance or later-step compatibility.

`--split validation` evaluates the held-out corpus and saves aggregate results only.

Account creation, OTP/MFA, automatic creation of repeated history rows, and arbitrary custom controls remain limitations. ATS rules help navigation and parsing; they do not guarantee every posting on an ATS will work.
