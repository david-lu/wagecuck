# Training and held-out validation

The application engine is evaluated with two disjoint URL corpora.

`examples/training-jobs.json` is the development corpus. It currently expands to 39 postings across Lever, Greenhouse, Ashby, Workday, Workable, SmartRecruiters, Jobvite, Recruitee, BambooHR, Teamtailor, Pinpoint, Breezy and iCIMS. Developers may inspect its DOM metadata, add regressions from its labels and use its failures to improve parsing, mapping and Playwright execution.

`examples/validation-jobs.json` contains eight held-out postings selected before validation. Their application forms must not be inspected while implementing field behavior. Their labels, options, mappings, source keys and failure details must not be copied into production rules, prompts, profiles or regression tests.

The normal corpus command runs training only:

```powershell
.venv\Scripts\python.exe scripts\dry_run_all.py --agent-provider openai --agent-fill
```

Held-out validation is an explicit separate command:

```powershell
.venv\Scripts\python.exe scripts\dry_run_all.py --split validation --agent-provider openai --agent-fill
```

Validation navigation uses temporary HTML and screenshot artifact directories. Its navigation report, dry-run report and console rows are aggregate-only: they retain case outcomes and counts while discarding field labels, question text, options, mappings, source keys, inferred reasons and individual failure details. The report records a SHA-256 fingerprint of all runtime Python, JavaScript and JSON under `src/wagecuck`, tying the result to the exact evaluated implementation.

Validation results may measure generalization, but they may not drive a code change. If a developer sees the aggregate validation result and then changes runtime field behavior, the old score no longer evaluates that new implementation. Select a new unseen validation corpus before making another held-out claim. Existing validation cases remain validation cases; they are never promoted into training.

`tests/test_evaluation_corpus.py` enforces disjoint IDs and URLs and checks that each validation posting's company/job tokens are absent from runtime source and regression tests. ATS-family hostnames and generic selectors remain shared because the split evaluates new forms within supported platform families, not whether the application can recognize the ATS hostname.

This separation does not make the engine universal. It measures whether the general label, ARIA, group, type, option, profile-semantic and Playwright logic transfers to unseen postings. Account requirements, closed listings, access controls, novel custom widgets, remote autocomplete, later pages and CAPTCHA integrations remain independent failure modes.
