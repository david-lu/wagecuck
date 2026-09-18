# wagecuck-search

An independent job-search package with 20 source adapters: Wellfound, Indeed,
LinkedIn, Simplify, HiringCafe, Jobright, Levels.fyi, TrueUp, Y Combinator,
Built In, TheirStack, JobShifu, MyGreenhouse, RoleSweep, Google Jobs,
VentureLoop, Remote Rocketship, BackchannelJobs, Hacker News Who Is Hiring, and a16z portfolio jobs.
This directory can be copied and installed on its own. It does not import `wagecuck`,
read application profiles, use application browser sessions, or share application storage.
Its dependencies, entry point, configuration, output, and tests live here.

## Saved results

The [senior/staff software engineer job list](results/senior-staff-2026-09-16.csv)
contains 1,710 postings with validated employer or ATS application URLs.
See [run details and coverage](results/README.md) and the
[per-site summary](results/senior-staff-2026-09-16-summary.csv).
These exports are kept outside the ignored `.artifacts/` directory so Git can track them.
Final CSV and JSON exports belong in `results/`; checkpoints, journals, criteria,
progress logs, diagnostics, and historical runs belong in `.artifacts/`.

## Install and run

From this directory, with Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\wagecuck-search.exe --job-title "software engineer" --seniority senior staff --output results/senior-staff.json
```

An installed Chrome or Edge can also be used with `--browser-channel chrome` or
`--browser-channel msedge`. `--show-browser` makes the search browser visible.
`python -m wagecuck_search` is equivalent to the console command.

Reusable searches can be stored as user-owned JSON profiles under `profiles/`.
The current Los Angeles-or-remote run uses
[`profiles/los-angeles-or-remote.json`](profiles/los-angeles-or-remote.json); its
titles, broad queries, result directory, sources, and post-generation filters are
configuration rather than runner code.

No model API key is required for search or URL validation. TheirStack is a paid
API source and is enabled when `THEIRSTACK_API_KEY` is present in the environment
or current directory's `.env`; its API charges one credit per returned job. Without
that key, the source reports `blocked` and consumes no credits. Natural-language
post-filtering uses the vocabulary classifier described below.

## Three-stage runs

Use `--stages-output-dir` when you want inspectable boundaries between discovery,
post-generation filtering, and native URL validation:

```powershell
.venv\Scripts\wagecuck-search.exe `
  --job-title "software engineer" `
  --seniority senior staff `
  --stages-output-dir results/senior-staff `
  --location-prompt "remote, in the Los Angeles area, or in OC" `
  --min-salary 180000 `
  --include-unknown
```

This writes:

| File | Contents |
| --- | --- |
| `01-search.csv` / `01-search.json` | Broad, deduplicated search results before URL validation or user filters |
| `02-filter.csv` / `02-filter.json` | Search results after joining partial-field decisions and applying local filters |
| `03-validation.csv` / `03-validation.json` | Live employer/ATS destinations, enriched with partial fields from the validated page |

The filter CSV preserves the search CSV's columns and retained values exactly; it
only removes nonmatching rows. Unique-location decisions and rejection diagnostics
are intermediate files under the run's hidden `.artifacts/` directory.

The location agent receives only the unique location vocabulary, the workplace
labels, and the location prompt. It returns a canonical location, match decision,
and short reason for every ID in bounded structured batches. The completed location
CSV is then joined back to all job rows. For example, 1,710 jobs in the checked-in
result currently collapse to 616 unique location/workplace values. Salary,
seniority, internship, sponsorship, employment type, keywords, company exclusions,
and posting age remain local deterministic filters and do not create agent calls.
If an agent batch times out, it is checkpointed as unclassified and its jobs are
kept; the summary and progress log report the number of skipped locations. Agent
responses have a 60-second read timeout, with 10-second connection and pool limits.

Set `OPENAI_API_KEY` in the environment or the current directory's `.env` when a
location prompt is used. Override the default model with
`WAGECUCK_SEARCH_AGENT_MODEL` or `--agent-model`. Location classification defaults to
`gpt-5.4-mini` for more reliable structured batches.

## Filter an existing generated CSV

Filtering can be repeated without searching or validating again:

```powershell
.venv\Scripts\wagecuck-search.exe `
  --post-filter-input results/senior-staff-2026-09-16.csv `
  --post-filter-output results/senior-staff-oc.csv `
  --location-prompt "in OC" `
  --min-salary 180000 `
  --include-unknown
```

