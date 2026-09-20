"""Load deduplicated application targets from job-search outputs and corpora."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .evaluation import CorpusSplit
from .store import application_key


@dataclass(frozen=True)
class JobInput:
    id: str
    url: str
    title: str
    company: str
    source_file: str
    source_index: int

    def as_dict(self) -> dict:
        return asdict(self)


def _rows_from_json(path: Path) -> tuple[list[dict], CorpusSplit]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return payload, "run"
    if not isinstance(payload, dict):
        raise TypeError(f"JSON input must contain jobs, cases, or a list: {path}")
    if isinstance(payload.get("cases"), list):
        split = payload.get("split", "run")
        if split not in ("training", "validation", "run"):
            raise ValueError(f"Invalid dataset split in {path}: {split}")
        return payload["cases"], split
    if isinstance(payload.get("jobs"), list):
        return payload["jobs"], "run"
    if "url" in payload:
        return [payload], "run"
    raise ValueError(f"JSON input must contain jobs, cases, or a URL: {path}")


def _rows(path: Path) -> tuple[list[dict], CorpusSplit]:
    if not path.is_file():
        raise ValueError(f"Input file does not exist: {path}")
    if path.suffix.casefold() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream)), "run"
    if path.suffix.casefold() == ".json":
        return _rows_from_json(path)
    raise ValueError(f"Unsupported input type (expected CSV or JSON): {path}")


def _first_url(row: dict) -> str:
    for key in ("url", "application_url", "apply_url"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("application_urls", "application_urls_json"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = [value]
        if isinstance(value, list):
            for candidate in value:
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    return ""


def _public_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and not parts.username
        and not parts.password
    )


def _identifier(row: dict, path: Path, index: int) -> str:
    supplied = str(row.get("id") or "").strip()
    if supplied:
        return supplied
    label = "-".join(
        value for value in (str(row.get("company") or ""), str(row.get("title") or "")) if value
    )
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")[:80]
    return f"{path.stem}-{index:04d}" + (f"-{slug}" if slug else "")


def load_job_inputs(
    paths: list[Path],
    *,
    expected_split: CorpusSplit | None = None,
    limit: int | None = None,
) -> tuple[list[JobInput], CorpusSplit, int]:
    """Load every input before execution, rejecting malformed rows before any run starts."""
    if not paths:
        raise ValueError("At least one job-search CSV or JSON input is required.")
    loaded: list[JobInput] = []
    splits: set[CorpusSplit] = set()
    seen_urls: set[str] = set()
    seen_ids: dict[str, int] = {}
    duplicates = 0
    for path in paths:
        rows, split = _rows(path)
        if expected_split is not None and split != expected_split:
            raise ValueError(f"Expected {expected_split} input, got {split}: {path}")
        splits.add(split)
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise TypeError(f"Job row {index} is not an object: {path}")
            url = _first_url(row)
            if not _public_url(url):
                raise ValueError(f"Job row {index} has no public HTTP(S) URL: {path}")
            canonical = application_key(url, "batch-input")
            if canonical in seen_urls:
                duplicates += 1
                continue
            seen_urls.add(canonical)
            base_id = _identifier(row, path, index)
            seen_ids[base_id] = seen_ids.get(base_id, 0) + 1
            suffix = seen_ids[base_id]
            job_id = base_id if suffix == 1 else f"{base_id}-{suffix}"
            loaded.append(
                JobInput(
                    id=job_id,
                    url=url,
                    title=str(row.get("title") or ""),
                    company=str(row.get("company") or ""),
                    source_file=str(path.resolve()),
                    source_index=index,
                )
            )
    if len(splits) != 1:
        raise ValueError("A single run cannot mix training, validation, and ordinary inputs.")
    if not loaded:
        raise ValueError("The input files contain no application URLs.")
    if limit is not None:
        loaded = loaded[:limit]
    return loaded, splits.pop(), duplicates


def manifest_for(jobs: list[JobInput], split: CorpusSplit) -> dict:
    return {
        "schema_version": 1,
        "split": split,
        "cases": [job.as_dict() for job in jobs],
    }
