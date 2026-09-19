"""Extensible, multi-value job attributes shared by discovery, validation and filtering."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from functools import lru_cache

from bs4 import BeautifulSoup

from .matching import matches_role_title, normalized

BUILTIN_TERMS = {
    "programming_languages": {
        "Python": [], "JavaScript": ["JS"], "TypeScript": [], "Java": [],
        "C++": [], "C#": [], "Go": ["Golang"], "Rust": [], "Ruby": [], "PHP": [],
        "Swift": [], "Kotlin": [], "Scala": [], "SQL": [], "Dart": [], "Elixir": [],
        "Clojure": [], "Haskell": [], "Objective-C": [], "Solidity": [],
        "Bash": [], "PowerShell": [], "R": [], "C": [],
    },
    "frameworks": {
        "React": ["React.js", "ReactJS"], "Next.js": ["NextJS"],
        "Vue.js": ["Vue", "VueJS"], "Nuxt.js": ["Nuxt", "NuxtJS"],
        "Angular": ["AngularJS"], "Svelte": [], "SvelteKit": [],
        "Node.js": ["NodeJS"], "Express.js": ["ExpressJS"],
        "Django": [], "Flask": [], "FastAPI": [], "Ruby on Rails": ["Rails"],
        "Spring Boot": [], "Spring": ["Spring Framework"], ".NET": ["ASP.NET", "dotnet"],
        "Laravel": [], "Symfony": [], "Phoenix": [], "React Native": [],
        "Flutter": [], "TensorFlow": [], "PyTorch": [], "scikit-learn": ["sklearn"],
        "jQuery": [], "NestJS": ["Nest.js"], "Remix": [], "Astro": [],
        "Three.js": ["ThreeJS"],
    },
}
# Bare words such as "go", "react", "spring" and letters need technical context.
AMBIGUOUS = {"Go", "R", "C", "React", "Spring", "Astro", "Remix", "Phoenix"}
TECH_CONTEXT = re.compile(
    r"language|framework|stack|develop|program|code|coding|backend|frontend|"
    r"experience|proficien|expert|familiar|skill|built|using|engineer|\bwith\b", re.I
)
RESERVED = {
    "url", "title", "company", "source", "salary", "description", "sources",
    "application_urls", "employer_urls", "array_fields", "array_fields_json",
    "note", "url_validated_at", "application_url_type", "source_sites", "source_urls",
    "workplace", "employment_type", "experience_levels", "internship", "sponsors_visa",
    "posted_at", "last_updated", "valid_through",
    "fields", "fields_json", "field_types", "field_types_json", "enrichment", "enrichment_json",
}


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("Array field names must use lowercase letters, numbers and underscores")
    if name in RESERVED or name.startswith("salary_") or name.endswith("_json"):
        raise ValueError(f"Array field name conflicts with a job column: {name}")
    return name


def items(value):
    """CSV item lists allow quoted commas inside a single item."""
    if value is None:
        return []
    if isinstance(value, str):
        value = next(csv.reader([value], skipinitialspace=True))
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
        raise ValueError("Array field values must be strings or lists of strings")
    unique = {}
    for item in value:
        item = re.sub(r"\s+", " ", item).strip()
        if item:
            unique.setdefault(item.casefold(), item)
    return list(unique.values())


def normalize_fields(fields):
    if not isinstance(fields, dict):
        raise ValueError("array_fields must be an object")
    return {validate_name(name): items(value) for name, value in fields.items()}


def merge_fields(*values):
    merged = {}
    for fields in values:
        for name, value in normalize_fields(fields or {}).items():
            merged[name] = items([*merged.get(name, []), *value])
    return merged


def field_values(job, name):
    arrays = job.get("array_fields") or {}
    if name in arrays:
        return items(arrays[name])
    if name in (job.get("fields") or {}):
        value = job["fields"][name]
    else:
        if name.startswith("salary_"):
            name = "salary." + name.removeprefix("salary_")
        value = job
        for part in name.split("."):
            value = value.get(part) if isinstance(value, dict) else None
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return items(value)
    if isinstance(value, dict):
        return [json.dumps(value, sort_keys=True)]
    return [str(value).lower() if isinstance(value, bool) else str(value).strip()]


def all_fields(job):
    values = normalize_fields(job.get("array_fields") or {})
    values.setdefault("location", field_values(job, "location"))
    for name in BUILTIN_TERMS:
        if name not in (job.get("fields") or {}):
            values.setdefault(name, field_values(job, name))
    return values


def comma_separated(values):
    stream = io.StringIO()
    csv.writer(stream, lineterminator="").writerow(items(values))
    return stream.getvalue()


def definitions(custom=None):
    result = {name: {"terms": terms} for name, terms in BUILTIN_TERMS.items()}
    if custom is not None and not isinstance(custom, dict):
        raise ValueError("Array field definitions must be an object")
    for name, definition in (custom or {}).items():
        validate_name(name)
        if not isinstance(definition, dict) or set(definition) - {"terms", "selectors"}:
            raise ValueError(f"{name}: use terms and/or selectors for extraction")
        terms = definition.get("terms", {})
        selectors = definition.get("selectors", [])
        if not isinstance(terms, dict) or any(
            not isinstance(key, str) or not key.strip()
            or not isinstance(aliases, list)
            or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
            for key, aliases in terms.items()
        ):
            raise ValueError(f"{name}: terms must map canonical items to lists of aliases")
        if not isinstance(selectors, list) or any(
            not isinstance(selector, str) or not selector.strip() for selector in selectors
        ):
            raise ValueError(f"{name}: selectors must be a list of CSS selectors")
        for selector in selectors:
            import soupsieve
            soupsieve.compile(selector)
        result[name] = {"terms": terms, "selectors": selectors}
    return result


@lru_cache(maxsize=2048)
def term_pattern(term):
    return re.compile(r"(?<![\w+#.\-])" + re.escape(term)
                      + (r"(?![\w+#&])" if term in ("R", "C") else r"(?![\w+#])"), re.I)


def extract_fields(description, custom=None, *, html=None):
    result = {}
    soup = BeautifulSoup(html, "html.parser") if html else None
    for name, definition in definitions(custom).items():
        found = []
        for canonical, aliases in definition.get("terms", {}).items():
            for term in [canonical, *aliases]:
                matches = list(term_pattern(term).finditer(description or ""))
                if term == canonical and canonical in AMBIGUOUS:
                    matches = [
                        match for match in matches
                        if match.group() == canonical
                        and TECH_CONTEXT.search(
                            description[max(0, match.start() - 100):match.end() + 100]
                        )
                    ]
                if matches:
                    found.append(canonical)
                    break
        if soup:
            for selector in definition.get("selectors", []):
                for node in soup.select(selector):
                    found.extend(items(node.get_text(" ", strip=True)))
        result[name] = items(found)
    return result


def enrich_job(job, custom=None):
    job.array_fields = merge_fields(
        job.array_fields,
        {k: v for k, v in extract_fields(job.title + "\n" + job.description, custom).items()
         if k not in job.fields},
    )
    job.array_fields.setdefault("location", [job.location] if job.location else [])
    return job


def page_evidence(html, title):
    """Use job descriptions, excluding scripts, navigation and recommended jobs."""
    soup = BeautifulSoup(html, "html.parser")
    descriptions = []
    locations = []
    records = []

    def location(value):
        if isinstance(value, list):
            return [item for entry in value for item in location(entry)]
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, dict):
            address = value.get("address", value)
            if isinstance(address, str):
                return [address]
            if isinstance(address, dict):
                parts = [address.get(key) for key in (
                    "addressLocality", "addressRegion", "addressCountry"
                )]
                parts = [part.get("name", "") if isinstance(part, dict) else part for part in parts]
                place = ", ".join(str(part) for part in parts if part) or str(value.get("name") or "")
                return [place] if place else []
        return []

    def walk(value):
        if isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                records.append(value)
            else:
                for child in value.values():
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            pass
    relevant = [record for record in records
                if normalized(str(record.get("title", ""))) == normalized(title)]
    if not relevant:
        candidates = [record for record in records
                      if matches_role_title(title, str(record.get("title", "")))]
        relevant = candidates if len(candidates) == 1 else []
    for record in relevant:
        places = location(record.get("jobLocation"))
        if record.get("jobLocationType") == "TELECOMMUTE":
            places = location(record.get("applicantLocationRequirements")) or places
            places = ["Remote; " + place for place in places] or ["Remote"]
        locations.extend(places)
        descriptions.append(
            BeautifulSoup(str(record.get("description") or ""), "html.parser")
            .get_text(" ", strip=True)
        )
    for node in soup.select("script, style, nav, footer, aside, noscript, [aria-hidden=true]"):
        node.decompose()
    if not any(descriptions):
        nodes = soup.select(
            '[itemprop=description], [data-testid=job-description], '
            '#jobDescriptionText, .prose-job, .show-more-less-html__markup, '
            '.posting-page .content, [class*=jobDescription]'
        )
        if not nodes:
            nodes = [soup.select_one("main") or soup]
        for node in nodes:
            value = re.split(
                r"Similar jobs|Related jobs|Recommended jobs|People also viewed",
                node.get_text(" ", strip=True), flags=re.I,
            )[0]
            descriptions.append(value)
    if not locations:
        labels = {}
        for term in soup.select("main header dl dt"):
            definition = term.find_next_sibling("dd")
            if definition:
                labels[term.get_text(" ", strip=True).casefold()] = definition.get_text(" ", strip=True)
        if labels.get("location"):
            place = labels["location"]
            if labels.get("work arrangement", "").casefold() == "remote":
                place = "Remote; " + place
            locations.append(place)
    return {"description": "\n".join(descriptions), "locations": items(locations), "html": str(soup)}


def extract_page_fields(html, title, custom=None):
    evidence = page_evidence(html, title)
    result = extract_fields(title + "\n" + evidence["description"], custom, html=evidence["html"])
    if evidence["locations"]:
        result["location"] = items([*result.get("location", []), *evidence["locations"]])
    return result


def value_id(name, value, context=""):
    value = f"{name}\0{value.strip().casefold()}\0{context.strip().casefold()}"
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def unique_values(jobs, name):
    rows = {}
    for job in jobs:
        context = (job.get("workplace") or "").strip() if name == "location" else ""
        for value in field_values(job, name):
            key = value_id(name, value, context)
            row = rows.setdefault(key, {
                "field_name": name, "value_id": key, "raw_value": value, "context": context,
                "job_count": 0, "filter_prompt": "", "canonical_value": "", "matches": "",
                "reason": "",
            })
            row["job_count"] += 1
    return sorted(rows.values(), key=lambda row: (row["raw_value"].casefold(), row["context"]))


def extraction_fingerprint(custom=None):
    value = json.dumps({"version": 3, "definitions": definitions(custom)}, sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()