This creates `senior-staff-oc.csv`, `senior-staff-oc.locations.csv`,
`senior-staff-oc.summary.json`, and `senior-staff-oc.rejections.csv`. Input and
output paths must differ, so the validated source list is preserved.

For remote senior/staff jobs in Canada, with a salary range reaching CAD 180,000/year:

```powershell
.venv\Scripts\wagecuck-search.exe --job-title "software engineer" --seniority senior staff --location Canada --workplace remote --min-salary 180000 --salary-currency CAD --salary-period year --no-internship --sponsors-visa --output results/canada.json
```

## Partial fields: locations, languages, frameworks, and custom attributes

Jobs carry a `partial_fields` object of named lists. CSVs display each custom field,
`programming_languages`, and `frameworks` as comma-separated columns. The
`partial_fields_json` column preserves exact lists, including locations such as
`["Los Angeles, CA", "Remote; Canada"]`. The original `location` column remains
compatible with existing CSVs; a comma between a city and state is not an item separator.

Languages and frameworks are extracted automatically from available descriptions during
search and again from the verified employer/ATS page during validation. Validation's
API shortcut also retains descriptions. Extraction records mentions, including
nice-to-have technologies; it does not assert that every item is required. Missing
evidence stays empty. Duplicate jobs retain the union of their partial fields.

Define any additional field with canonical terms and aliases, CSS selectors, or both.
See [examples/partial-fields.json](examples/partial-fields.json), which adds cloud
platforms and teams. CSS selectors apply when page HTML is available; term matching
also works on descriptions from search APIs. Names must be lowercase identifiers and
cannot overwrite existing job columns. Defining a built-in field replaces its term list.

```powershell
.venv\Scripts\wagecuck-search.exe --job-title "software engineer" --sites a16z --partial-fields examples/partial-fields.json --stages-output-dir results/with-partial-fields
```

Filter the enriched validation output:

```powershell
.venv\Scripts\wagecuck-search.exe --post-filter-input results/with-partial-fields/03-validation.csv --post-filter-output results/with-partial-fields/04-filter.csv --partial-filter "programming_languages=Python or TypeScript" --partial-filter "frameworks=React or Next.js"
```

For each requested field, the filter uniquifies its items, asks the model for one
boolean per unique item, and joins those decisions back to every job. Matching is
**any item within a field, all requested fields across a job**. For example, a job
with `Python,Java` and `React` passes the command above. Empty fields fail unless
`--include-unknown` is set. `--partial-filter "location=Los Angeles or remote"`
uses the same mechanism; existing `--location-prompt` remains supported.

Classification requires `OPENAI_API_KEY` and uses the existing agent model,
bounded batches, and concurrency limit. Reviewable decisions, counts, and reasons
are saved in `.artifacts/04-filter.partial-fields/<field>.csv`. Decisions are
cached by field, item/context, and prompt. Timeout batches are marked
`Unclassified`, kept under the existing timeout policy, and retried next run.
Changing a filter prompt invalidates its cached decisions.

The stages remain search -> filter -> validation. To use fields discovered only
during validation, run filtering on `03-validation.csv` as shown above. Existing
CSV files are not automatically enriched. For the saved three-title search,
`.venv\Scripts\python.exe scripts/find_three_titles.py --validation-only` refreshes
validation fields; older validation cache entries are refreshed automatically.

In a saved search profile, top-level `partial_fields` holds extraction definitions,
and `filters.partial_fields` maps field names to natural-language predicates:

```json
{
  "partial_fields": {
    "cloud_platforms": {
      "terms": {"AWS": ["Amazon Web Services"], "GCP": ["Google Cloud"]}
    }
  },
  "filters": {
    "partial_fields": {
      "programming_languages": "Python or TypeScript",
      "cloud_platforms": "AWS"
    }
  }
}
```

This is a profile fragment; retain the profile's titles, queries, and output directory.
Python providers can directly populate `JobPosting(partial_fields={"teams": ["Platform"]}, ...)`,
and validators can return additional values in `ValidationResult.partial_fields`.

