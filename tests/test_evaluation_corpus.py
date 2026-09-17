from pathlib import Path

from wagecuck.evaluation import implementation_fingerprint, load_corpus

ROOT = Path(__file__).parents[1]


def test_training_and_validation_corpora_are_explicit_and_disjoint():
    training = load_corpus(ROOT / "examples" / "jobs.json", expected_split="training")
    validation = load_corpus(
        ROOT / "examples" / "validation-jobs.json", expected_split="validation"
    )
    assert len(training.cases) >= 100
    assert len(validation.cases) == 28
    imported = [case for case in training.cases if case["id"].startswith("csv-")]
    assert len(imported) >= 41
    assert len({case["company"] for case in imported}) >= 40
    assert len({case["source_platform"] for case in imported}) >= 10
    assert {"remote", "hybrid", "onsite"} <= {case["workplace"] for case in imported}
    assert all(
        case["source_dataset"] == "job_search/results/senior-staff-2026-09-16.csv"
        for case in imported
    )
    assert {case["id"] for case in training.cases}.isdisjoint(
        case["id"] for case in validation.cases
    )
    assert {case["url"] for case in training.cases}.isdisjoint(
        case["url"] for case in validation.cases
    )


def test_validation_posting_tokens_do_not_leak_into_behavior_or_regression_tests():
    validation = load_corpus(
        ROOT / "examples" / "validation-jobs.json", expected_split="validation"
    )
    scanned = [
        path
        for directory in (ROOT / "src" / "wagecuck", ROOT / "tests")
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".js", ".json"}
        and path != Path(__file__)
        and "__pycache__" not in path.parts
    ]
    source = "\n".join(path.read_text(encoding="utf-8").casefold() for path in scanned)
    leaked = {
        token
        for case in validation.cases
        for token in case["holdout_tokens"]
        if token.casefold() in source
    }
    assert not leaked


def test_implementation_fingerprint_changes_with_runtime_source(tmp_path):
    source = tmp_path / "src" / "wagecuck"
    source.mkdir(parents=True)
    module = source / "module.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    before = implementation_fingerprint(tmp_path)
    module.write_text("VALUE = 2\n", encoding="utf-8")
    assert implementation_fingerprint(tmp_path) != before
