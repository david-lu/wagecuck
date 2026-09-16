# Application dry-run coverage

Checked: 2026-09-16T18:38:22.106634+00:00. Dataset: validation. URLs: 8. Employer submissions: 0.
Model provider: openai. Model: gpt-5.6-terra. Model calls attempted: 10. Profile inference and invented-answer fallback: True.

Every URL was attempted in a fresh browser context. For reachable forms, the real planner and field executor ran on the loaded DOM after network access was disabled. Service workers and WebSockets were blocked. Closed, inaccessible and login-gated URLs remain in this report as failures; they are not skipped from the totals.

**These results do not establish complete application compatibility.** They cover navigation and the loaded form step. Server validation, upload acceptance, remote autocomplete, later pages and real CAPTCHA acceptance remain unverified. MAPPED_FIELDS_VERIFIED means only the profile mappings were retained locally.

This is an aggregate-only held-out report. Field labels, options, mappings and failure details were not persisted and must not be used to tune field behavior.

Outcome counts: MAPPED_FIELDS_VERIFIED: 2, FORM_NOT_FOUND: 1, REQUIRED_ANSWER_MISSING: 1, JOB_CLOSED: 3, FIELD_FILL_FAILED: 1.

| Case | Outcome | Questions satisfied / total | Required satisfied / total | Required pass | Made-up answers filled | CAPTCHA detected |
|---|---|---:|---:|---|---:|---|
| [validation-ashby-mycroft](https://jobs.ashbyhq.com/mycroft/7b832163-c9e5-48f1-a68b-6523073800ff/application) | MAPPED_FIELDS_VERIFIED | 4 / 4 | 3 / 3 | yes | 0 | recaptcha_v2 |
| [validation-ashby-gigi](https://jobs.ashbyhq.com/gigi/5fb915ce-aa72-41a7-976e-430a10928c24/application) | MAPPED_FIELDS_VERIFIED | 8 / 8 | 4 / 4 | yes | 1 | recaptcha_v2 |
| [validation-ashby-harvey](https://jobs.ashbyhq.com/harvey/9fa38e48-dba2-496d-baf7-32a8c5e0c730) | FORM_NOT_FOUND | — | — | — | not recorded | not observed |
| [validation-breezy-skyspecs](https://skyspecs.breezy.hr/p/d9399b066206-senior-software-engineer/apply) | REQUIRED_ANSWER_MISSING | 13 / 17 | 11 / 15 | no | 1 | not observed |
| [validation-teamtailor-mannarino](https://careers.mss.ca/jobs/5977506-drone-ground-station-software-developer) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [validation-workable-goglobal](https://apply.workable.com/goglobal/j/8E35097D4B/apply/) | FIELD_FILL_FAILED | 13 / 17 | 12 / 14 | no | 2 | not observed |
| [validation-greenhouse-zip](https://job-boards.greenhouse.io/zipcolimited?error=true) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [validation-lever-glue](https://jobs.lever.co/gluegroups/357a840b-e0eb-47ff-97f9-1d7161bbb399) | JOB_CLOSED | — | — | — | not recorded | not observed |

Aggregate per-case results are in [the JSON report](dry-run-validation.json).