## Inputs

Only `--job-title` is required.

| Flag | Meaning |
| --- | --- |
| `--seniority senior staff` | Accept either level. Also supports intern, junior, mid, principal, lead, manager. Levels in the requested title are inferred if omitted. |
| `--location Canada` | Repeat for alternative locations. Matches published location text, case-insensitively. |
| `--workplace remote` | Remote, hybrid, or onsite. `--location remote` also requests remote work. A country and remote together require both. |
| `--min-salary 180000` | Keep advertised ranges whose upper bound reaches this amount; a lone amount must reach it. This is not a guaranteed minimum offer. |
| `--max-salary 250000` | During staged/post-filter runs, reject a known range whose lower bound exceeds this amount. |
| `--salary-currency CAD` | Currency for the salary threshold; default USD. No exchange conversion. |
| `--salary-period year` | Year, month, week, day, hour; default year. No assumed annualization. |
| `--internship` / `--no-internship` | Require or exclude internships. Omitted means either. |
| `--sponsors-visa` / `--no-sponsors-visa` | Require an explicit positive or negative job-level sponsorship statement. Omitted means either. |
| `--employment-type full_time` | Also part_time, contract, temporary. |
| `--partial-fields examples/partial-fields.json` | Custom term/alias or CSS extraction definitions for search and validation. |
| `--partial-filter "frameworks=React or Next.js"` | Filter unique items, then join booleans to jobs; repeat for different fields. Staged/post-filter runs only. |
| `--keyword Python` | Repeat for required title/description terms; all must match. |
| `--exclude-keyword clearance` | Repeat to reject title/description terms; any match excludes. |
| `--exclude-company "Acme"` | Repeat to exclude company names. |
| `--posted-within-days 14` | Use the posted date, independently of the last-updated date. |
| `--include-unknown` | Retain unknown optional filters and mark each unverified criterion in `note`. Known mismatches still fail. |
| `--sites wellfound linkedin` | Select a subset; default is all 20 sites. |
| `--max-pages 200` | Maximum search pages/batches per site; default 200. |
| `--max-per-site 10000` | Maximum posting records/URLs to process per site, before filtering; default 10,000. |
| `--timeout-seconds 30` | Timeout for each browser operation. |
| `--site-timeout-seconds 900` | Overall discovery/enrichment deadline per site; completed postings survive a timeout. |
| `--validation-timeout-seconds 60` | Deadline per matched job for resolving and validating its employer application URL. |
| `--validation-workers 8` | Concurrent HTTP-validation workers (1–64); default 8. Browser fallback is capped at 8. |
| `--detail-workers 4` | Concurrent detail-enrichment workers for batch discovery (1–8); default 4. |
| `--discovery-output .artifacts/01-search.checkpoint.json` | Save public candidates before validation, for later reuse. This is not a verified result file. |
| `--resume-discovery .artifacts/01-search.checkpoint.json` | Reuse a discovery checkpoint with the same title/filters/sites and rerun native-URL validation. |
| `--output results/results.json` | Save the same JSON report printed to stdout. |

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
   Levels.fyi reads its public result records in bulk and keeps the application URL attached
   to that exact job ID, avoiding unrelated Apply links embedded elsewhere on the page.
   RoleSweep and Remote Rocketship use their public paginated result and detail pages.
   BackchannelJobs uses its public bulk search response, while Hacker News searches the
   current Who Is Hiring thread in batches. TheirStack uses its authenticated JSON API.
   The remaining page adapters enrich detail pages with the bounded `--detail-workers` pool
   instead of serially waiting on every posting.
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
6. After every filtered row has been validated, deduplicate one final time by resolved native
   application URL. Emit that URL in `url`, keeping all original board links in `sources`.
   Validation is mandatory, including when `--include-unknown` is enabled.

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
pagination, title/filter selectivity, and native application URL validation. Simplify,
TheirStack, BackchannelJobs, and Hacker News support batch discovery; page boards can be slower
or blocked. Higher budgets cannot make a blocked board accessible.

```powershell
.venv\Scripts\wagecuck-search.exe --job-title "software engineer" --seniority senior staff --sites simplify --max-per-site 10000 --discovery-output .artifacts/01-search.checkpoint.json --output results/03-validation.json
```

