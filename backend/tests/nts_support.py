"""Shared SYNTHETIC fixtures for the NTS tests. Nothing here is real ALURES or article data."""

from __future__ import annotations

import csv
import json
from pathlib import Path

COMMIT = "a" * 40

OPEN = {
    "license_id": "CC-BY-4.0",
    "url": "https://creativecommons.org/licenses/by/4.0/",
    "basis": "synthetic",
    "evidence_url": "https://example.org/licence",
}
UNKNOWN = {"license_id": "unknown", "url": None, "basis": "synthetic", "evidence_url": None}
NC = {
    "license_id": "CC-BY-NC-4.0",
    "url": "x",
    "basis": "synthetic",
    "evidence_url": "https://e.org/l",
}


def section(title, text, kind="methods"):
    return {"title": title, "kind": kind, "text": text}


def source(source_id, sections, license=OPEN, doi=None, pmcid=None):
    return {
        "source_id": source_id,
        "identifiers": {"doi": doi or f"10.5555/{source_id}", "pmid": None, "pmcid": pmcid},
        "title": f"Synthetic article {source_id}",
        "authors": [],
        "pub_types": ["Journal Article"],
        "license": license,
        "retrieval": {
            "method": "synthetic-test",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "completeness": "excerpt",
            "fidelity": "synthetic",
            "public_url": None,
        },
        "sections": sections,
    }


ANIMALS = section(
    "Animals",
    "A total of 40 rats were used. Rats were housed in pairs. "
    "The ALURES code is NTS-ES-111111 v.2, 3 May 2025.",
)


def write_study(tmp: Path, sources: dict[str, dict], extra_case: dict | None = None) -> dict:
    (tmp / "sources").mkdir(parents=True, exist_ok=True)
    cases = []
    for sid, src in sources.items():
        (tmp / "sources" / f"{sid}.json").write_text(json.dumps(src), encoding="utf-8")
        cases.append(
            {
                "case_id": sid,
                "role": "synthetic",
                "source": f"sources/{sid}.json",
                "expected_nts_id": None,
                **(extra_case or {}),
            }
        )
    return {
        "study_id": "synthetic",
        "alures": {"landing_url": "https://example.org"},
        "cases": cases,
    }


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path
