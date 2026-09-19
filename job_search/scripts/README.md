# Python run scripts

Run these from `job_search/` using its installed virtual environment. Create one
directory per run so the CSV, JSON report, summary, and hidden artifacts stay
together:

```powershell
$py = ".venv\Scripts\python.exe"
$run = "results/software-engineer-$(Get-Date -Format 'yyyy-MM-dd-HHmmss')"
New-Item -ItemType Directory -Path $run | Out-Null

& $py scripts/run_search.py --output "$run/01-search.csv"
& $py scripts/run_preliminary_filter.py "$run/01-search.csv" --output "$run/02-preliminary.csv"
& $py scripts/run_validation.py "$run/02-preliminary.csv" --output "$run/03-validation.csv" --cache "$run/.artifacts/03-validation.urls.jsonl"
& $py scripts/run_final_filter.py "$run/03-validation.csv" --output "$run/04-selected.csv"
```

Each script runs one existing operation. Every output path is required; every CSV
operation also requires an explicit input. The block above is a copyable sequence,
but each line remains an independent operation and can use any input/output path.
Paths are relative to your current directory, except the bundled default JSON
definitions, which resolve beside the package's scripts.

| Script | Preset behavior | Overrides |
| --- | --- | --- |
| `run_search.py` | Search for software engineer jobs | `--query`, `--sites`, `--max-pages`, `--max-per-site` |
| `run_preliminary_filter.py` | Salary range reaches USD 180k/year; U.S.-eligible remote or Los Angeles metro | `--min-salary`, `--salary-basis`, `--salary-currency`, `--salary-period`, `--location-prompt` |
| `run_validation.py` | Verify URLs and generate languages, frameworks, role, is_backend, and us_authorization | `--fields`, `--validation-workers`, `--field-workers`, `--cache` |
| `run_final_filter.py` | Frontend/full_stack and all listed languages/frameworks fit an interpreted web stack | `--filters` and additional `--filter` conditions |

The preliminary filter retains unknown salary information by default; pass
`--no-include-unknown` to require known salary information in the selected currency
and period. Its location prompt requires evidence that a remote job accepts workers
located in the United States, or evidence of an LA-area location. Generic remote and
foreign-only remote listings do not match. Final filtering excludes unknown required
filter values by default.

All underlying operation options are available. Arguments supplied on the command
line override the script's preset scalar options:

```powershell
.venv\Scripts\python.exe scripts/run_search.py --query "frontend engineer" --sites a16z --max-pages 1 --max-per-site 6 --output custom-search.csv
.venv\Scripts\python.exe scripts/run_preliminary_filter.py jobs.csv --output local.csv --min-salary 200000 --no-include-unknown --location-prompt "Remote or Seattle"
.venv\Scripts\python.exe scripts/run_validation.py jobs.csv --output enriched.csv --fields my-fields.json
.venv\Scripts\python.exe scripts/run_final_filter.py enriched.csv --output selected.csv --filters my-filters.json
```

Use `--help` on any script for its operation's options. `OPENAI_API_KEY` is needed
for the preset prompt filters and field generation. Search and numeric filters
alone do not need a model key.

Exit codes are inherited from the shared CLI: 0 means completed, 1 means partial
results were saved, and 2 means invalid arguments/input. Reports and caches use the
same locations as the underlying commands.

`find_three_titles.py` remains a compatibility filename for the generic CLI.
`run_validation.ps1` is a separate PowerShell wrapper for generic validation.
`full_run.py` is the historical senior/staff export harness.
