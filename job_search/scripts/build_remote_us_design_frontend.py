"""Curate the October 2 design/creative/frontend search into one US-remote CSV.

The category CSVs are raw search results, so neither a matching title nor the
provider's `remote` flag alone proves the job belongs in the final list.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path


RESULTS = Path(__file__).resolve().parents[1] / "results" / "design-creative-frontend-2026-10-02"
SOURCES = (
    "creative-technologist.csv",
    "design-engineer.csv",
    "design-technologist.csv",
    "frontend-discovery.csv",
    "frontend-engineer.csv",
)
DESTINATION = RESULTS / "remote-all-titles-by-posted-at.csv"

US_LOCATION = re.compile(r"(?<![a-z])(?:united states|u\.?s\.?a?\.?)(?![a-z])", re.I)
US_STATE_REMOTE = re.compile(r"\b(?:california|texas|arizona|north carolina)\s*[–-]\s*remote\b", re.I)
FRONTEND_TITLE = re.compile(
    r"\b(?:front[ -]?end|web(?:site|master)?|ui|ux|react(?:\.js)?|angular|"
    r"vue(?:\.js)?|svelte|next\.js|blazor|javascript|wordpress|contentful|"
    r"craftcms|fiori|ui5|design systems?|creative technologist|design technologist|"
    r"interactives?|cms)\b",
    re.I,
)
DESIGN_ENGINEER_TITLE = re.compile(r"\b(?:product )?design engineer\b", re.I)

# Generic design-engineer titles need a role-level check. These postings describe
# production UI, frontend code, websites, or coded design systems. The company
# postings for Ashby, Vercel, WorkOS, Turquoise Health, and others were checked.
VERIFIED_DESIGN_ENGINEERS = {
    ("accelerant", "product design engineer"),
    ("ashby", "design engineer - americas"),
    ("ashby", "staff design engineer - americas"),
    ("celigo", "design engineer"),
    ("clipboard health", "senior design engineer - design systems"),
    ("consensys", "senior design engineer - metamask"),
    ("filevine", "product design engineer"),
    ("good maven", "design engineer"),
    ("infisical", "design engineer - site"),
    ("kick", "design engineer"),
    ("linear", "design engineer - web & brand"),
    ("livekit", "design engineer - web/brand"),
    ("lunon", "founding design engineer"),
    ("one finance", "design engineer"),
    ("revenuecat", "senior design engineer"),
    ("runway ai", "design engineer - runway labs"),
    ("stripe", "design engineer - expansion"),
    ("tradeify", "design engineer"),
    ("turquoise health", "senior design engineer"),
    ("vercel", "design engineer"),
    ("vetcove", "staff design engineer"),
    ("workos", "design engineer - dashboard - admin portal"),
}

VERIFIED_OTHER_FRONTEND = {
    ("keeper security", "senior software architect - browser extension platform"),
    ("loancrate", "senior software engineer - design"),
    ("reddit", "senior staff software engineer - client architecture"),
    ("topstep", "staff software engineer - trading platform - charts"),
}

# Source metadata is incorrect or too broad for these specific postings.
EXCLUDED_ROLES = {
    ("hud", "design engineer"),  # Employer posting says on-site.
    ("restate", "design engineer"),  # Employer posting says remote Europe.
    ("amazon", "design technologist 2 - prime video"),  # Employer lists offices, not remote.
    ("matcha.fm", "design engineer"),  # Application is for a designer, not an engineer.
    ("certik", "solidity compiler frontend engineer"),  # Compiler frontend, not UI.
    ("divcon", "scada front-end engineer"),  # Industrial controls interface.
    ("truelogic", "senior frontend engineer - fintech"),  # Employer labels it hybrid in NYC.
}

# This raw record only says "Remote". The role listing explicitly allows US remote.
VERIFIED_US_REMOTE = {("elation health", "design technologist")}


def role_key(row: dict[str, str]) -> tuple[str, str]:
    return row["company"].strip().casefold(), row["title"].strip().casefold()


def clean_company(row: dict[str, str]) -> None:
    # RoleSweep sometimes puts the full title and a "Seen ... ago" label in company.
    match = re.fullmatch(r".+\s+Seen\s+\d+[a-z]+\s+ago\s+(.+)", row["company"])
    if match:
        row["company"] = match.group(1)


def is_us_remote(row: dict[str, str]) -> bool:
    if row["workplace"].strip().casefold() != "remote":
        return False
    location = row["location"]
    return bool(US_LOCATION.search(location) or US_STATE_REMOTE.search(location)) or role_key(row) in VERIFIED_US_REMOTE


def is_relevant(row: dict[str, str]) -> bool:
    key = role_key(row)
    if key in EXCLUDED_ROLES:
        return False
    if DESIGN_ENGINEER_TITLE.search(row["title"]):
        return key in VERIFIED_DESIGN_ENGINEERS
    return key in VERIFIED_OTHER_FRONTEND or bool(FRONTEND_TITLE.search(row["title"]))


def posted_time(row: dict[str, str]) -> datetime:
    raw = row["posted_at"].strip()
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def main() -> None:
    chosen: dict[tuple[str, str], dict[str, str]] = {}
    fieldnames: list[str] | None = None
    for name in SOURCES:
        with (RESULTS / name).open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            if fieldnames is None:
                fieldnames = reader.fieldnames
            elif fieldnames != reader.fieldnames:
                raise ValueError(f"CSV schema differs in {name}")
            for row in reader:
                clean_company(row)
                if not is_us_remote(row) or not is_relevant(row):
                    continue
                key = role_key(row)
                previous = chosen.get(key)
                if previous is None or (
                    posted_time(row), sum(bool(v) for v in row.values())
                ) > (
                    posted_time(previous), sum(bool(v) for v in previous.values())
                ):
                    chosen[key] = row

    rows = sorted(chosen.values(), key=lambda row: (posted_time(row), row["title"].casefold()), reverse=True)
    with DESTINATION.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} US-remote, relevant roles to {DESTINATION}")


if __name__ == "__main__":
    main()
