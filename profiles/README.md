# Shared profiles

Each person has one directory with separate files for the two Python tools:

| File | Used by |
| --- | --- |
| `dummy/profile.json` | Playwright filler test runs. Generate it with `wagecuck demo-profile`. |
| `dummy/search.json` | Example job search preferences. |
| `actual/profile.json` | Your real application details and résumé path. |
| `actual/search.json` | Your job titles, sources, salary, and location preferences. |

The application and search files use different schemas. Keeping them together lets both tools select the same person without making job search read application details. Résumé and cover letter paths in `profile.json` are relative to that file.

Run the filler from `filler/` with `--profile dummy` or `--profile actual`. For job search, use `wagecuck-search search --profile actual --output jobs.csv`, then `wagecuck-search filter jobs.csv --profile actual --output selected.csv`. The search profile supplies a default query and filters; command line options can override them.

Files under `actual/` are ignored by Git. The real profile must contain truthful applicant details and a valid résumé before it can be used for live applications. The dummy application profile must remain synthetic; regenerate it with `wagecuck demo-profile` when needed.

The earlier file in the dummy directory mixed a personal email with fictional details. It is preserved as `actual/profile-draft.json`, with its résumé beside it. It is not selected by `--profile actual`. Review and replace the fictional details before saving a verified `actual/profile.json`. The private `actual/search.json` carries the existing software engineer, Los Angeles or eligible U.S. remote, USD 180k search preferences.
