# Research log — 2026-09-16

## User's architecture discussion

The [shared discussion](https://chatgpt.com/share/6aaa105c-a7d8-83e8-a693-b5234d8641b5) initially failed in the search tool but was recovered from its embedded conversation data. Its relevant direction is: normalize live forms, maintain ATS rules, execute narrow Playwright actions, verify values, use AI for unfamiliar semantics, and keep one run per application. This implementation follows that direction. Research-cache material stays local and ignored by git.

## ApplyPilot: source inspection

Inspected upstream commit `4a8d521f67f5139811c0a910ef37410f8e6d836a` in [Pickle-Pixel/ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot). This is the open-source project, not unrelated products with the same name.

Its launcher claims SQLite jobs transactionally, provisions per-worker Chrome/Playwright MCP configuration, starts a Claude Code process, and interprets its output. The application browser sequence is largely delegated through the prompt. That informs wagecuck's run isolation, durable claims and distinct outcomes. Here, browser progression and success checks are executable Python rather than instructions entrusted to an unconstrained browser agent. [Launcher source](https://github.com/Pickle-Pixel/ApplyPilot/blob/4a8d521f67f5139811c0a910ef37410f8e6d836a/src/applypilot/apply/launcher.py).

The prompt builder combines profile, documents, eligibility and application instructions. It also describes provider requests and browser-side CAPTCHA token delivery. Wagecuck moves that transport into a dedicated CapSolver client, keeps the API key out of browser state, and uses explicit answer mappings rather than prompt defaults for eligibility/consent. It does not copy prompt instructions, resume tailoring or discovery logic. [Prompt source](https://github.com/Pickle-Pixel/ApplyPilot/blob/4a8d521f67f5139811c0a910ef37410f8e6d836a/src/applypilot/apply/prompt.py).

ApplyPilot persists pipeline/application state in SQLite; wagecuck narrows persistence to a single application's claim and commit uncertainty. ApplyPilot is AGPL-3.0; wagecuck's code was independently written from the requested architecture and behavior research. [Database source](https://github.com/Pickle-Pixel/ApplyPilot/blob/4a8d521f67f5139811c0a910ef37410f8e6d836a/src/applypilot/database.py), [license](https://github.com/Pickle-Pixel/ApplyPilot/blob/4a8d521f67f5139811c0a910ef37410f8e6d836a/LICENSE).

## Simplify: public code versus documented behavior

Simplify documents profile-backed autofill, résumé uploads, common questions, reuse of saved answers for identical questions, and tracking following submission. Its help workflow tells the applicant to review and submit. Wagecuck borrows the explicit profile and reusable answer-library approach, with an optional final-submit mode owned by its state machine. [Autofill documentation](https://help.simplify.jobs/articles/2415391-using-copilot-to-autofill-applications).

Its setup documentation names Workday, Lever, Greenhouse, Ashby, iCIMS and Taleo. These are compatibility claims, not evidence that every employer configuration is covered or that the product autonomously submits. [Copilot setup](https://help.simplify.jobs/en/articles/1749022-installing-and-setting-up-copilot).

Inspected the public `extension-take-home` repository at commit `92632033c1f89f81f0cad5b5abc6bc78c2d213ae`: README, content-script starter, background script and manifest. The README asks for sequential XPath-driven actions, visible per-step completion state, and suggests configurable JSON action descriptions. The starter content script renders an empty React component, the background registers an extension click handler, and the manifest injects content scripts in all frames. These establish a public engineering exercise, **not Copilot's production implementation**. No production autofill engine was found in the inspected public organization. [Exercise and starter code](https://github.com/SimplifyJobs/extension-take-home/tree/92632033c1f89f81f0cad5b5abc6bc78c2d213ae), [public organization](https://github.com/SimplifyJobs).

The shared conversation mentions an independent audit and remote selector-count estimates. Those counts were not independently verified here and are not used as implementation evidence. The maintainable lesson is a versioned rule registry plus verified actions, which wagecuck implements locally in `ats.json`.

## Browser and provider contracts

Playwright supplies locators, label-driven actions, native select/check/upload operations and automatic actionability waits. Wagecuck additionally reads back values and rechecks the complete fill plan. [Playwright actions](https://playwright.dev/python/docs/input), [locators](https://playwright.dev/python/docs/locators).

CapSolver documents `ReCaptchaV2TaskProxyLess`/enterprise variants and `AntiTurnstileTaskProxyLess`, with `createTask` followed by `getTaskResult`. The adapter uses these documented task names, required website URL/site key, bounded polling and provider-specific token fields. Only explicitly supported widget delivery paths are implemented. [reCAPTCHA v2](https://docs.capsolver.com/en/guide/captcha/ReCaptchaV2/), [Turnstile](https://docs.capsolver.com/en/guide/captcha/cloudflare_turnstile/), [polling](https://docs.capsolver.com/en/guide/api-gettaskresult/).

The optional local model integration uses Ollama's JSON-schema output on `/api/chat`. Its contract is fact-key classification, not generated browser code or qualifications. [Ollama chat API](https://docs.ollama.com/api/chat).

## Posting corpus and observed behavior

Searches targeted direct employer/ATS URLs for software roles in Canada/Toronto and deliberately included different control families. Eleven candidates were selected, rather than eleven copies of one simple form. Sources and coverage notes live in [the corpus](../examples/live-jobs.json). The checked browser results are in [live-report.json](live-report.json); these observations take precedence over search-index snippets.

| Family | Postings researched | Observations during inspection |
|---|---|---|
| Lever | AltaML intermediate, AltaML associate, PocketHealth engineer | Description pages lead to `/apply`; contact, upload and screening fields |
| Greenhouse | Capco, Dialpad, Canonical | Inline forms, React combobox helper inputs, location control, employer-specific questions; Dialpad link redirected to an error-marked job index |
| Ashby | Maxima, Cerebras, Relay | JavaScript hydration then `/application`; differently shaped custom questions |
| Workday | Autodesk, OCLC | Apply-choice dialog and manual-application path; Autodesk exposed an account gate; OCLC did not expose a usable form in the bounded inspection |

Implementation changes driven by these checks: exclude ARIA-hidden select helper inputs; coerce DOM booleans; strip dropdown option text from wrapping labels; prefer Apply Manually inside Workday's choice dialog; identify Greenhouse error redirects as closed postings. Live runs never entered the synthetic profile, uploaded its PDF, or submitted to employers.

Local fixtures exercise successful POST/PDF upload, popup and iframe entry, open shadow DOM, multi-step review, conditional fields, selects/radio/checkbox/combobox controls, missing answers, account gates, closed jobs, validation changes, duplicate claims, uncertain receipt, and mocked CapSolver integration. Full production employer submission and paid CAPTCHA acceptance are outside the observed test evidence.

## Expanded checks, September 16, 2026

The additional [20-URL corpus](../examples/expanded-jobs.json) covers Workable, SmartRecruiters, Jobvite, Recruitee, BambooHR, Teamtailor, Pinpoint, Breezy and iCIMS. Search results included expired listings, so current links were also taken directly from employer career boards: [Chipply](https://apply.workable.com/chipply/), [CARTO](https://carto.pinpointhq.com/), [Codebase](https://codebase.breezy.hr/), and [ClearRoute](https://clearroute.teamtailor.com/jobs). Expired and inaccessible candidates remain in the corpus to exercise failure reporting.

The [all-application dry-run report](dry-run-all.md) includes every original and expanded URL. Reachable forms were loaded in Chromium, then disconnected from networking before the actual planner/executor entered synthetic values. This measures DOM compatibility and profile coverage, not complete application success. The JSON retains individual field failures, missing required answers, CAPTCHA type and navigation blockers. Several live Lever/Recruitee forms expose hCaptcha, which the current CapSolver adapter does not solve.

These runs exposed and drove fixes for premature entry clicks before hydration, additional Apply labels, cookie overlays, careers search false positives, expired Jobvite/SmartRecruiters pages, scoped group questions, unlabeled upload controls, required CSS markers, React Select rendered values, dial-code country options and masked telephone inputs. Field-level regression fixtures exercise these behaviors without depending on employer availability.
