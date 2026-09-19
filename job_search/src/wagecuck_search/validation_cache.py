"""Optional URL-validation journal used by resumable CSV workflows."""

import hashlib
import json
from dataclasses import asdict

from .dedupe import canonical_url
from .validation import ValidationResult


class CachedValidator:
    def __init__(self, validator, path):
        self.validator = validator
        self.path = path
        self.cached = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                    if value.get("array_field_fingerprint", value.get("partial_field_fingerprint")) != validator.array_field_fingerprint:
                        continue
                    result = value["result"]
                    if "partial_fields" in result:
                        result.setdefault("array_fields", result.pop("partial_fields"))
                    self.cached[value["key"]] = ValidationResult(**result)
                except (ValueError, KeyError, TypeError):
                    continue

    @staticmethod
    def key(job):
        value = {
            "url": canonical_url(job.url), "title": job.title, "company": job.company,
            "sources": job.sources, "employer_urls": job.employer_urls,
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def record(self, job, result):
        key = self.key(job)
        self.cached[key] = result
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({
                    "key": key, "result": asdict(result),
                    "array_field_fingerprint": self.validator.array_field_fingerprint,
                }, ensure_ascii=False) + "\n"
            )

    @staticmethod
    def cacheable(result):
        reason = (result.reason or "").lower()
        return not any(
            value in reason
            for value in ("http 429", "timed out", "could not be loaded", "failed (")
        )

    async def prepare(self, job):
        key = self.key(job)
        if key in self.cached:
            return self.cached[key]
        result = await self.validator.prepare(job)
        if isinstance(result, ValidationResult) and self.cacheable(result):
            self.record(job, result)
        return result

    async def finish(self, plan):
        result = await self.validator.finish(plan)
        if self.cacheable(result):
            self.record(plan.job, result)
        return result

    async def recycle(self):
        await self.validator.recycle()

    async def close(self):
        await self.validator.close()
