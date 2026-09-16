"""Corpus loading and implementation fingerprints for reproducible evaluations."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar
from uuid import uuid4

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


Case = TypeVar("Case")
Result = TypeVar("Result")


MAX_CONCURRENCY = 8


def _check_concurrency(count: int) -> int:
    if (
        not isinstance(count, int) or isinstance(count, bool)
        or not 1 <= count <= MAX_CONCURRENCY
    ):
        raise ValueError(f"concurrency must be between 1 and {MAX_CONCURRENCY}")
    return count


def positive_concurrency(value: str) -> int:
    try:
        return _check_concurrency(int(value))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"concurrency must be an integer between 1 and {MAX_CONCURRENCY}"
        ) from exc


def add_concurrency_argument(parser) -> None:
    parser.add_argument(
        "--concurrency", type=positive_concurrency, default=1, metavar="N",
        help=f"Maximum active jobs, 1-{MAX_CONCURRENCY} (default: 1); excess jobs wait",
    )
    parser.add_argument(
        "--pool", action="store_true",
        help="Reuse a bounded pool of browsers; each job still gets a fresh context",
    )


async def run_cases(
    cases: Sequence[Case],
    worker: Callable[[Case], Awaitable[Result]],
    *,
    concurrency: int = 1,
    on_result: Callable[[int, Result], None] | None = None,
) -> list[Result]:
    """Run a bounded number of cases; collect completions on one event loop.

    Results retain input order. Workers turn expected case failures into results;
    unexpected failures cancel and await all remaining workers before returning.
    """
    _check_concurrency(concurrency)
    pending = iter(enumerate(cases))
    results: dict[int, Result] = {}

    async def consume():
        for index, case in pending:
            result = await worker(case)
            results[index] = result
            if on_result:
                on_result(index, result)

    async with asyncio.TaskGroup() as group:
        for _ in range(min(concurrency, len(cases))):
            group.create_task(consume())
    return [results[index] for index in range(len(cases))]


def default_report_path(prefix: str, *, directory: Path = Path("runs/reports")) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return directory / f"{prefix}-{stamp}-{uuid4().hex[:12]}.json"


def write_report(path: Path, payload: dict) -> None:
    """Publish a complete JSON snapshot; readers never see a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class BrowserPool:
    """A fixed number of reusable browser slots, scoped to one evaluation."""

    def __init__(self, browser_type, size: int):
        self.size = _check_concurrency(size)
        self.browser_type = browser_type
        self._available = asyncio.Queue(maxsize=size)
        self._browsers = []
        self._closed = True

    async def __aenter__(self):
        self._closed = False
        for _ in range(self.size):
            self._available.put_nowait(None)
        return self

    async def __aexit__(self, *_):
        self._closed = True
        await asyncio.gather(
            *(browser.close() for browser in self._browsers), return_exceptions=True
        )

    @asynccontextmanager
    async def lease(self):
        if self._closed:
            raise RuntimeError("Browser pool is not open")
        browser = await self._available.get()
        try:
            if browser is None or not browser.is_connected():
                browser = await self.browser_type.launch()
                self._browsers.append(browser)
            yield browser
        finally:
            if not self._closed:
                self._available.put_nowait(browser)
