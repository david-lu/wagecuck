"""Classify the unique location vocabulary once, then join it back to jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .export import write_table

LOCATION_COLUMNS = [
    "filter_prompt", "location_id", "raw_location", "workplace", "job_count",
    "canonical_location", "matches", "reason",
]


def location_id(location, workplace):
    value = f"{location.strip()}\0{(workplace or '').strip()}".encode()
    return hashlib.sha256(value).hexdigest()[:16]


def unique_locations(jobs):
    counts = Counter((job.get("location", "").strip(), (job.get("workplace") or "").strip())
                     for job in jobs)
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
    """One structured model call for the entire unique-location table."""

    def __init__(self, model=None, endpoint=None, api_key=None, timeout=180, transport=None):
        load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)
        self.model = model or os.environ.get("WAGECUCK_SEARCH_AGENT_MODEL", "gpt-5-mini")
        self.endpoint = (endpoint or os.environ.get(
            "WAGECUCK_SEARCH_AGENT_ENDPOINT", "https://api.openai.com/v1"
        )).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("Location filtering requires OPENAI_API_KEY in the environment or .env")
        if timeout <= 0:
            raise ValueError("agent timeout must be positive")
        self.timeout = timeout
        self.transport = transport

    async def classify(self, rows, prompt):
        if not prompt or not prompt.strip():
            raise ValueError("location prompt cannot be empty")
        ids = [row["location_id"] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("location IDs must be unique")
        schema = {
            "type": "object",
            "properties": {"locations": {"type": "array", "minItems": len(rows),
                "maxItems": len(rows), "items": {"type": "object", "properties": {
                    "location_id": {"type": "string"},
                    "canonical_location": {"type": "string"},
                    "matches": {"type": "boolean"},
                    "reason": {"type": "string"},
                }, "required": ["location_id", "canonical_location", "matches", "reason"],
                    "additionalProperties": False}}},
            "required": ["locations"], "additionalProperties": False,
        }
        compact = [{"location_id": row["location_id"],
                    "location": row["raw_location"],
                    "workplace": row["workplace"]} for row in rows]
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
        body = {
            "model": self.model, "store": False,
            "max_output_tokens": min(64000, max(4000, len(rows) * 80)),
            "instructions": instructions,
            "input": json.dumps({"location_request": prompt, "locations": compact},
                                ensure_ascii=False),
            "text": {"format": {"type": "json_schema", "name": "location_filter",
                                  "strict": True, "schema": schema}},
        }
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False,
                                     transport=self.transport) as client:
            response = await client.post(self.endpoint + "/responses",
                headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
        if response.status_code >= 400:
            category = "authentication" if response.status_code in (401, 403) else (
                "rate_limited" if response.status_code == 429 else "provider_error")
            raise RuntimeError(f"Location agent failed: {category} ({response.status_code})")
        payload = response.json()
        if payload.get("status") != "completed":
            raise RuntimeError("Location agent returned an incomplete response")
        texts = [part["text"] for item in payload.get("output", [])
                 if item.get("type") == "message" for part in item.get("content", [])
                 if part.get("type") == "output_text"]
        if len(texts) != 1:
            raise RuntimeError("Location agent did not return one structured result")
        result = json.loads(texts[0])["locations"]
        by_id = {}
        for item in result:
            key = item["location_id"]
            if key not in ids or key in by_id:
                raise RuntimeError("Location agent returned an unknown or duplicate location ID")
            by_id[key] = item
        if set(by_id) != set(ids):
            raise RuntimeError("Location agent omitted one or more locations")
        return [{**row, **by_id[row["location_id"]]} for row in rows]


async def classify_locations(jobs, prompt, path, agent=None):
    rows = write_location_input(path, jobs)
    for row in rows:
        row["filter_prompt"] = prompt
    # Persist the exact agent input before the external call. A failure leaves a
    # reviewable, resumable vocabulary CSV rather than losing the batch boundary.
    write_table(path, LOCATION_COLUMNS, rows)
    agent = agent or OpenAILocationAgent()
    filled = await agent.classify(rows, prompt)
    write_table(path, LOCATION_COLUMNS, filled)
    return filled


def classify_locations_sync(jobs, prompt, path, agent=None):
    return asyncio.run(classify_locations(jobs, prompt, path, agent))
