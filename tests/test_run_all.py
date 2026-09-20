import csv
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from wagecuck.job_inputs import JobInput, load_job_inputs
from wagecuck.models import ApplicationResult, Code


def run_all_script():
    spec = importlib.util.spec_from_file_location(
        "run_all_test", Path(__file__).parents[1] / "scripts" / "run_all.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_job_search_csv_and_json_inputs_are_combined_and_deduplicated(tmp_path):
    csv_path = tmp_path / "selected.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("url", "title", "company"))
        writer.writeheader()
        writer.writerow(
            {
                "url": "https://jobs.example.test/one?utm_source=search",
                "title": "Frontend Engineer",
                "company": "Example",
            }
        )
        writer.writerow(
            {
                "url": "https://jobs.example.test/one",
                "title": "Duplicate",
                "company": "Example",
            }
        )
    json_path = tmp_path / "selected.json"
    json_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "url": "https://jobs.example.test/two",
                        "title": "Design Engineer",
                        "company": "Second",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    jobs, split, duplicates = load_job_inputs([csv_path, json_path])

    assert split == "run"
    assert duplicates == 1
    assert [job.url for job in jobs] == [
        "https://jobs.example.test/one?utm_source=search",
        "https://jobs.example.test/two",
    ]
    assert jobs[0].title == "Frontend Engineer"
    assert jobs[1].source_file == str(json_path.resolve())


async def test_live_batch_persists_results_in_input_order(tmp_path, profile, monkeypatch):
    module = run_all_script()
    jobs = [
        JobInput(
            id=f"job-{index}",
            url=f"https://jobs.example.test/{index}",
            title=f"Role {index}",
            company="Example",
            source_file=str(tmp_path / "jobs.csv"),
            source_index=index,
        )
        for index in (1, 2)
    ]
    output = tmp_path / "batch.json"
    args = SimpleNamespace(
        mode="fill",
        headed=False,
        agent_fill=False,
        slow_mo=0,
        timeout=120,
        artifacts=tmp_path / "artifacts",
        database=tmp_path / "applications.sqlite3",
        storage_state=None,
        sensitive_artifacts=False,
        inputs=[tmp_path / "jobs.csv"],
        concurrency=2,
        pool=False,
    )

    class Runner:
        def __init__(self, agent):
            assert agent is None

        async def run(self, url, run_profile, options, *, browser=None):
            index = int(url.rsplit("/", 1)[-1])
            return ApplicationResult(
                run_id=f"run-{index}",
                profile_id=run_profile.id,
                job_url=url,
                mode=options.mode,
                status="ready",
                code=Code.READY,
                started_at="start",
                finished_at="finish",
            )

    monkeypatch.setattr(module, "ApplicationRunner", Runner)
    monkeypatch.setattr(module, "create_agent", lambda args: None)

    report = await module.run_live(jobs, profile, args, output, duplicates=0)

    stored = json.loads(output.read_text(encoding="utf-8"))
    assert report["completed"] == 2
    assert [row["run_id"] for row in stored["results"]] == ["run-1", "run-2"]
    assert [row["input"]["title"] for row in stored["results"]] == ["Role 1", "Role 2"]
    assert module.outcome_summary(stored)["passed"] == 2
