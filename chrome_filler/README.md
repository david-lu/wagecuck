# AI Input Filler

The popup and the small Wagecuck panel at the bottom right of application pages offer **SCAN PAGE** then **FILL**. The panel shows the number of fillable inputs after scanning, including inputs in available frames. Scanning reveals purple outlines on supported form fields; hover or focus a field to show its yellow Write button. Resume/CV PDF inputs get an **Attach PDF** button. FILL attaches the saved PDF to empty resume inputs and drafts answers for unanswered fields across available frames in one agent request, including factual checkboxes, native dropdowns, and supported custom dropdowns. Review the results before submitting; the extension never submits the form.

## Install in Chrome

1. Open `chrome://extensions`, enable **Developer mode**, and use **Load unpacked** to select `chrome_filler`.
2. Pin the extension and open its popup. Choose **Edit profile** to add your resume, name, contact details, location, work eligibility, veteran status, free-form Notes, and writing preferences. Use **Save profile**; edits also save automatically.
3. Open **AI settings** to enter your OpenAI API key. Choose GPT-6.1 Sol (balanced), GPT-6 Astra (highest capability), GPT-6 Luna (lowest cost), a GPT-4.1 option, or **Other model ID** for an existing model, then click **Save settings**.
4. Reload an already-open application page. In the popup, click **SCAN PAGE**, then **FILL**. You can also click individual Write buttons beside highlighted fields. The page button switches to **Stop** while filling. Review each answer before submitting.

No build step or backend is needed. The extension has its own settings and does not read wagecuck’s `.env`. Importing a shared profile copies its content into Chrome storage; re-import it after editing the file. API usage is billed to your OpenAI API account. Existing settings keep their chosen model, and new installs still start on `gpt-4.1-mini` until you choose another. Custom model IDs must support the Responses API, structured output, and PDF input if using a PDF résumé.

Individual **Write** clicks create one API request per field; **FILL** creates one request for the scanned unanswered fields using the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/text). It does not open or automate the ChatGPT website.

## What it handles

- Visible, editable text, email, phone, URL, and number inputs; textareas; and contenteditable fields, including fields added after page load and fields in open shadow roots.
- Native single-select dropdowns, Greenhouse React Select dropdowns with visible choices, Ashby Yes/No buttons, grouped radio and checkbox questions, and Ashby location autocomplete. The agent sees each available fixed dropdown choice and returns an exact listed value. For Ashby location autocomplete, the extension types the answer and clicks it only when an exact suggestion appears. A false answer leaves an ordinary unchecked box unchanged. Already selected choices are preserved by FILL. Other search-driven dropdowns without reliable suggestions, such as Greenhouse's Location (City), remain manual.
- Paragraph questions such as “Why do you want to work here at Northstar?” with company and role details from the page and experience from your profile.
- Explicit and nested labels, multiple accessibility labels, floating labels, nearby questions, fieldset legends, grouped headings, table headers, and definition lists. Placeholder, title, and readable field identifiers provide fallbacks. Help text and writing limits are included as context while neighboring fields' values are excluded.
- The complete rendered page text, including offscreen sections, a field inventory, and snapshots from available same-origin and cross-origin frames. Hidden markup, scripts, existing form values, and extension controls are excluded. Only the target field’s existing value is included. URL query strings and fragments are omitted.
- Each field's button shows when you hover or focus it. While AI is working, the button shows progress and can be clicked to stop. After filling, it briefly offers **Undo** where supported. FILL shows **Stop** while it runs. Toasts appear only for errors.
- Preservation of edits made while generation is running. Undo also preserves edits made after filling.
- Native input and change events for framework forms. A field's **Write** button fills that field; **FILL** fills all supported empty fields from one response. Neither clicks Next or Submit.

Password, search, payment, identity-number, verification-code, disabled, and read-only fields are excluded. Consent and agreement choices, multi-select lists, and unrecognized custom dropdowns remain manual. FILL uses one request for supported unanswered controls. Its structured output schema names every scanned field and includes that field's label, type, and applicable format, length, or choice constraints. Each returned value is validated again before insertion. For open-ended questions without a direct profile answer, the model drafts a short, plausible answer based on the profile and job posting; review invented details before use. It does not invent identity, contact details, education, licenses, employment dates, work location, work authorization, sponsorship needs, referrals, consent, or demographic facts. Missing essential facts may still need to be entered manually.

## Résumé and data

PDF files are limited to 5 MB, can be attached to detected resume/CV fields, including Greenhouse forms with hidden file inputs and visible Attach buttons, and are included in AI context using [OpenAI file inputs](https://developers.openai.com/api/docs/guides/file-inputs) when generating answers. Attaching the PDF alone does not call OpenAI. Text files can be up to 1 MB and provide AI context only; extracted text must fit the editor’s limit. DOCX is not supported: export it to PDF or paste the text. Some websites may block programmatic file attachment; if the extension reports that the site rejected the PDF, upload it manually.

Settings, profile, and résumé are stored in `chrome.storage.local`, with access restricted to trusted extension pages. Your API key stays in the extension’s background worker and popup; content scripts do not receive it. Local storage is **not encrypted**. The extension sends page context and saved applicant data to OpenAI only when you click a field's AI button or **FILL**. Requests use `store: false`; this does not disable OpenAI’s separate [API abuse-monitoring retention](https://developers.openai.com/api/docs/guides/your-data).

Page context is not silently shortened. Oversized context is rejected before the API call. Inaccessible frames are explicitly noted in the model context. Closed shadow roots, browser-internal pages, canvas editors, and custom rich-text editors that reject native updates are limitations. Existing tabs need a reload after installing or reloading the extension. Chrome may require granting this extension access to a site.

## Diagnostics

Open **AI settings > Diagnostics > Troubleshooting** to refresh, copy, or clear the last 100 operation records. Logs contain request IDs, state outcomes, timings, field types, character counts, and error codes. They exclude keys, profile and resume contents, page text and URLs, field labels, and generated answers. Detailed diagnostics also enable console output in the extension service worker, accessible from `chrome://extensions`.

Generation has a bounded timeout and no automatic retries. Use the toast’s retry button after fixing your key, billing, model access, or missing profile information. The extension’s on/off switch immediately removes page buttons and cancels running sessions. Model selection lives in AI settings.

## Test

From this folder, with Node.js 20 or newer:

```powershell
npm test
npm run check
```

From the wagecuck repository root, use the existing Python Playwright installation for the real unpacked-extension UI tests:

```powershell
filler\.venv\Scripts\python.exe chrome_filler\tests\browser_test.py
```

The browser tests launch an isolated Chromium profile, serve synthetic application pages, and replace the worker’s API transport with deterministic streaming responses. They make no paid API calls and do not read `.env`. Screenshots and the test report are written to `chrome_filler/.artifacts/`. They exercise the real manifest, content script, background worker, popup, storage, and frame messaging.

To try the synthetic page manually:

```powershell
filler\.venv\Scripts\python.exe -m http.server 8787 --bind 127.0.0.1 --directory chrome_filler/tests/fixtures
```

Open `http://127.0.0.1:8787/application.html` in Chrome. Clicking AI manually uses your configured API key and makes a real API request.
