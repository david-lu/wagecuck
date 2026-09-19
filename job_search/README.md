# wagecuck-search

Independent job discovery, CSV filtering, URL validation, and prompt-based field
generation. Each command reads the paths you supply and writes the output you
choose. Operations can be repeated or combined in any order.

## Setup

From `job_search/`, with Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\Activate.ps1
```

After activation, use `wagecuck-search` below. Without activation, use
`.venv\Scripts\wagecuck-search.exe`. `python -m wagecuck_search` is equivalent.

Search and URL validation do not require a model key. Numeric filters run locally without a model. Prompt-based filtering and
field generation use `OPENAI_API_KEY` from the environment or `.env` in this
directory or its parent. The model defaults to `gpt-5.4-mini`; override with
`--agent-model` or `WAGECUCK_SEARCH_AGENT_MODEL`.

## Commands

| Command | Input | Output |
| --- | --- | --- |
| `search` | A query and selected sources | Discovered jobs CSV |
| `filter` | Any job CSV and field predicates | Matching rows, with original cells preserved |
| `validate` | Any job CSV | Jobs with verified employer/ATS URLs and page evidence |
| `fill-fields` | Any job CSV and field definitions | The same rows with generated scalar or array fields |

Every command requires `--output`. CSV operations require distinct input and output
paths. There are no reserved filenames, workflow profiles, or prescribed operation
order. Use `wagecuck-search COMMAND --help` for all options.

Search broadly:

```powershell
wagecuck-search search --query "software engineer" --output jobs.csv
```

For a small search, add `--sites a16z --max-pages 1 --max-per-site 6`. Use
`--show-browser` to see navigation, or `--browser-channel chrome` to use installed
Chrome. Search deduplicates discovered jobs but does not validate application
destinations or automatically infer technology fields from search snippets.
The default search and preliminary outputs have no language/framework fields.
The validation script sends the actual employer/ATS page description and each
requested field's type and prompt to the agent. There is no keyword-based
technology extractor. Filtering only selects rows and preserves existing fields.
Explicitly requesting `--fields FILE` on search generates fields from discovery
descriptions; omit it to defer enrichment until page validation.

## Python scripts

The [run scripts](scripts/README.md) provide separate Python entry points for
search, preliminary filtering, validation with field generation, and final
filtering. They supply the example defaults while keeping input/output paths
explicit. Pass CLI options to override those defaults.

## Field types

| Type | Value | Filtering |
| --- | --- | --- |
| `number_field` | A finite number or null | Local `>`, `>=`, `<`, `<=`, `==`, `!=` comparisons |
| `array_field` | A list of strings | Prompt per unique item, then any/all/none matching |
| `string_field` | Text or null; optional allowed `options` | Prompt on the complete string |
| `boolean_field` | True, false, or null | Existing boolean fields remain supported |

`array_field` replaces the old "partial field" terminology. Generated fields carry
explicit types in `field_types_json`; arrays are stored in `array_fields_json`.
Old `partial_fields_json` CSVs and legacy generation type names are accepted on input.

Numeric comparisons never call a model and do not need an API key:

```powershell
wagecuck-search filter input.csv --output selected.csv --filter salary_maximum ">=180000"
wagecuck-search filter input.csv --output range.csv --filter score ">200" --filter score "<=400"
```

Repeated comparisons on the same number field are ANDed. Comparisons accept signed
integers, decimals, and scientific notation. Prompts and array matching modes are
rejected for number fields. Quote comparisons so the shell does not interpret
`>` or `<` as redirection.

For an imported CSV, you can declare a column explicitly:

```powershell
wagecuck-search filter input.csv --output selected.csv --field-type score number_field --filter score ">200"
```

Without type metadata, numeric cells are inferred as numbers, boolean cells as
booleans, and other scalar cells as strings. Use `--field-type code string_field`
for numeric-looking identifiers whose exact text must be preserved. Empty cells
stay unknown; `--include-unknown` retains them. Supplied types must agree with
saved metadata. Filtering preserves source cells; repeat import declarations if
the source lacks saved type metadata.

## Filter any field

Supply the field name and its prompt as separate arguments:

```powershell
wagecuck-search filter input.csv --output selected.csv --filter role "Is this frontend or full_stack?"
wagecuck-search filter input.csv --output backend-languages.csv --filter languages "Is this language commonly used for backend development?"
```

For a string or boolean field, the prompt classifies its single value. For an array field,
each comma-separated item is classified once per unique value/context and prompt,
then decisions are joined back to the rows.

| Option | Keep a row when |
| --- | --- |
| `--filter FIELD "prompt"` | Any item matches |
| `--filter-all FIELD "prompt"` | Every item matches |
| `--filter-none FIELD "prompt"` | No item matches |

Repeat options to add filters; **all field conditions must pass**:

```powershell
wagecuck-search filter enriched.csv --output web.csv --filter role "Is this frontend or full_stack?" --filter-all languages "Is this JavaScript, TypeScript, Python, Ruby, PHP, SQL or shell scripting?" --filter-all frameworks "Is this a framework or runtime for an interpreted web application stack?"
```

Fields are arbitrary: `role`, `is_backend`, `title`, `company`, `salary.maximum`,
`languages`, `frameworks`, or a custom field you added. Field names use lowercase
identifiers; nested built-in fields use dotted paths. CSV salary column names such
as `salary_maximum` also work. Missing column names produce an error before model
requests. Missing cell values and unclassified answers fail the filter unless
`--include-unknown` is set. Boolean `false` and numeric `0` are real values.

Declare custom list columns from another CSV with `--array-column`:

```powershell
wagecuck-search filter input.csv --output cloud.csv --array-column cloud_platforms --filter cloud_platforms "Is this AWS or GCP?"
```

`languages`, `programming_languages`, and `frameworks` are recognized as list columns.
Our exported CSVs retain values and types in `fields_json`,
`array_fields_json`, and `field_types_json`. Other custom columns remain scalar; text containing commas stays a single string. Location preserves city/state combinations as one value.

Reusable predicates can also live in a JSON file:

```json
{
  "role": {"prompt": "Is this frontend or full_stack?", "mode": "any"},
  "languages": {"prompt": "Is this C, C++, Go, Rust, or another systems language?", "mode": "none"},
  "is_backend": {"prompt": "Is this false?", "mode": "any"}
}
```

```powershell
wagecuck-search filter input.csv --output selected.csv --filters my-filters.json
```

[examples/web-filters.json](examples/web-filters.json) contains the requested
frontend/full-stack and interpreted-web-stack predicates. Add other field filters
on the command line alongside `--filters`. A string/array field may have one prompt; combine its conditions in that prompt.
A number field can have several comparison expressions, written as a JSON list.

Salary and location filtering can be applied to any job CSV:

```powershell
wagecuck-search filter jobs.csv --output local-or-remote.csv --min-salary 180000 --salary-basis maximum --salary-currency USD --salary-period year --include-unknown --filter location "Is this remote or in the Los Angeles metropolitan area?"
```

This retains a salary range whose upper bound reaches $180k, and allows unknown
salary/location values. Use `--salary-basis minimum` to require the advertised
floor, and omit `--include-unknown` to exclude unknowns. There is no currency
conversion or assumed annualization.

## Validate and generate fields

Validate URLs and optionally generate fields from the actual job page:

```powershell
wagecuck-search validate input.csv --output checked.csv --fields examples/validation-fields.json
```

Without `--fields`, validation performs deterministic URL/page checks. It verifies
the employer and role, an application entry point, and that the listing is live;
it rejects broken, closed, blocked, or unverifiable destinations. It follows links
but never fills or submits applications.

Generate fields from existing CSV descriptions and metadata without browsing:

```powershell
wagecuck-search fill-fields input.csv --output enriched.csv --fields examples/validation-fields.json
```

Every added field requires a type and a nonempty prompt. The agent receives all
requested definitions with the job evidence and returns typed values with evidence;
no additional fields are created by keyword lists. This works for arbitrary scalar
and array fields:

```json
{
  "languages": {"type": "array_field", "prompt": "Programming languages required for the job"},
  "frameworks": {"type": "array_field", "prompt": "Software frameworks required for the job"},
  "role": {
    "type": "string_field",
    "options": ["backend", "frontend", "full_stack"],
    "prompt": "Classify the role from its primary responsibilities."
  },
  "is_backend": {"type": "boolean_field", "prompt": "Does this role involve material backend development?"}
}
```

Supported types are `array_field`, `string_field`, `number_field`, and `boolean_field`.
Constrain a `string_field` to an enum by supplying `options`.
Unknown scalar answers are null; unknown lists are empty. Populated values include
their definitions (including prompts) and evidence in `enrichment_json`.
The parsed job description is internal evidence for the agent and is not exported
as a CSV column or included in the adjacent public JSON report.
The old `--array-fields` flag aliases `--fields` and now requires the same
type/prompt format; terms/selectors definitions are rejected. Failed generation
is logged and retried on the next run. Generated names cannot replace core job identity columns.

Filling uses four concurrent workers by default, capped at eight
(`--field-workers`). URL validation has its own bounded pool
(`--validation-workers`). `validate --cache PATH` optionally reuses a validation
journal; otherwise URLs are checked afresh.

## Files and reports

CSV inputs need `url`, `title`, and `company` columns; `location` is optional.
Filter output preserves column order and accepted cell values, including separate
rows that happen to share the same URL.

Each command writes the specified CSV plus adjacent `.json` and `.summary.json`
reports. Per-value boolean decisions/reasons, rejection lists, and caches are
stored under `.artifacts/` beside the output. Changed prompts invalidate their
cached decisions. Unknown classifications are retried rather than cached as
successful decisions.

Summaries go to stdout; progress goes to stderr. Exit code 0 means the requested
operation completed, including when no rows match. Exit code 1 means partial or
failed work; saved output remains available. Invalid arguments/input produce
exit code 2. A completed search can contain unvalidated destinations; use
`validate` when verified application URLs are needed.

Saved exports are under [results/](results/README.md). The search package is
independent of the application package and does not read application profiles
or use its browser sessions.

## Python and tests

```python
from wagecuck_search import SearchCriteria, filter_csv, validate_csv, fill_fields_csv

await filter_csv("input.csv", "selected.csv", SearchCriteria(
    "generated jobs",
    field_filters={"role": {"prompt": "Is this frontend or full_stack?", "mode": "any"}},
))
await validate_csv("any-jobs.csv", "checked.csv", field_definitions=definitions)
await fill_fields_csv("descriptions.csv", "enriched.csv", definitions)
```

In-memory equivalents are `discover`, `filter_jobs`, `validate_jobs`, and `fill_fields`.
The older combined `search()` Python API and flag-only search CLI remain available
for compatibility; the explicit `search` command performs discovery only.

```powershell
python -m pytest -q
python -m ruff check src tests
```

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
and carries employer Apply links into the standalone URL validator.
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
