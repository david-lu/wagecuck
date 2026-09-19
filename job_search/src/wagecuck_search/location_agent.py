"""Classify the unique location vocabulary once, then join it back to jobs."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .array_fields import field_values
from .export import write_table

LOCATION_COLUMNS = [
    "filter_prompt", "location_id", "raw_location", "workplace", "job_count",
    "canonical_location", "matches", "reason",
]


class IncompleteLocationResponse(RuntimeError):
    pass


class InvalidLocationResponse(RuntimeError):
    pass


class LocationBatchTimeout(RuntimeError):
    pass


def location_id(location, workplace):
    value = f"{location.strip()}\0{(workplace or '').strip()}".encode()
    return hashlib.sha256(value).hexdigest()[:16]


def unique_locations(jobs):
    counts = Counter((value, (job.get("workplace") or "").strip())
                     for job in jobs for value in field_values(job, "location"))
    return [{
        "filter_prompt": "",
        "location_id": location_id(location, workplace),
        "raw_location": location,
        "workplace": workplace,
        "job_count": count,
        "canonical_location": "",
        "matches": "",
        "reason": "",
    } for (location, workplace), count in sorted(counts.items())]


def write_location_input(path, jobs):
    rows = unique_locations(jobs)
    write_table(path, LOCATION_COLUMNS, rows)
    return rows


class OpenAILocationAgent:
    """Structured, bounded batches over the unique-location table."""

    agent_label = "Location agent"
    result_key = "locations"
    id_key = "location_id"
    raw_key = "raw_location"
    canonical_key = "canonical_location"
    value_key = "location"
    context_key = "workplace"
    request_key = "location_request"
    schema_name = "location_filter"
    instructions = """You classify job locations against a user's natural-language location request.
