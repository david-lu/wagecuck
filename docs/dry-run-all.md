# Application dry-run coverage

Checked: 2026-09-16T05:43:52.786365+00:00. URLs: 31. Employer submissions: 0.

Every URL was attempted in a fresh browser context. For reachable forms, the real planner and field executor ran on the loaded DOM after network access was disabled. Service workers and WebSockets were blocked. Closed, inaccessible and login-gated URLs remain in this report as failures; they are not skipped from the totals.

**These results do not establish complete application compatibility.** They cover navigation and the loaded form step. Server validation, upload acceptance, remote autocomplete, later pages and real CAPTCHA acceptance remain unverified. MAPPED_FIELDS_VERIFIED means only the profile mappings were retained locally.

Outcome counts: REQUIRED_ANSWER_MISSING: 11, FIELD_FILL_FAILED: 5, JOB_CLOSED: 7, AUTH_REQUIRED: 1, FORM_NOT_FOUND: 4, ACCESS_DENIED: 2, MAPPED_FIELDS_VERIFIED: 1.

| Case | Outcome | Fields filled / mapped | Missing required answers | CAPTCHA detected |
|---|---|---:|---:|---|
| [lever-altaml-intermediate](https://jobs.lever.co/altaml/74efba24-9733-4308-903a-9757a80684ba/apply) | REQUIRED_ANSWER_MISSING | 7 / 7 | 2 | hcaptcha |
| [lever-altaml-intern](https://jobs.lever.co/altaml/bd8167f5-e84c-48b1-9cad-2831bf71dea1/apply) | REQUIRED_ANSWER_MISSING | 6 / 6 | 1 | hcaptcha |
| [lever-pockethealth](https://jobs.lever.co/PocketHealth/72a1603f-2fa5-4242-9250-9573f7e1070c/apply) | FIELD_FILL_FAILED | 7 / 8 | 5 | hcaptcha |
| [greenhouse-capco](https://job-boards.greenhouse.io/capco/jobs/8160588) | FIELD_FILL_FAILED | 5 / 7 | 7 | recaptcha_v2 |
| [greenhouse-dialpad](https://job-boards.greenhouse.io/dialpad?error=true) | JOB_CLOSED | — | — | not observed |
| [greenhouse-canonical](https://job-boards.greenhouse.io/canonicaljobs/jobs/6768836) | FIELD_FILL_FAILED | 6 / 7 | 19 | recaptcha_v2 |
| [ashby-maxima](https://jobs.ashbyhq.com/Maxima/c513fc8d-9f63-4e5d-a011-45238bf7d903/application) | REQUIRED_ANSWER_MISSING | 6 / 6 | 1 | recaptcha_v2 |
| [ashby-cerebras](https://jobs.ashbyhq.com/cerebras/99c289fa-8fc6-49f7-b7e8-78ac4e9d99ac/application) | REQUIRED_ANSWER_MISSING | 5 / 5 | 1 | recaptcha_v2 |
| [ashby-relay](https://jobs.ashbyhq.com/relayfi/c412e8d5-d7fc-4dde-b905-e3e4ceb03c08/application) | REQUIRED_ANSWER_MISSING | 4 / 4 | 3 | recaptcha_v2 |
| [workday-autodesk](https://autodesk.wd1.myworkdayjobs.com/en-US/Ext/job/Toronto%2C-ON%2C-CAN/Software-Engineer_26WD100762-1/apply/applyManually) | AUTH_REQUIRED | — | — | not observed |
| [workday-oclc](https://oclc.wd1.myworkdayjobs.com/en-US/OCLC_Careers/job/Ottawa/Software-Engineer---Canada_R0003900/apply/applyManually) | FORM_NOT_FOUND | — | — | not observed |
| [workable-thanks](https://apply.workable.com/thanks-co/?not_found=true) | JOB_CLOSED | — | — | not observed |
| [workable-activ](https://apply.workable.com/activ-2/?not_found=true) | JOB_CLOSED | — | — | not observed |
| [smartrecruiters-servicenow](https://jobs.smartrecruiters.com/ServiceNow/744000132922559-sr-software-engineer-veza) | JOB_CLOSED | — | — | not observed |
| [smartrecruiters-ubisoft](https://jobs.smartrecruiters.com/oneclick-ui/company/Ubisoft2/publication/715fc046-f687-4ace-80a1-3af89c18c03f?dcr_ci=Ubisoft2) | ACCESS_DENIED | — | — | not observed |
| [jobvite-progress](https://jobs.jobvite.com/careers/progress/jobs?error=404) | JOB_CLOSED | — | — | not observed |
| [jobvite-ezra](https://jobs.jobvite.com/ezra/job/o233zfwr/apply) | REQUIRED_ANSWER_MISSING | 0 / 0 | 1 | not observed |
| [recruitee-bluestone](https://bluestonelogic.recruitee.com/o/software-engineer-1/c/new) | MAPPED_FIELDS_VERIFIED | 4 / 4 | 0 | not observed |
| [recruitee-trafilea](https://trafilea.recruitee.com/o/senior-software-engineer-backend/c/new) | REQUIRED_ANSWER_MISSING | 4 / 4 | 5 | not observed |
| [bamboohr-r2](https://r2.bamboohr.com/careers/177) | FIELD_FILL_FAILED | 8 / 9 | 4 | recaptcha_v2 |
| [bamboohr-versafile](https://versafile1.bamboohr.com/careers) | FORM_NOT_FOUND | — | — | not observed |
| [teamtailor-reel](https://careers.reel.energy/jobs/7501997-senior-software-engineer) | JOB_CLOSED | — | — | not observed |
| [teamtailor-jenni](https://jenniai-1748504648.teamtailor.com/jobs/5989905-growth-engineer/applications/new) | JOB_CLOSED | — | — | not observed |
| [pinpoint-carto](https://carto.pinpointhq.com/) | FORM_NOT_FOUND | — | — | not observed |
| [breezy-codebase](https://codebase.breezy.hr/p/2d1498a8dcda-full-stack-developer-react-typescript) | FORM_NOT_FOUND | — | — | not observed |
| [breezy-opensea](https://opensea.breezy.hr/p/5d42cd50fcea-full-stack-software-engineer/apply) | REQUIRED_ANSWER_MISSING | 5 / 5 | 1 | not observed |
| [icims-platform](https://careers.icims.com/technology/jobs/6460?lang=en-gb&previousLocale=en-US) | ACCESS_DENIED | — | — | not observed |
| [teamtailor-clearroute](https://clearroute.teamtailor.com/jobs/8326970-software-engineer-platform-ai) | FIELD_FILL_FAILED | 4 / 6 | 13 | not observed |
| [pinpoint-carto-current](https://carto.pinpointhq.com/en/postings/202b72e3-f76b-4096-80c2-a5be77d8a2e2/applications/new) | REQUIRED_ANSWER_MISSING | 8 / 8 | 1 | not observed |
| [workable-chipply](https://apply.workable.com/chipply/j/3EBC0B6983/apply/) | REQUIRED_ANSWER_MISSING | 6 / 6 | 1 | not observed |
| [breezy-codebase-current](https://codebase.breezy.hr/p/c1b62fc81300-senior-full-stack-developer-react-js-node-js/apply) | REQUIRED_ANSWER_MISSING | 5 / 5 | 3 | not observed |

Field-level failures and exact unanswered questions are in [the JSON report](dry-run-all.json).
