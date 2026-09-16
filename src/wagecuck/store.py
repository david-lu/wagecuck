"""Durable submission fence. An uncertain commit is never retried automatically."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import ApplicationError, Code


def application_key(url: str, profile_id: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    if (host == "jobs.lever.co" or host.endswith(".lever.co")) and path.endswith("/apply"):
        path = path[:-6]
    if host == "jobs.ashbyhq.com" and path.endswith("/application"):
        path = path[:-12]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in ("gh_src", "lever-source", "source")
    ]
    canonical = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(sorted(query)),
            "",
        )
    )
    return hashlib.sha256(f"{profile_id}\n{canonical}".encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS applications (key TEXT PRIMARY KEY, run_id TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def claim(self, key: str, run_id: str):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM applications WHERE key=?", (key,)
            ).fetchone()
            if row and row[0] != "failed":
                code = {"succeeded": Code.ALREADY_SUBMITTED, "claimed": Code.RUN_IN_PROGRESS}.get(
                    row[0], Code.PRIOR_SUBMISSION_UNCERTAIN
                )
                raise ApplicationError(
                    code,
                    "This profile already has a submitted, active, or uncertain application for this URL.",
                )
            connection.execute(
                "INSERT INTO applications(key,run_id,state) VALUES (?,?,'claimed') ON CONFLICT(key) DO UPDATE SET run_id=excluded.run_id,state='claimed',updated_at=CURRENT_TIMESTAMP",
                (key, run_id),
            )

    def transition(self, key: str, run_id: str, state: str):
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE applications SET state=?,updated_at=CURRENT_TIMESTAMP WHERE key=? AND run_id=?",
                (state, key, run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Submission claim lost")
