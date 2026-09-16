"""Corpus loading and implementation fingerprints for reproducible evaluations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CorpusSplit = Literal["training", "validation"]


@dataclass(frozen=True)
class Corpus:
    split: CorpusSplit
    manifests: tuple[Path, ...]
    cases: tuple[dict, ...]


def load_corpus(path: Path, *, expected_split: CorpusSplit | None = None) -> Corpus:
    """Load one manifest plus its includes and reject mixed or duplicate datasets."""
    manifests: list[Path] = []
    cases: list[dict] = []
    active: set[Path] = set()

    def visit(current: Path, inherited_split: CorpusSplit | None) -> CorpusSplit:
        current = current.resolve()
        if current in active:
            raise ValueError(f"Corpus manifest include cycle at {current}")
        active.add(current)
        data = json.loads(current.read_text(encoding="utf-8"))
        split = data.get("split", inherited_split)
        if split not in ("training", "validation"):
            raise ValueError(f"Corpus manifest needs split=training|validation: {current}")
        if inherited_split and split != inherited_split:
            raise ValueError(f"Corpus manifest mixes {inherited_split} and {split}: {current}")
        manifests.append(current)
        for included in data.get("includes", []):
            visit(current.parent / included, split)
        cases.extend(data.get("cases", []))
        active.remove(current)
        return split

    split = visit(path, expected_split)
    if expected_split and split != expected_split:
        raise ValueError(f"Expected {expected_split} corpus, got {split}")
    ids = [case.get("id") for case in cases]
    urls = [case.get("url") for case in cases]
    if any(not value for value in ids + urls):
        raise ValueError("Every corpus case needs a nonempty id and url")
    if len(ids) != len(set(ids)):
        raise ValueError("Corpus contains duplicate case IDs")
    if len(urls) != len(set(urls)):
        raise ValueError("Corpus contains duplicate job URLs")
    return Corpus(split, tuple(manifests), tuple(cases))


def implementation_fingerprint(root: Path) -> str:
    """Hash runtime source so a holdout report identifies the exact evaluated code."""
    source = root / "src" / "wagecuck"
    digest = hashlib.sha256()
    for path in sorted(
        candidate
        for candidate in source.rglob("*")
        if candidate.is_file()
        and candidate.suffix in {".py", ".js", ".json"}
        and "__pycache__" not in candidate.parts
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
