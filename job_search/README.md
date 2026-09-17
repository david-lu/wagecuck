# wagecuck-search

An independent job-search package for Wellfound, Indeed, LinkedIn, and Simplify.
This directory can be copied and installed on its own. It does not import `wagecuck`,
read application profiles, use application browser sessions, or share application storage.
Its dependencies, entry point, configuration, output, and tests live here.

## Saved results

The [senior/staff software engineer job list](results/senior-staff-2026-09-16.csv)
contains 1,710 postings with validated employer or ATS application URLs.
See [run details and coverage](results/README.md) and the
[per-site summary](results/senior-staff-2026-09-16-summary.csv).
These exports are kept outside the ignored `runs/` directory so Git can track them.

## Install and run

From this directory, with Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\wagecuck-search.exe --job-title "software engineer" --seniority senior staff --output runs/senior-staff.json
```

An installed Chrome or Edge can also be used with `--browser-channel chrome` or
`--browser-channel msedge`. `--show-browser` makes the search browser visible.
`python -m wagecuck_search` is equivalent to the console command.
No model API key is required.

For remote senior/staff jobs in Canada, with a salary range reaching CAD 180,000/year:

```powershell
.venv\Scripts\wagecuck-search.exe --job-title "software engineer" --seniority senior staff --location Canada --workplace remote --min-salary 180000 --salary-currency CAD --salary-period year --no-internship --sponsors-visa --output runs/canada.json
```

## Inputs

Only `--job-title` is required.

| Flag | Meaning |
| --- | --- |
| `--seniority senior staff` | Accept either level. Also supports intern, junior, mid, principal, lead, manager. Levels in the requested title are inferred if omitted. |
| `--location Canada` | Repeat for alternative locations. Matches published location text, case-insensitively. |
| `--workplace remote` | Remote, hybrid, or onsite. `--location remote` also requests remote work. A country and remote together require both. |
| `--min-salary 180000` | Keep advertised ranges whose upper bound reaches this amount; a lone amount must reach it. This is not a guaranteed minimum offer. |
| `--salary-currency CAD` | Currency for the salary threshold; default USD. No exchange conversion. |
| `--salary-period year` | Year, month, week, day, hour; default year. No assumed annualization. |
| `--internship` / `--no-internship` | Require or exclude internships. Omitted means either. |
| `--sponsors-visa` / `--no-sponsors-visa` | Require an explicit positive or negative job-level sponsorship statement. Omitted means either. |
| `--employment-type full_time` | Also part_time, contract, temporary. |
| `--keyword Python` | Repeat for required title/description terms; all must match. |
| `--exclude-keyword clearance` | Repeat to reject title/description terms; any match excludes. |
| `--exclude-company "Acme"` | Repeat to exclude company names. |
| `--posted-within-days 14` | Use the posted date, independently of the last-updated date. |
| `--include-unknown` | Retain unknown optional filters and mark each unverified criterion in `note`. Known mismatches still fail. |
| `--sites wellfound linkedin` | Select a subset; default is all four sites. |
| `--max-pages 200` | Maximum search pages/batches per site; default 200. |
| `--max-per-site 10000` | Maximum posting records/URLs to process per site, before filtering; default 10,000. |
| `--timeout-seconds 30` | Timeout for each browser operation. |
| `--site-timeout-seconds 900` | Overall discovery/enrichment deadline per site; completed postings survive a timeout. |
| `--validation-timeout-seconds 60` | Deadline per matched job for resolving and validating its employer application URL. |
| `--validation-workers 8` | Concurrent HTTP-validation workers (1–64); default 8. Browser fallback is capped at 8. |
| `--detail-workers 4` | Concurrent detail-enrichment workers for batch discovery (1–8); default 4. |
| `--discovery-output runs/discovery.json` | Save public candidates before validation, for later reuse. This is not a verified result file. |
| `--resume-discovery runs/discovery.json` | Reuse a discovery checkpoint with the same title/filters/sites and rerun native-URL validation. |
| `--output runs/results.json` | Save the same JSON report printed to stdout. |

Unknown requested fields fail filters by default. A missing sponsorship statement does
not mean no sponsorship; a bare `$` does not establish USD; remote does not mean worldwide.
Country and city matching uses published text rather than a geocoder. For example, `Canada`
will not match a posting that only says `Toronto`. Use location variants as needed.

## Search process

1. Generalize the title. `Senior to Staff Software Engineer` becomes `software engineer`.
   Common backend/frontend/full-stack engineer searches also use this broad query, while
   preserving the requested specialization for subsequent title filtering.
2. Search each selected site's public board concurrently, in separate fresh browser contexts.
   Only the broad title is sent to the board. The page adapters read JobPosting JSON-LD
   and supported page markup. Simplify bootstraps from its own public UI search request,
   then paginates that same broad query in batches of up to 250 records, without visiting
   every posting. Detail pages are read only when requested filters require missing metadata.
   Public search credentials remain in memory and are never included in checkpoints or logs.
3. Deduplicate before filtering so one copy can supply metadata missing from another.
   Canonical posting URLs ignore tracking parameters and changing title slugs.
   Matching normalized company, complete title, and location also identifies probable duplicates.
   Senior and staff, different title specialties, and different locations stay separate.
   All source URLs are retained. Conflicting salary/sponsorship assertions become unknown.
   Earliest known posted date and latest known update date are retained across copies.
4. Apply title, seniority, and all requested filters. Explicitly expired postings are excluded.
   Seniority comes from the title or explicit board metadata; ambiguous numeric levels are
   not guessed. Company H1B history alone is not a sponsorship promise for a particular job.
5. Resolve each matched posting's Apply links and published application metadata. Follow
   redirects and verify that the final page loads successfully, identifies the role, belongs
   to the employer or an employer-branded ATS, and has an application entry point. Reject
   closed/expired postings, broken links, generic careers pages, blocked pages, and destinations
   that remain on a job board or social-media site. Employer domain evidence comes from the
   source posting; an arbitrary external page cannot establish its own employer identity.
6. Deduplicate again when different source postings resolve to the same application URL.
   Emit the report with the validated native URL in `url`, keeping original board links in
   `sources`. Validation is mandatory, including when `--include-unknown` is enabled.

Employer-hosted ATS pages (for example, Greenhouse, Lever, Ashby, or Workday) count as native
application destinations. Validation follows navigation only; it never fills or submits forms.
Pages whose employer identity or application entry point cannot be verified are excluded rather
than returned with a board URL. The pipeline completes its HTTP pass before starting a separate
browser pass for unresolved pages. HTTP concurrency follows `--validation-workers`, with at most
two HTTP requests to a given hostname at once; rendering uses at most eight isolated contexts.
HTTP-resolved URLs and employer provenance are retained for the browser pass. Time spent queued
between passes does not consume a candidate's validation budget, but active work in both passes
shares that budget. Both queues are bounded even for thousands of results.
The built-in runner also replaces its browser every 25 rendered candidates, after all checks
in the current batch finish. This releases accumulated renderer processes during long searches.
Redirects release the originating host's request slot before loading the next host, so a slow
employer does not occupy the job board's request slots. Browser checks finish once application
evidence is ready, with up to 2.5 seconds for JavaScript hydration instead of an unconditional
network-idle wait. Processing uses deterministic Python checks, with no per-job LLM calls.
Browser fallbacks use the URL already resolved by HTTP and skip image, media, and font downloads.
For Simplify UUID postings, the validator tries the published `/jobs/click/{posting_id}` Apply
redirect before loading the full board page. A destination on an ATS still has to identify the
correct employer and role. Custom employer domains retain the board-page provenance check.
An explicit [`directApply: true`](https://schema.org/directApply) on a matching, live JobPosting
is accepted as application-entry evidence only after native-domain and employer checks pass.

## Large searches and checkpoints

The default budgets permit thousands of candidates. Actual volume depends on site access,
pagination, title/filter selectivity, and native application URL validation. Simplify supports
batch discovery; the other boards still use their public search/detail pages and can be slower
or blocked. Higher budgets cannot make a blocked board accessible.

```powershell
.venv\Scripts\wagecuck-search.exe --job-title "software engineer" --seniority senior staff --sites simplify --max-per-site 10000 --discovery-output runs/discovery.json --output runs/verified.json
```

Progress on stderr separates discovered candidates from completed URL checks. Discovery is
saved before validation starts. To retry validation after an interruption or network problem:

```powershell
.venv\Scripts\wagecuck-search.exe --job-title "software engineer" --seniority senior staff --sites simplify --resume-discovery runs/discovery.json --output runs/verified.json
```

Resume avoids rediscovery and rechecks every matching native URL; it does not reuse old validation
successes. Keep the same filters and sites; worker counts, browser choice, deadlines, and budgets
may change. A discovery checkpoint contains unvalidated public posting data and must not be used
as the final application list. It is a snapshot, so rediscover when fresh jobs are needed.

On 2026-09-16, a live discovery benchmark collected **5,000 Simplify postings in 28 seconds**
using 20 batches, with zero detail-page visits. After 880 identity duplicates were merged,
2,032 senior/staff candidates passed local filters. These benchmark counts are **not validated
native application URLs**. An offline test also processes 5,000 results through the complete
pipeline using a fake validator, verifying bounded concurrency and report accounting.

Identity deduplication is a heuristic: two distinct openings with identical company/title/location
may be grouped, and differently worded reposts may remain separate. Retained source URLs make
these groups reviewable. Deduplication is within one run, not across previous runs.

## Output

Stdout is JSON containing `criteria`, `broad_query`, `jobs`, and `summary`. Each job always
has `url`, `title`, `company`, and `location`. `location: "Unknown"` explicitly represents an
unpublished location. Optional fields are omitted when unknown, preserving real `false` values.

```json
{
  "url": "https://careers.example.com/jobs/123-example",
  "url_validated_at": "2026-09-16T22:00:00+00:00",
  "application_url_type": "employer",
  "title": "Staff Software Engineer",
  "company": "Example Company",
  "location": "Remote; Canada",
  "salary": {"minimum": 180000, "maximum": 240000, "currency": "CAD", "period": "year", "text": null},
  "internship": false,
  "sponsors_visa": true,
  "posted_at": "2026-09-10T00:00:00Z",
  "last_updated": "2026-09-12T00:00:00Z",
  "workplace": "remote",
  "employment_type": "full_time",
  "sources": [{"site": "wellfound", "url": "https://wellfound.com/jobs/123-example"}]
}
```

This is a fictional schema example. `note` records missing data, conflicting source assertions,
or unverified requested filters. `experience_levels` appears when explicit board metadata exists.
Dates are ISO 8601 when supplied; posting dates are never relabeled as last-updated dates.
`url_validated_at` records the live validation time; `application_url_type` is `employer` or `ats`.
Validation establishes availability at that time, not a guarantee that a posting will remain open.

Every run includes all selected sites, including failed sites, in `summary.sites`:

- `discovered`: unique posting URLs found on visited search pages, including URLs beyond the visit budget.
- `fetched`: successfully extracted posting records before deduplication/filtering.
- `advertised_total`: broad match count reported by the source, when available; not a count we verified.
- `detail_visits`: extra posting-page visits during discovery/enrichment; bulk records need not incur a visit.
- `unavailable`: removed postings (HTTP 404/410), skipped while continuing the search.
- `deduplicated`: records from this site absorbed into an earlier record.
- `returned`: surviving records credited to this site; these counts sum to the total returned.
- `matched_with_duplicates`: returned groups with a source URL on this site; these counts can overlap.
- `validation_attempted`, `validation_rejected`: matched groups checked and excluded by URL validation,
  attributed to the group's first source site.
- `pages`, `limited`, `status`, `errors`: coverage and failures.

Total counts satisfy `fetched - discovery_deduplicated = unique_before_filtering` and
`unique_before_filtering - filtered_out - validation_rejected - application_url_deduplicated = returned`.
`deduplicated` includes both discovery duplicates and duplicates of validated native URLs.
`validation_rejections` records source URLs, company, title, and the failure reason for every
excluded matched group. Filter-reason counts can overlap because
one posting may fail several criteria. Repeated links inside one search page are ignored as
navigation duplicates and do not count as fetched posting records.

The same per-site counts and overall deduplication count are printed to stderr. A degraded
run still emits its complete report and exits with code 1. Healthy runs, including those
that reached configured budgets, exit 0; invalid arguments exit 2. `summary.partial` is
true when any site failed, reached a coverage limit, or a matched job failed URL validation.
URL validation failures also cause exit code 1, while preserving successfully validated results.

## Coverage and live access

These are bounded public-board searches, not an exhaustive crawl of every job on the internet.
Board ranking, default geographic markets, pagination, and the configured budgets affect coverage.
Increase budgets when broad results contain too few matches.

Sites may require login, rate-limit a session, return HTTP 403, or change their markup.
The adapters report these conditions instead of claiming there were no jobs. They do not
solve CAPTCHAs or reuse application sessions. A live check on 2026-09-16 successfully read
Wellfound, LinkedIn, and Simplify; Indeed returned HTTP 403 from this environment.

The page URLs used by these adapters can be inspected directly:
[Wellfound](https://wellfound.com/role/software-engineer),
[Indeed](https://www.indeed.com/jobs?q=software+engineer),
[LinkedIn](https://www.linkedin.com/jobs/search/?keywords=software+engineer), and
[Simplify](https://simplify.jobs/jobs).

## Tests and Python API

Run tests from this directory; they are independent of the application test suite:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src tests
```

Tests use senior-to-staff software engineer roles and deterministic public-page shapes,
with no network calls. Live access is checked separately by running the CLI with small budgets.

```python
import asyncio
from wagecuck_search import SearchCriteria, search

report = asyncio.run(search(SearchCriteria(
    job_title="software engineer",
    seniority=("senior", "staff"),
    locations=("Canada",),
    workplace="remote",
)))
```

For other providers, pass `providers=[...]` to `search`. Each provider exposes
`site` and `async fetch(broad_query, criteria) -> SiteResult`; it must return `JobPosting` records
and its coverage/error metadata. Providers can supply internal `application_urls` and `employer_urls`
discovered from the posting; these unvalidated hints are never emitted as final URLs.
The common search pipeline owns filtering, deduplication, URL validation, and reporting.
Offline tests also inject `validator=...` with `async validate(job) -> ValidationResult`.
Custom providers without a validator still use the real browser validator; validation is not skipped.
