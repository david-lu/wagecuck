# Job-search run scripts

These are reusable execution harnesses. Generated checkpoints, journals, logs,
and historical runs belong in `job_search/.artifacts/`; final CSV and JSON exports
belong in `job_search/results/`.

- `full_run.py` runs the broad senior/staff software-engineer search and writes its
  working data beneath `.artifacts/runs/`.
- `find_three_titles.py` runs the resumable software-engineer, frontend-engineer,
  and creative-technologist search from a user-owned JSON search profile. Pass
  `--profile profiles/los-angeles-or-remote.json` to select the current profile.
  It writes final stage exports to `results/` and keeps its validation journal in
  that result set's hidden `.artifacts/` folder.
- `run_validation.ps1` reads the profile's existing `02-filter.csv`, runs only URL
  validation, prints progress to the console, and saves the same stream under
  `.artifacts/console/`. It never reruns or overwrites the filter stage.
