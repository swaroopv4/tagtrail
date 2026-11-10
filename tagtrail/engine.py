"""Deterministic reconciliation with explicit authority and no speculative corrections."""

import hashlib
import json
from collections import Counter

from .parsers import FIELDS, normalize


def reconcile(documents: list[dict]) -> list[dict]:
    baseline = next((d for d in documents if d["role"] == "register"), None)
    if not baseline:
        raise ValueError("Upload a comparison register before running checks.")
    master = {r["tag"]: r for r in baseline["parsed"]["records"]}
    findings = []

    def add(kind, document, tag, record=None, field=None, expected=None, actual=None, proposal=None):
        source = record or {
            "location": "document coverage",
            "excerpt": "Tag absent from this declared complete list.",
        }
        authority = master.get(tag)
        fingerprint = [kind, document["id"], tag, source["location"], field]
        finding_id = hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()[:24]
        titles = {
            "mismatch": f"{field.replace('_', ' ').title()} differs from register"
            if field
            else "Value differs",
            "missing_value": f"{field.replace('_', ' ').title()} is missing" if field else "Value missing",
            "unknown_tag": "Tag is not in the comparison register",
            "duplicate_tag": "Tag appears in multiple table rows",
            "missing_tag": "Tag missing from a declared complete list",
        }
        findings.append(
            {
                "id": finding_id,
                "kind": kind,
                "tag": tag,
                "title": titles[kind],
                "severity": "high" if kind in {"mismatch", "duplicate_tag"} else "review",
                "field": field,
                "expected": expected,
                "actual": actual,
                "proposal": proposal,
                "source": {
                    "document_id": document["id"],
                    "filename": document["filename"],
                    "sha256": document["sha256"],
                    "location": source["location"],
                    "excerpt": source["excerpt"],
                },
                "authority": (
                    {
                        "document_id": baseline["id"],
                        "filename": baseline["filename"],
                        "sha256": baseline["sha256"],
                        "location": authority["location"],
                        "excerpt": authority["excerpt"],
                    }
                    if authority
                    else None
                ),
                "explanation": {
                    "mismatch": (
                        "An explicit table value disagrees with your chosen comparison register. "
                        "The register is a comparison baseline, not a design certification."
                    ),
                    "missing_value": (
                        "The register supplies this value but the reference table leaves it blank."
                    ),
                    "unknown_tag": (
                        "This extracted token has no matching register entry. Confirm whether it is "
                        "equipment, an intentional addition, or another identifier."
                    ),
                    "duplicate_tag": (
                        "Multiple rows make the intended target ambiguous. "
                        "Automatic corrections are disabled for this tag in this file."
                    ),
                    "missing_tag": (
                        "You marked this table as a complete list, but it omits a register tag. "
                        "Confirm the document scope before adding a row."
                    ),
                }[kind],
            }
        )

    for document in documents:
        if document["role"] == "register":
            continue
        records = document["parsed"]["records"]
        counts = Counter(r["tag"] for r in records)
        seen = set()
        is_table = document["parsed"]["kind"] == "table"
        duplicates = {tag for tag, count in counts.items() if count > 1} if is_table else set()
        for record in records:
            tag = record["tag"]
            if tag in seen and (not is_table or tag in duplicates):
                continue
            seen.add(tag)
            if tag in duplicates:
                add("duplicate_tag", document, tag, record)
                continue
            if tag not in master:
                add("unknown_tag", document, tag, record)
                continue
            if not is_table:
                continue
            for field in FIELDS:
                expected = master[tag]["values"].get(field, "")
                actual = record["values"].get(field, "")
                if not expected or normalize(field, expected) == normalize(field, actual):
                    continue
                # A column absent from the reference is not an editable missing cell.
                if field not in document["parsed"]["headers"]:
                    continue
                proposal = {
                    "document_id": document["id"],
                    "row": record["row"],
                    "sheet": record.get("sheet"),
                    "field": field,
                    "before": actual,
                    "after": expected,
                    "source_sha256": document["sha256"],
                }
                add(
                    "missing_value" if not actual else "mismatch",
                    document,
                    tag,
                    record,
                    field,
                    expected,
                    actual,
                    proposal,
                )
        if is_table and document["complete_list"]:
            for tag in sorted(set(master) - seen):
                add("missing_tag", document, tag)
    return sorted(
        findings, key=lambda f: (f["severity"] != "high", f["tag"], f["source"]["filename"], f["kind"])
    )