Progress on stderr separates discovered candidates from completed URL checks. Discovery is
saved before validation starts. To retry validation after an interruption or network problem:

```powershell
.venv\Scripts\wagecuck-search.exe --job-title "software engineer" --seniority senior staff --sites simplify --resume-discovery .artifacts/01-search.checkpoint.json --output results/03-validation.json
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

Search counts satisfy `fetched - discovery_deduplicated = unique_before_filtering`. Final counts
satisfy `unique_before_filtering - filtered_out - validation_rejected - application_url_deduplicated = returned`.
Filtering never merges records. Deduplication occurs during search and once more at
the very end of validation, when native application URL identity is available.
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
Wellfound, LinkedIn, and Simplify; Indeed returned HTTP 403 from this environment. A later
live check successfully extracted current jobs from Jobright, Levels.fyi, Y Combinator, and
Built In. HiringCafe and TrueUp returned HTTP 403 security challenges from this environment,
which their adapters report as `blocked` instead of silently returning zero jobs.

The new sources are grouped by the requested coverage waves:

| Wave | Sources | Current adapter behavior |
| --- | --- | --- |
| 1 | Simplify, HiringCafe, TheirStack, JobShifu, TrueUp, Jobright, MyGreenhouse, RoleSweep | Public page/API search where available. TheirStack reads `THEIRSTACK_API_KEY`; JobShifu and MyGreenhouse explicitly report their account gate. |
| 2 | LinkedIn, Indeed, Google Jobs, VentureLoop, Wellfound, YC, Built In, Remote Rocketship, BackchannelJobs, HN | Public page/feed search where available. VentureLoop reports its anonymous preview count but does not emit hidden-company records; Google reports its verification challenge. |

The a16z portfolio board is enabled by default as source `a16z` (also selectable with
`--sites a16z`). It searches by title, follows Show more jobs, reads posting details,
and carries employer Apply links into the existing native-URL validation stage.
Search profiles that omit an explicit sites list include it automatically.

The existing Levels.fyi adapter remains enabled as an additional source. On 2026-09-17,
a bounded live smoke check extracted records from RoleSweep, Remote Rocketship,
BackchannelJobs, and the current Hacker News thread. VentureLoop reported 6,330 broad
matches but withheld company and application details from anonymous access. The smoke check
also confirmed the explicit credential/account/challenge statuses for TheirStack, JobShifu,
MyGreenhouse, and Google Jobs.

The page URLs used by these adapters can be inspected directly:
[Simplify](https://simplify.jobs/jobs),
[HiringCafe](https://hiring.cafe/jobs/software-engineer),
[LinkedIn](https://www.linkedin.com/jobs/search/?keywords=software+engineer),
[Indeed](https://www.indeed.com/jobs?q=software+engineer),
[Jobright](https://jobright.ai/jobs/software-engineer-jobs-in-united-states),
[Levels.fyi](https://www.levels.fyi/jobs/title/software-engineer),
[TrueUp](https://www.trueup.io/engineering),
[Wellfound](https://wellfound.com/role/software-engineer),
[Y Combinator](https://www.ycombinator.com/jobs?query=software%20engineer),
[Built In](https://builtin.com/jobs/dev-engineering?search=software%20engineer),
[TheirStack API](https://theirstack.com/en/docs/api-reference/jobs/search_jobs_v1),
[JobShifu](https://app.jobshifu.com/),
[MyGreenhouse](https://my.greenhouse.com/),
[RoleSweep](https://rolesweep.com/jobs?keyword=software%20engineer),
[Google Jobs](https://www.google.com/search?q=software+engineer+jobs),
[VentureLoop](https://ventureloop.com/jobs?query=software%20engineer),
[Remote Rocketship](https://www.remoterocketship.com/jobs/software-engineer/),
[BackchannelJobs](https://www.backchanneljobs.com/),
[Hacker News Who Is Hiring](https://news.ycombinator.com/ask), and
[a16z portfolio jobs](https://jobs.a16z.com/jobs).

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
The common search pipeline owns search deduplication, filtering, URL validation, final native-URL
deduplication, and reporting.
Offline tests also inject `validator=...` with `async validate(job) -> ValidationResult`.
Custom providers without a validator still use the real browser validator; validation is not skipped.
