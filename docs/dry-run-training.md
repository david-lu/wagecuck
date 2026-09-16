# Application dry-run coverage

Checked: 2026-09-16T18:35:36.937693+00:00. Dataset: training. URLs: 39. Employer submissions: 0.
Model provider: openai. Model: gpt-5.6-terra. Model calls attempted: 63. Profile inference and invented-answer fallback: True.

Every URL was attempted in a fresh browser context. For reachable forms, the real planner and field executor ran on the loaded DOM after network access was disabled. Service workers and WebSockets were blocked. Closed, inaccessible and login-gated URLs remain in this report as failures; they are not skipped from the totals.

**These results do not establish complete application compatibility.** They cover navigation and the loaded form step. Server validation, upload acceptance, remote autocomplete, later pages and real CAPTCHA acceptance remain unverified. MAPPED_FIELDS_VERIFIED means only the profile mappings were retained locally.

Outcome counts: FIELD_FILL_FAILED: 14, JOB_CLOSED: 9, MAPPED_FIELDS_VERIFIED: 9, AUTH_REQUIRED: 1, FORM_NOT_FOUND: 4, ACCESS_DENIED: 2.

| Case | Outcome | Questions satisfied / total | Required satisfied / total | Required pass | Made-up answers filled | CAPTCHA detected |
|---|---|---:|---:|---|---:|---|
| [lever-altaml-intermediate](https://jobs.lever.co/altaml/74efba24-9733-4308-903a-9757a80684ba/apply) | FIELD_FILL_FAILED | 15 / 16 | 7 / 7 | yes | 1 | hcaptcha |
| [lever-altaml-intern](https://jobs.lever.co/altaml/bd8167f5-e84c-48b1-9cad-2831bf71dea1/apply) | FIELD_FILL_FAILED | 16 / 18 | 5 / 5 | yes | 2 | hcaptcha |
| [lever-pockethealth](https://jobs.lever.co/PocketHealth/72a1603f-2fa5-4242-9250-9573f7e1070c/apply) | FIELD_FILL_FAILED | 18 / 19 | 10 / 10 | yes | 6 | hcaptcha |
| [greenhouse-capco](https://job-boards.greenhouse.io/capco/jobs/8160588) | FIELD_FILL_FAILED | 12 / 16 | 11 / 13 | no | 5 | recaptcha_v2 |
| [greenhouse-dialpad](https://job-boards.greenhouse.io/dialpad?error=true) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [greenhouse-canonical](https://job-boards.greenhouse.io/canonicaljobs/jobs/6768836) | FIELD_FILL_FAILED | 16 / 26 | 12 / 18 | no | 7 | recaptcha_v2 |
| [ashby-maxima](https://jobs.ashbyhq.com/Maxima/c513fc8d-9f63-4e5d-a011-45238bf7d903/application) | FIELD_FILL_FAILED | 7 / 8 | 4 / 4 | yes | 0 | recaptcha_v2 |
| [ashby-cerebras](https://jobs.ashbyhq.com/cerebras/99c289fa-8fc6-49f7-b7e8-78ac4e9d99ac/application) | MAPPED_FIELDS_VERIFIED | 10 / 11 | 5 / 5 | yes | 2 | recaptcha_v2 |
| [ashby-relay](https://jobs.ashbyhq.com/relayfi/c412e8d5-d7fc-4dde-b905-e3e4ceb03c08/application) | FIELD_FILL_FAILED | 11 / 13 | 6 / 6 | yes | 3 | recaptcha_v2 |
| [workday-autodesk](https://autodesk.wd1.myworkdayjobs.com/en-US/Ext/job/Toronto%2C-ON%2C-CAN/Software-Engineer_26WD100762-1/apply/applyManually) | AUTH_REQUIRED | — | — | — | not recorded | not observed |
| [workday-oclc](https://oclc.wd1.myworkdayjobs.com/en-US/OCLC_Careers/job/Ottawa/Software-Engineer---Canada_R0003900/apply/applyManually) | FORM_NOT_FOUND | — | — | — | not recorded | not observed |
| [workable-thanks](https://apply.workable.com/thanks-co/?not_found=true) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [workable-activ](https://apply.workable.com/activ-2/?not_found=true) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [smartrecruiters-servicenow](https://jobs.smartrecruiters.com/ServiceNow/744000132922559-sr-software-engineer-veza) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [smartrecruiters-ubisoft](https://jobs.smartrecruiters.com/oneclick-ui/company/Ubisoft2/publication/715fc046-f687-4ace-80a1-3af89c18c03f?dcr_ci=Ubisoft2) | ACCESS_DENIED | — | — | — | not recorded | not observed |
| [jobvite-progress](https://jobs.jobvite.com/careers/progress/jobs?error=404) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [jobvite-ezra](https://jobs.jobvite.com/ezra/job/o233zfwr/apply) | MAPPED_FIELDS_VERIFIED | 1 / 1 | 1 / 1 | yes | 1 | not observed |
| [recruitee-bluestone](https://bluestonelogic.recruitee.com/o/software-engineer-1/c/new) | MAPPED_FIELDS_VERIFIED | 4 / 5 | 4 / 4 | yes | 0 | not observed |
| [recruitee-trafilea](https://trafilea.recruitee.com/o/senior-software-engineer-backend/c/new) | MAPPED_FIELDS_VERIFIED | 9 / 9 | 9 / 9 | yes | 2 | not observed |
| [bamboohr-r2](https://r2.bamboohr.com/careers/177) | FIELD_FILL_FAILED | 12 / 13 | 12 / 13 | no | 1 | recaptcha_v2 |
| [bamboohr-versafile](https://versafile1.bamboohr.com/careers) | FORM_NOT_FOUND | — | — | — | not recorded | not observed |
| [teamtailor-reel](https://careers.reel.energy/jobs/7501997-senior-software-engineer) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [teamtailor-jenni](https://jenniai-1748504648.teamtailor.com/jobs/5989905-growth-engineer/applications/new) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [pinpoint-carto](https://carto.pinpointhq.com/) | FORM_NOT_FOUND | — | — | — | not recorded | not observed |
| [breezy-codebase](https://codebase.breezy.hr/p/2d1498a8dcda-full-stack-developer-react-typescript) | FORM_NOT_FOUND | — | — | — | not recorded | not observed |
| [breezy-opensea](https://opensea.breezy.hr/p/5d42cd50fcea-full-stack-software-engineer/apply) | MAPPED_FIELDS_VERIFIED | 7 / 7 | 5 / 5 | yes | 0 | not observed |
| [icims-platform](https://careers.icims.com/technology/jobs/6460?lang=en-gb&previousLocale=en-US) | ACCESS_DENIED | — | — | — | not recorded | not observed |
| [teamtailor-clearroute](https://clearroute.teamtailor.com/jobs/8326970-software-engineer-platform-ai) | FIELD_FILL_FAILED | 22 / 25 | 19 / 20 | no | 8 | not observed |
| [pinpoint-carto-current](https://carto.pinpointhq.com/en/postings/202b72e3-f76b-4096-80c2-a5be77d8a2e2/applications/new) | MAPPED_FIELDS_VERIFIED | 18 / 18 | 7 / 7 | yes | 3 | not observed |
| [workable-chipply](https://apply.workable.com/chipply/j/3EBC0B6983/apply/) | FIELD_FILL_FAILED | 9 / 11 | 6 / 7 | no | 0 | not observed |
| [breezy-codebase-current](https://codebase.breezy.hr/p/c1b62fc81300-senior-full-stack-developer-react-js-node-js/apply) | MAPPED_FIELDS_VERIFIED | 11 / 11 | 10 / 10 | yes | 0 | not observed |
| [greenhouse-doordash-backend](https://job-boards.greenhouse.io/doordashusa/jobs/5630445) | FIELD_FILL_FAILED | 13 / 28 | 13 / 24 | no | 2 | recaptcha_v2 |
| [greenhouse-applied-intuition-full-stack](https://job-boards.greenhouse.io/appliedintuition?error=true) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [greenhouse-accenture-federal-software](https://job-boards.greenhouse.io/accenturefederalservices/jobs/4700296006) | FIELD_FILL_FAILED | 19 / 31 | 18 / 25 | no | 5 | recaptcha_v2 |
| [workable-total-life-full-stack](https://apply.workable.com/total-life/j/84F61216A8/apply/) | FIELD_FILL_FAILED | 10 / 12 | 8 / 9 | no | 1 | not observed |
| [workable-devsinc-python](https://apply.workable.com/devsinc-17/?not_found=true) | JOB_CLOSED | — | — | — | not recorded | not observed |
| [breezy-centrl-software-engineer](https://centrl.breezy.hr/p/5f71fbede25a-software-engineer/apply) | MAPPED_FIELDS_VERIFIED | 7 / 7 | 4 / 4 | yes | 0 | not observed |
| [breezy-utilityapi-ai-platform](https://utilityapi.breezy.hr/p/7c161dbb2af0-senior-staff-software-engineer-ai-systems-platform/apply) | MAPPED_FIELDS_VERIFIED | 12 / 12 | 11 / 11 | yes | 1 | not observed |
| [teamtailor-clearroute-agentic-ai](https://clearroute.teamtailor.com/jobs/8261776-agentic-ai-developer-python) | FIELD_FILL_FAILED | 12 / 15 | 9 / 10 | no | 1 | not observed |

Field-level failures and exact unanswered questions are in [the JSON report](dry-run-training.json).
