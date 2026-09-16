# Architecture and contracts

```mermaid
flowchart TD
  A[Job URL + Profile + RunOptions] --> B[Validate documents and claim submission key]
  B --> C[Playwright isolated browser context]
  C --> D[ATS rules + live DOM scanner]
  D --> E[Normalized fields and controls]
  E --> F[WorkflowAgent: named profile fields and declarations]
  F --> G[Optional requirement assessment and constrained fact selection]
  F --> H[Playwright fill and upload]
  G --> O[Opt-in drafting for unmapped prose fields]
  O --> H
  H --> I[Re-scan and verify complete plan]
  I -->|Next page or conditional field| D
  I -->|Final step| J[CapSolver if configured and needed]
  J --> K[Durable submission fence]
  K --> L[Deliver token and submit once]
  L --> M[Observe new receipt]
  M --> N[Typed result + SQLite state + artifacts]
```

`ApplicationRunner.run` is the single-job boundary. It does not discover jobs, score fit, tailor qualifications, or delegate browser control to an LLM. A future queue should invoke this boundary independently for each job/profile. The shipped live-inspection script is a sequential test harness, not a batch application product.

## Modules

| Module | Responsibility |
|---|---|
| `models.py` | Pydantic profile, normalized form, options and result contracts |
| `ats.json`, `ats.py` | Versioned local selector/action rules, exact hostname detection |
| `parse.js`, `browser.py` | Labels/ARIA/native controls, frame and open shadow-root traversal; Playwright actions and value verification |
| `agent.py` | Planning coordinator and compatibility exports for public agent contracts |
| `agent_types.py`, `agent_prompts.py` | Provider-neutral operation/contracts and prompts for requirement, mapping, drafting and inference calls |
| `agent_client.py`, `agent_config.py` | Structured operation builder, Ollama/OpenAI transports, retry policy, metrics, `.env` loading and provider selection |
| `field_semantics.py`, `mapping.py` | Deterministic label recognition plus isolated validation/application of each model mapping proposal |
| `requirements.py`, `inference.py` | Required-field evidence validation and typed inferred-answer validation/provenance |
| `field_values.py` | Native input value contracts, typed normalization and site-specific rendering |
| `logical_fields.py` | Radio/checkbox control grouping and question-level report rows |
| `screening.py` | Explicit country-specific authorization, visa, sponsorship, veteran and disability declarations |
| `profile_fields.py` | Question intent to named application fields; shared choice rendering and per-field declaration guards |
| `captcha.py` | CapSolver transport, widget detection, explicit token delivery |
| `runner.py` | Bounded workflow, progression, failure classification, submission verification |
| `store.py` | Atomic SQLite claim and durable no-resubmit fence |
| `demo.py`, `fixtures.py` | Synthetic profile/PDF and local test portals |

Selectors annotate live DOM elements with normalized fact/action hints. Generic label mappings remain available if the ATS is unknown. No remote executable selectors/configuration are downloaded. Individual form instances use ephemeral element IDs, while loop detection uses URL and semantic field/control signatures so a rerender does not defeat the step bound.

## Applicant data

Profiles store contact/custom `facts`, a structured `address`, and `employment`/`education` arrays. History entries have typed dates, explicit current status, location and summary, plus employment company/title or education school/degree/field. `Profile.values()` projects sections into atomic string/boolean choices such as `employment.0.title`, preserving indices, and computes full-name, street-address, full-address and location choices through shared helpers. ISO dates become strings; unknown dates and empty strings are omitted. A missing end date does not infer current employment. Multiple current jobs leave singular current-company/title aliases unresolved.

Existing flat profiles remain readable. A structured address replaces flat address components; populated structured history arrays replace their indexed flat keys. Repeated education/employment rows can be represented in a profile, but automatically adding/repeating rows on the application page is not implemented. Documents are paths validated before filling. Exact question answers are stored separately from general facts. Direct sensitive mappings require a declaration with matching meaning and country. With agent-fill enabled, the final inference stage may create unsupported answers, but labels them made_up rather than treating them as existing profile declarations.

Normal answers are named data: `application` contains availability, pronouns, compensation and supplied prose; `experience` stores explicit skill-specific years/experience; `consents` stores distinct choices; `screening` stores eligibility, veteran/disability and structured demographic declarations. Demographics cover gender identity, sexual orientation, transgender status, race/ethnicity and Hispanic/Latino status. `declared_values()` and `values()` expose only populated keys to the planner. `authorized_canada` is an alias for `screening.work_authorization.CA.authorized`; there is no second answer value to synchronize. The demo has no question-keyed `answers`; that field remains a compatibility override for existing profiles.