Return exactly one result for every supplied location_id and preserve every ID exactly.
Interpret ordinary geographic language intelligently: abbreviations, misspellings, metro areas,
counties, nearby cities, and remote eligibility. OC means Orange County, California when the
context does not identify a different Orange County. 'Los Angeles area' means the commonly
understood LA metro/commuting area. A remote job matches a remote request; country-limited remote
jobs only match when that country is allowed. Multiple listed locations match if any listed option
matches. Canonicalize equivalent places consistently (for example SF CA and San Francisco,
California). Use the supplied workplace field as evidence. Do not infer missing remote eligibility.
Keep each reason short and factual."""

    def __init__(self, model=None, endpoint=None, api_key=None, timeout=60, transport=None,
                 batch_size=100, concurrency=8):
        load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)
        load_dotenv(Path.cwd().parent / ".env", override=False, interpolate=False)
        self.model = model or os.environ.get("WAGECUCK_SEARCH_AGENT_MODEL", "gpt-5.4-mini")
        self.endpoint = (endpoint or os.environ.get(
            "WAGECUCK_SEARCH_AGENT_ENDPOINT", "https://api.openai.com/v1"
        )).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("Location filtering requires OPENAI_API_KEY in the environment or .env")
        if timeout <= 0:
            raise ValueError("agent timeout must be positive")
        if batch_size <= 0 or concurrency <= 0:
            raise ValueError("batch_size and concurrency must be positive")
        self.timeout = timeout
        self.transport = transport
        self.batch_size = batch_size
        self.concurrency = concurrency
        self.request_count = 0

    async def _classify_batch(self, client, rows, prompt):
        ids = [row[self.id_key] for row in rows]
        schema = {
            "type": "object",
            "properties": {self.result_key: {"type": "array", "minItems": len(rows),
                "maxItems": len(rows), "items": {"type": "object", "properties": {
                    self.id_key: {"type": "string", "enum": ids},
                    self.canonical_key: {"type": "string"},
                    "matches": {"type": "boolean"},
                    "reason": {"type": "string"},
                }, "required": [self.id_key, self.canonical_key, "matches", "reason"],
                    "additionalProperties": False}}},
            "required": [self.result_key], "additionalProperties": False,
        }
        compact = [{self.id_key: row[self.id_key],
                    self.value_key: row[self.raw_key],
                    self.context_key: row[self.context_key]} for row in rows]
        instructions = self.instructions
        body = {
            "model": self.model, "store": False,
            "max_output_tokens": min(64000, max(8000, len(rows) * 160)),
            "reasoning": {"effort": "low"},
            "instructions": instructions,
            "input": json.dumps({self.request_key: prompt, self.result_key: compact,
                                 "field_name": rows[0].get("field_name", "location")},
                                ensure_ascii=False),
            "text": {"format": {"type": "json_schema", "name": self.schema_name,
                                  "strict": True, "schema": schema}},
        }
        result = (await self.structured_response(client, body)).get(self.result_key)
        if not isinstance(result, list):
            raise InvalidLocationResponse("Location agent returned a malformed location list")
        by_id = {}
        for item in result:
            try:
                key = item[self.id_key]
            except (KeyError, TypeError) as exc:
                raise InvalidLocationResponse(
                    "Location agent returned a location without an ID"
                ) from exc
            if key not in ids or key in by_id:
                raise InvalidLocationResponse(
                    "Location agent returned an unknown or duplicate location ID"
                )
            if (
                not isinstance(item.get("matches"), bool)
                or not isinstance(item.get(self.canonical_key), str)
                or not isinstance(item.get("reason"), str)
            ):
                raise InvalidLocationResponse("Agent decisions require a boolean and text evidence")
            by_id[key] = {name: item[name] for name in (
                self.id_key, self.canonical_key, "matches", "reason"
            )}
        if set(by_id) != set(ids):
            raise InvalidLocationResponse("Location agent omitted one or more locations")
        return [{**row, **by_id[row[self.id_key]]} for row in rows]

    async def structured_response(self, client, body):
        response = None
        for attempt in range(4):
            self.request_count += 1
            try:
                response = await client.post(
                    self.endpoint + "/responses",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
            except httpx.TimeoutException as exc:
                raise LocationBatchTimeout("Location agent batch timed out") from exc
            except httpx.TransportError as exc:
                if attempt == 3:
                    raise RuntimeError("Location agent failed: network") from exc
                await asyncio.sleep(2 ** attempt)
                continue
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                break
            retry_after = response.headers.get("retry-after")
            try:
                delay = min(30.0, max(1.0, float(retry_after)))
            except (TypeError, ValueError):
                delay = 2 ** attempt
            await response.aclose()
            await asyncio.sleep(delay)
        status = response.status_code
        if status >= 400:
            await response.aclose()
            category = "authentication" if status in (401, 403) else (
                "rate_limited" if status == 429 else "provider_error")
            raise RuntimeError(f"Location agent failed: {category} ({status})")
        payload = response.json()
        await response.aclose()
        if payload.get("status") == "incomplete":
            details = payload.get("incomplete_details") or {}
            raise IncompleteLocationResponse(
                "Location agent returned an incomplete response: "
                + str(details.get("reason") or "unknown reason")
            )
        if payload.get("status") != "completed":
            raise RuntimeError(f"Location agent failed with status {payload.get('status')!r}")
        texts = [part["text"] for item in payload.get("output", [])
                 if item.get("type") == "message" for part in item.get("content", [])
                 if part.get("type") == "output_text"]
        if len(texts) != 1:
            raise InvalidLocationResponse(
                "Location agent did not return one structured result"
            )
        try:
            result = json.loads(texts[0])
            if not isinstance(result, dict):
                raise InvalidLocationResponse("Agent returned a non-object response")
            return result
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise InvalidLocationResponse(
                "Location agent returned malformed structured data"
            ) from exc

    async def classify(self, rows, prompt, on_batch=None):
        if not prompt or not prompt.strip():
            raise ValueError("location prompt cannot be empty")
        ids = [row[self.id_key] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("location IDs must be unique")
        batches = [rows[offset:offset + self.batch_size]
                   for offset in range(0, len(rows), self.batch_size)]
        limiter = asyncio.Semaphore(self.concurrency)
        timeout = httpx.Timeout(self.timeout, connect=10, write=30, pool=10)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False,
                                     transport=self.transport) as client:
            async def one(batch, invalid_attempt=0):
                try:
                    async with limiter:
                        result = await self._classify_batch(client, batch, prompt)
                except LocationBatchTimeout:
                    result = [{
                        **row,
                        self.canonical_key: row[self.raw_key],
                        "matches": True,
                        "reason": f"Unclassified: {self.agent_label.lower()} timed out; kept by policy",
                    } for row in batch]
                except (IncompleteLocationResponse, InvalidLocationResponse):
                    if len(batch) <= 1:
                        if invalid_attempt >= 2:
                            raise
                        return await one(batch, invalid_attempt + 1)
                    middle = len(batch) // 2
                    parts = await asyncio.gather(one(batch[:middle]), one(batch[middle:]))
                    return [row for part in parts for row in part]
                if on_batch is not None:
                    on_batch(result)
                return result

            completed = await asyncio.gather(*(one(batch) for batch in batches))
        return [row for batch in completed for row in batch]


async def classify_locations(jobs, prompt, path, agent=None, progress=None):
    rows = unique_locations(jobs)
    for row in rows:
        row["filter_prompt"] = prompt
    existing = {}
    if Path(path).exists():
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if (row.get("filter_prompt") == prompt
                        and str(row.get("matches", "")).casefold() in ("true", "false")):
                    existing[row.get("location_id", "")] = row
    rows = [{**row, **existing.get(row["location_id"], {})} for row in rows]
    # Persist the exact agent input before the external call. A failure leaves a
    # reviewable, resumable vocabulary CSV rather than losing the batch boundary.
    write_table(path, LOCATION_COLUMNS, rows)
    agent = agent or OpenAILocationAgent()
    pending = [row for row in rows if str(row["matches"]).casefold() not in ("true", "false")]
    pending_ids = {row["location_id"] for row in pending}
    completed_ids = {row["location_id"] for row in rows if row["location_id"] not in pending_ids}
    if progress:
        progress({
            "phase": "filter_locations",
            "unique_locations": len(rows),
            "cached": len(completed_ids),
            "pending": len(pending),
        })
    if not pending:
        return rows
    if isinstance(agent, OpenAILocationAgent):
        by_id = {row["location_id"]: row for row in rows}

        def checkpoint(completed):
            by_id.update((row["location_id"], row) for row in completed)
            completed_ids.update(row["location_id"] for row in completed)
            write_table(path, LOCATION_COLUMNS, [by_id[row["location_id"]] for row in rows])
            if progress:
                progress({
                    "phase": "filter_location_progress",
                    "completed": len(completed_ids),
                    "total": len(rows),
                    "requests": agent.request_count,
                    "skipped": sum(
                        str(row.get("reason", "")).startswith("Unclassified:")
                        for row in by_id.values()
                    ),
                })

        await agent.classify(pending, prompt, on_batch=checkpoint)
        return [by_id[row["location_id"]] for row in rows]
    filled = await agent.classify(pending, prompt)
    by_id = {row["location_id"]: row for row in rows}
    by_id.update((row["location_id"], row) for row in filled)
    completed = [by_id[row["location_id"]] for row in rows]
    write_table(path, LOCATION_COLUMNS, completed)
    if progress:
        progress({
            "phase": "filter_location_progress",
            "completed": len(rows),
            "total": len(rows),
            "requests": getattr(agent, "request_count", 1),
            "skipped": sum(
                str(row.get("reason", "")).startswith("Unclassified:") for row in completed
            ),
        })
    return completed


def classify_locations_sync(jobs, prompt, path, agent=None):
    return asyncio.run(classify_locations(jobs, prompt, path, agent))
