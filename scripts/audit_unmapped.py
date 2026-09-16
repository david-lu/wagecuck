"""Re-plan every previously unmapped control using saved metadata; no browser/network actions."""

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from wagecuck.agent import WorkflowAgent, fact_key
from wagecuck.browser import normalize
from wagecuck.models import FormField, Profile, Snapshot
from wagecuck.profile_fields import key_for
from wagecuck.screening import answer_for


def signature(field):
    return normalize(field.label), normalize(field.group), field.kind


def missing_reason(field, key):
    q = normalize(field.group or field.label)
    if key:
        return "Set this named profile value; it is currently unknown or empty."
    if field.kind == "file":
        return "No matching document is configured."
    if "privacy" in q or "own words" in q or "read" in q and "policy" in q:
        return "Employer-specific acknowledgement is not implied by processing consent."
    if "sponsor" in q:
        return "An explicit sponsorship declaration for this jurisdiction is missing."
    if any(term in q for term in ("gender", "pronoun", "race", "ethnic", "nationality")):
        return "This demographic declaration is not supplied."
    if any(term in q for term in ("export", "citizen", "work permit", "authorized")):
        return "This eligibility question needs its own applicable declaration; other jurisdictions/statuses are not substitutes."
    if q in ("start typing", "3 questions", "4 submit application", "locations required"):
        return "Insufficient question context in saved metadata; parser/control work is needed."
    return (
        "Employer-specific answer, additional declared fact, or semantic/control support is needed."
    )


async def audit(report, profile, snapshot_runs):
    output = []
    values = profile.values()
    for case in report["results"]:
        old = case.get("unmapped_fields", [])
        if not old:
            continue
        paths = sorted(Path("runs", snapshot_runs.get(case["id"], "missing")).glob("step-*.json"))
        saved = (
            Snapshot.model_validate_json(paths[-1].read_text(encoding="utf-8")).fields
            if paths
            else []
        )
        fields, available = [], []
        for index, data in enumerate(old):
            fallback = FormField(id=f"audit{index}", frame=0, name=data.get("group", ""), **data)
            matches = [f for f in saved if signature(f) == signature(fallback)]
            found = len(matches) == 1
            fields.append(
                matches[0].model_copy(update={"id": f"audit{index}"}) if found else fallback
            )
            available.append(found)
        actions, unresolved = await WorkflowAgent().plan(fields, profile)
        by_id = {a.field.id: a for a in actions}
        missing = {f.id for f in unresolved}
        for f, snapshot_found in zip(fields, available, strict=True):
            key = key_for(f, profile) or fact_key(f)
            screening = answer_for(f.group if f.kind == "radio" else f.label, profile.screening)
            if screening:
                key = f"screening.{screening.source}"
            action = by_id.get(f.id)
            status = (
                "planned"
                if action
                else "already_filled_or_group_option"
                if f.id not in missing
                else "missing_profile_value"
                if key and key not in values
                else "choice_or_control_unresolved"
                if key
                else "unresolved"
            )
            output.append(
                {
                    "case": case["id"],
                    "label": f.label,
                    "group": f.group,
                    "kind": f.kind,
                    "required": f.required,
                    "status": status,
                    "profile_field": (
                        action.source.replace("screening:", "screening.", 1)
                        if action.source.startswith("screening:")
                        else action.source.split(":", 1)[1]
                    )
                    if action
                    else key,
                    "source": action.source if action else None,
                    "saved_control_metadata_available": snapshot_found,
                    "reason": "Resolved from a named profile field; browser acceptance was not retested."
                    if action or status == "already_filled_or_group_option"
                    else missing_reason(f, key),
                }
            )
    return output


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path("profiles/demo/profile.json"))
    parser.add_argument("--report", type=Path, default=Path("docs/dry-run-all.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/unmapped-field-audit.json"))
    args = parser.parse_args()
    original = json.loads(args.report.read_text(encoding="utf-8"))
    snapshot_runs = {}
    for path in (Path("docs/live-report.json"), Path("docs/expanded-live-report.json")):
        if path.exists():
            snapshot_runs.update(
                {
                    row["id"]: row["run_id"]
                    for row in json.loads(path.read_text(encoding="utf-8"))["results"]
                }
            )
    rows = await audit(original, Profile.load(args.profile), snapshot_runs)
    counts = dict(Counter(row["status"] for row in rows))
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "source_report": str(args.report),
        "mode": "saved_field_metadata_replay",
        "applications_submitted": 0,
        "limitations": "Re-plans stored controls only. No live navigation, browser filling, model calls, server validation or submissions were performed. Missing snapshots lack select options/context.",
        "original_unmapped_controls": len(rows),
        "distinct_question_type_requirement_groups": len(
            {(r["group"] or r["label"], r["kind"], r["required"]) for r in rows}
        ),
        "counts": counts,
        "fields": rows,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Unmapped-field review",
        "",
        report["limitations"],
        "",
        f"Reviewed every unmapped control in the original report: **{len(rows)} controls**, across **{report['distinct_question_type_requirement_groups']} distinct question/type/requirement groups**.",
        "",
        f"Current planner outcomes: {counts}.",
        "",
        "The original live report remains unchanged. `planned` means a profile mapping exists, not that an employer accepted the answer. Repeated radio/checkbox labels remain separate controls in the JSON.",
        "",
        "| Question | Type | Status | Profile field |",
        "|---|---|---|---|",
    ]
    seen = set()
    for row in rows:
        question = row["group"] or row["label"]
        signature = (question, row["kind"], row["status"], row["profile_field"])
        if signature in seen:
            continue
        seen.add(signature)
        question = question.replace("|", " / ").replace("\n", " ")
        lines.append(
            f"| {question} | {row['kind']} | {row['status']} | {row['profile_field'] or '—'} |"
        )
    args.output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "fields"}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