Both parser and model routes resolve declared values through the same control renderer. Numeric years preserve zero; boolean choices preserve false. Radio and checkbox options are interpreted through a shared question heading even when the underlying option names differ. This prevents an option label from being treated as an independent required question and lets multi-select race declarations choose only their matching options. Model-selected declarations are checked against question meaning, jurisdiction and currency. Exact option matching may still leave a populated field unresolved when an employer uses unsupported choices.

All page content is untrusted input. The optional model gets no navigation, JavaScript, file-access or submission tools. Deterministic planning runs first for every discovered editable field. The fallback mapper receives field metadata and an enum of available profile keys, with validated single-key selection or ordered text combination. Each mapping proposal is validated independently: valid fields are retained, while an unsafe, unknown or malformed proposal leaves only that field unresolved and emits a field-level warning. Document keys can target upload controls only. Screening questions use explicit declarations and vetted choice labels; missing sponsorship is not inferred from visa type. Exact question answers override the screening mapper.

Requirement assessment accepts high-confidence claims backed by exact nearby evidence. It cannot demote a DOM-required field. Each field records `required`, `optional` or `unknown`; lack of a required attribute alone does not establish optionality. `analysis-NN.json` exposes the status, evidence, source keys and deterministic/mapping/drafting/unresolved route for all discovered fields, without answer values. Inspection performs classification without filling or drafting.

`RunOptions(agent_fill=True)` enables the final `infer` stage for every unresolved supported input. It receives all resolved raw and derived profile values (excluding credentials and document paths), field context/options, the native value contract and the current reference date. It uses profile information directly, infers from it, or generates an explicitly marked invented answer when support is absent. `inference.py` validates field IDs, option membership, typed values and one selection per radio group; invalid answers remain unresolved. Values are rendered for the actual control only after validation, so an ISO model/profile date can become `MM/DD/YYYY` for a text date field while a native date input keeps ISO format. Numeric controls receive a numeric scalar rather than a compensation sentence. Mapping transport and requirement-assessment failures become warnings and permit the inference stage to proceed. File inputs still require real documents. Older custom adapters exposing only `draft` retain the legacy prose path.

Model calls carry an explicit operation type rather than inferring behavior from a schema title. The OpenAI adapter builds a strict schema for that operation, retries rate limits and server errors with bounded exponential backoff, and records logical calls, HTTP attempts, retries, token usage and provider request IDs. Exposed errors contain sanitized categories and status codes; provider response bodies are never copied into result messages or metrics.

Each action records `answer_basis`, `made_up`, `source_keys` and `inference_reason`. These propagate to analysis, dry-run outcomes, fill events and the final `answer_log`, including fill failures. No inferred or invented value is written back to the profile. Missing citations force a made_up label, but model-supplied provenance is not a proof of entailment.

Standard text, date, number, email, telephone, URL, native select, checkbox, radio, file and common ARIA combobox actions are implemented. The parser retains placeholders, input modes, patterns, length bounds and numeric/date bounds. Both deterministic and model-generated answers pass through the same value-contract validator before Playwright fills a control, and native browser validity is checked after filling. Custom comboboxes require one exact visible option. After all fills, the complete current plan is checked again to catch change handlers that overwrite earlier fields. Conditional fields trigger a fresh scan. Each run has a time limit, action timeouts, no-progress detection and a step limit. Destructive actions are never retried based on a timeout.

Reports use logical application questions as their primary field rows. Radio and checkbox controls that share a DOM group are emitted once with their available options and selected state; raw physical controls remain under `control_outcomes` and `control_analysis` for executor debugging. Selects and comboboxes are already single logical controls and keep their option lists on that row.

## Results

| Status | success | Meaning |
|---|---|---|
| `succeeded` | true | A new application receipt message appeared after submission |
| `ready` | false | Supported fields verified; stopped before final submit |
| `inspected` | false | Form metadata extracted; no applicant data entered |
| `failed` | false | Workflow stopped before the final submission boundary |
| `unknown` | false | Submission attempted, but no receipt was observed |

`submitted` is true only on confirmed success. `submission_attempted` records crossing the final-action boundary. Neither HTTP 200 nor clicking Submit proves success. Receipt matching is currently based on explicit English confirmation phrases in rendered text, compared to the pre-submit baseline. This cannot prove backend persistence; it is stronger than inferring success from disappearance of a button. Local fixtures verify server receipt independently. Translated/custom confirmations require additional rules.

The CLI exits 0 for succeeded/ready/inspected, 1 for failure, and 2 for an uncertain submission. Automation should inspect the JSON code/status, not just the process exit code.

