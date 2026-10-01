# AI Input Filler

A standalone Chrome extension inside wagecuck. Click **Scan page** to reveal purple outlines and **✦ Write** buttons beside eligible fields (**✦ AI** on small fields). Click a field button to draft and insert an answer using your profile, résumé, writing preferences, and the entire rendered page. Fields with existing text show **Rewrite**. Scanning does not call the AI; navigation clears the scan until you scan the new page.

## Install in Chrome

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and select this `chrome_filler` folder.
3. Pin **Wagecuck · AI Input Filler** from Chrome’s extensions menu.
4. Click its icon and follow the setup banner: **Connect AI** with your OpenAI API key, then add your résumé or background in **About you**. You can upload PDF, TXT, or Markdown, paste résumé text, or import the shared `../profiles/actual/profile.json` after you have completed it.
5. In **Your voice**, choose a tone or write your preferences. New settings default to a casual, first-person voice. For example: “Keep it conversational and specific. Mention my interest in accessible products. Avoid buzzwords.” Tone changes keep your other instructions. Edits save automatically; the footer shows saving or saved status, and **Save** is available when you need it.
6. Reload an already-open application page. Click **Scan page** on the page or in the extension popup to reveal purple outlines and **✦ Write** buttons on supported fields. Review each result before submitting.

No build step or backend is needed. The extension has its own settings and does not read wagecuck’s `.env`. Importing a shared profile copies its content into Chrome storage; re-import it after editing the file. API usage is billed to your OpenAI API account. The default model is `gpt-4.1-mini`; you can enter another model ID that supports the Responses API, structured output, and PDF input if using a PDF résumé.

This creates a fresh API generation session for each field using the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/text). It does not open or automate the ChatGPT website.

## What it handles

- Visible, editable text, email, phone, URL, and number inputs; textareas; and contenteditable fields, including fields added after page load and fields in open shadow roots.
- Paragraph questions such as “Why do you want to work here at Northstar?” with company and role details from the page and experience from your profile.
- Explicit and nested labels, multiple accessibility labels, floating labels, nearby questions, fieldset legends, grouped headings, table headers, and definition lists. Placeholder, title, and readable field identifiers provide fallbacks. Help text and writing limits are included as context while neighboring fields' values are excluded.
- The complete rendered page text, including offscreen sections, a field inventory, and snapshots from available same-origin and cross-origin frames. Hidden markup, scripts, existing form values, and extension controls are excluded. Only the target field’s existing value is included. URL query strings and fragments are omitted.
- A persistent toast that explains when AI is thinking or writing, with elapsed time and **Cancel**, followed by success with **Undo** or an actionable error with retry. Notifications appear on the left to leave the fields' Write buttons accessible. Cancelling leaves the field unchanged.
- Preservation of edits made while generation is running. Undo also preserves edits made after filling.
- Native input and change events for framework forms. It fills one field per click and never clicks Next or Submit.

Password, search, payment, identity-number, verification-code, hidden, disabled, and read-only fields are excluded. The model uses provided personal facts by default. If the saved profile or writing instructions explicitly permit it, it can draft invented details for open-ended answers; review those details before use. It does not invent identity, contact details, education, licenses, employment dates, work location, work authorization, sponsorship needs, referrals, or consent.

## Résumé and data

PDF files are limited to 5 MB and included directly using [OpenAI file inputs](https://developers.openai.com/api/docs/guides/file-inputs). Text files can be up to 1 MB; extracted text must fit the editor’s limit. DOCX is not supported: export it to PDF or paste the text.

Settings, profile, and résumé are stored in `chrome.storage.local`, with access restricted to trusted extension pages. Your API key stays in the extension’s background worker and popup; content scripts do not receive it. Local storage is **not encrypted**. The extension sends page context and saved applicant data to OpenAI only when you click a field’s AI button. Requests use `store: false`; this does not disable OpenAI’s separate [API abuse-monitoring retention](https://developers.openai.com/api/docs/guides/your-data).

Page context is not silently shortened. Oversized context is rejected before the API call. Inaccessible frames are explicitly noted in the model context. Closed shadow roots, browser-internal pages, canvas editors, and custom rich-text editors that reject native updates are limitations. Existing tabs need a reload after installing or reloading the extension. Chrome may require granting this extension access to a site.

## Diagnostics

Open **Connect AI → Advanced settings → Troubleshooting** to refresh, copy, or clear the last 100 operation records. Logs contain request IDs, state outcomes, timings, field types, character counts, and error codes. They exclude keys, profile/résumé contents, page text and URLs, field labels, and generated answers. Detailed diagnostics also enable console output in the extension service worker, accessible from `chrome://extensions`.

Generation has a bounded timeout and no automatic retries. Use the toast’s retry button after fixing your key, billing, model access, or missing profile information. The extension’s on/off switch immediately removes page buttons and cancels running sessions. Model selection lives in Advanced settings.

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
