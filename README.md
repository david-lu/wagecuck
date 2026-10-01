# wagecuck

This repository contains three separate tools:

| Directory | Purpose |
| --- | --- |
| [filler](filler/README.md) | Playwright application filling, scripts, examples, and tests. |
| [chrome_filler](chrome_filler/README.md) | Chrome extension that drafts answers for individual fields. |
| [job_search](job_search/README.md) | Job discovery, filtering, and validation. |
| [profiles](profiles/README.md) | Shared dummy and actual profile directories for filling and search preferences. |

Run Python filler commands from `filler/` and job search commands from `job_search/`. Load `chrome_filler/` as an unpacked extension in Chrome. Each directory has its own setup and test instructions.

The existing root `.env` remains in place. The Playwright filler also accepts `filler/.env`, which takes precedence over root values. Real profile data is ignored by Git; generated runs and artifacts stay in their respective tool directories.