`RunOptions(wait_for_user=True)` adds an interactive handoff to fill mode and forces a visible browser. After final validation, the automated time budget is disabled, the browser stays open, and the runner only observes pages for receipt. It never clicks Submit or invokes CAPTCHA callbacks during manual review. A new receipt returns success; browser closure without receipt returns `unknown / USER_SUBMISSION_UNCONFIRMED`. The `user_handoff` result flag distinguishes this path. `submission_attempted` becomes true when a manual receipt is observed; a false value after handoff does not prove the person never submitted.

## Error codes

| Codes | Meaning / next action |
|---|---|
| `INVALID_INPUT`, `PROFILE_INVALID`, `DOCUMENT_MISSING` | Fix input/profile/document configuration |
| `NAVIGATION_FAILED`, `TIMEOUT`, `BROWSER_ERROR` | Transient pre-submit failure may be retryable |
| `JOB_CLOSED`, `ACCESS_DENIED` | Posting unavailable or site refused access |
| `AUTH_REQUIRED` | Supply a valid session; no login/account/OTP automation is implemented |
| `CAPTCHA_REQUIRED` | Challenge encountered in inspect/fill mode, or unrecognized CAPTCHA markup |
| `CAPTCHA_KEY_MISSING` | Configure `CAPSOLVER_API_KEY` |
| `CAPTCHA_UNSUPPORTED` | Unsupported challenge type or token delivery integration |
| `CAPTCHA_SOLVE_FAILED`, `CAPTCHA_TIMEOUT` | Provider error, malformed response, or solve budget exhausted |
| `FORM_NOT_FOUND`, `SUBMIT_NOT_FOUND` | Page does not expose recognized application controls |
| `REQUIRED_ANSWER_MISSING` | Populate the corresponding named profile declaration or supply a question-specific override |
| `UNSUPPORTED_CONTROL`, `FIELD_FILL_FAILED`, `VALIDATION_FAILED` | Control/action or value verification could not be completed |
| `AGENT_FAILED` | Agent unavailable or invalid mapping output |
| `NO_PROGRESS`, `STEP_LIMIT` | Workflow did not progress within its bounds |
| `ALREADY_SUBMITTED`, `RUN_IN_PROGRESS` | Durable duplicate/active claim detected |
| `PRIOR_SUBMISSION_UNCERTAIN` | Earlier run may have submitted; do not automatically retry |
| `SUBMISSION_UNCONFIRMED` | Current run may have submitted; inspect employer-side status |
| `USER_SUBMISSION_UNCONFIRMED` | Manual review ended without observed receipt; inspect employer-side status |
| `SYNTHETIC_PROFILE_BLOCKED` | Demo profiles can submit only to local fixtures |
| `INTERNAL_ERROR` | Unexpected implementation/environment failure |

Errors after the final action boundary are conservatively represented as `SUBMISSION_UNCONFIRMED`. A successful receipt remains success even if browser cleanup fails. Captcha solving itself happens before the fence; token delivery callbacks happen after it because a callback can submit.

## Persistence and future batching

SQLite claims use `BEGIN IMMEDIATE` and a unique hash of profile ID and canonical job URL. Tracking parameters are stripped, meaningful job query parameters preserved, and Lever `/apply` and Ashby `/application` aliases deduplicated. Arbitrary employer redirects/URL aliases are not globally resolved. Use a stable profile ID for one applicant.

The journal moves through `claimed → submitting → succeeded/uncertain`, or `claimed → failed`. The `submitting` state is committed before the final browser action. Crashed submitting claims are never automatically released. Crashed claimed rows also require operator reconciliation; automatic leases are deliberately absent because they could let a slow original worker and a replacement overlap. No exactly-once guarantee can be made for a third-party employer form without its own idempotency API.

Inspect/fill runs do not claim a submission key unless `wait_for_user` is enabled. Manual handoff commits an `awaiting_user` state before releasing control; receipt transitions it to succeeded, while closure or interruption leaves an uncertain claim. Run artifacts are isolated by UUID. A future batch scheduler can use bounded concurrency and the `retryable` flag; it must never blanket-retry unknown results. For multi-host workers, replace SQLite with transactional shared storage and add a human reconciliation interface.

## Current verification boundary

The local fixture suite proves browser actions, file uploads, progression, confirmation detection and deduplication for its scenarios. Live inspection proves navigation/scanning only. It does not establish that real employers accept automated applications or that every employer's question set is covered. Test fixtures are simplified control reproductions, not copies of production ATS frontends.

Live evaluation uses disjoint training and validation manifests. Training forms may inform implementation and regression tests. Validation forms produce aggregate-only reports from temporary artifacts, and their posting tokens are checked for leakage into runtime source and tests. Each validation report includes a runtime-source fingerprint. A validation score applies only to that fingerprint and must not be used to tune field behavior.
