"""Preprint / version-of-record deduplication for the independent-output count.

A preprint and the journal article of the same study are one research output. A relation is
only honoured when a case declares ``preprint-of`` with the DOI of another case in the run;
nothing is merged by title or text similarity. A declared but unresolved preprint is reported,
not guessed.
"""

from __future__ import annotations

from typing import Any


def classify(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per case: ``counted``, ``duplicate-of`` another case, or a preprint note."""
    by_doi = {
        (c["identifiers"].get("doi") or "").lower(): c["case_id"]
        for c in cases
        if c["identifiers"].get("doi")
    }
    rows = []
    for c in cases:
        status, note = "counted", None
        for rel in c.get("relations", []):
            if rel["type"] == "preprint-of":
                target = by_doi.get(str(rel.get("doi", "")).lower())
                if target and target != c["case_id"]:
                    status, note = (
                        f"duplicate-of:{target}",
                        "preprint of a version of record in this run",
                    )
                else:
                    note = (
                        "declared preprint-of an article not in this run; counted once, unresolved"
                    )
            elif rel["type"] == "has-preprint" and rel.get("status") != "resolved-in-run":
                state = rel.get("status", "unresolved")
                note = f"preprint {state}: {rel.get('note', '')}".strip()
        rows.append(
            {
                "case_id": c["case_id"],
                "independent_output": status == "counted",
                "status": status,
                "note": note,
            }
        )
    return rows
