# Application dry-run coverage

Checked: 2026-09-16T07:56:41.523589+00:00. URLs: 2. Employer submissions: 0.
Model provider: openai. Model: gpt-5.6-terra. Model calls attempted: 5. Profile inference and invented-answer fallback: True.

Every URL was attempted in a fresh browser context. For reachable forms, the real planner and field executor ran on the loaded DOM after network access was disabled. Service workers and WebSockets were blocked. Closed, inaccessible and login-gated URLs remain in this report as failures; they are not skipped from the totals.

**These results do not establish complete application compatibility.** They cover navigation and the loaded form step. Server validation, upload acceptance, remote autocomplete, later pages and real CAPTCHA acceptance remain unverified. MAPPED_FIELDS_VERIFIED means only the profile mappings were retained locally.

Outcome counts: MAPPED_FIELDS_VERIFIED: 1, FIELD_FILL_FAILED: 1.

| Case | Outcome | Fields filled / mapped | Missing required answers | Made-up answers filled | CAPTCHA detected |
|---|---|---:|---:|---:|---|
| [jobvite-ezra](https://jobs.jobvite.com/ezra/job/o233zfwr/apply) | MAPPED_FIELDS_VERIFIED | 1 / 1 | 0 | 0 | not observed |
| [pinpoint-carto-current](https://carto.pinpointhq.com/en/postings/202b72e3-f76b-4096-80c2-a5be77d8a2e2/applications/new) | FIELD_FILL_FAILED | 11 / 16 | 0 | 0 | not observed |

Field-level failures and exact unanswered questions are in [the JSON report](inference-live.json).
