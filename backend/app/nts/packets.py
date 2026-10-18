"""Packet inputs and the exploratory (unreviewed) writer.

Two distinct outputs come from the same extraction:

* ``producer_input`` feeds the *unchanged* production ``build_packet``. There is no human review,
  so that producer withholds every finding. That is the correct, strict result.
* ``build_exploratory_packet`` writes a contract-valid ``proposed`` / ``unreviewed`` packet so the
  extraction can be inspected and so a consumer's rejection of unreviewed work is observable. It is
  never a substitute for the reviewed packet: it has no assessments, no approvals and
  ``creation_method: manual`` because it is not the Finding Curation producer's output.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.finding_packet import (
    ARTIFACTS,
    NA,
    SCHEMA_VERSION,
    PacketResult,
    _jsonl,
    _paper_rights,
    _sha,
    _source_type,
    _text,
    _ts,
)
from app.nts.extract import Proposal
from app.nts.source import FrozenSource, stable_uuid
from app.rights import quotes_redistributable

PROPOSER_REF = "automation:nts-deterministic-extractor-v1"  # not a human, not a reviewer
EMPTY = "1970-01-01T00:00:00Z"


def redacted_content(p: Proposal) -> dict[str, Any]:
    """Machine-worded statement for articles whose quotation rights are not established.

    The verbatim sentence must not leak through ``statement`` while the passage quote is withheld.
    The closest contract enum for text worded by a tool, not by the authors, is ``ai-paraphrase``.
    """
    digest = hashlib.sha256(p.quote.encode("utf-8")).hexdigest()[:12]
    if p.kind == "nts-identifier":
        ids = ", ".join(
            f"{m['normalized']} (cited version {m['version_cited'] or 'not cited'})"
            for m in p.typed["nts_mentions"]
        )
        what = f"an ALURES NTS identifier: {ids}"
    elif p.kind == "national-authorization":
        what = "a national or institutional authorization number: " + ", ".join(
            f"{a['kind']} {a['value']}" for a in p.typed["authorizations"]
        )
    else:
        n = len(p.content["numeric_results"])
        what = f"animal numbers ({n} typed values, see numeric_results)"
    statement = (
        f"The passage with quote sha256 {digest} reports {what}. "
        "Statement worded by an extraction rule; the verbatim sentence is withheld because "
        "quotation rights are not established."
    )
    return {**p.content, "statement": statement, "origin": "ai-paraphrase"}


def _paper_input(frozen: FrozenSource, rights: dict[str, Any] | None) -> dict[str, Any]:
    ids = frozen.source["identifiers"]
    return {
        "article_id": str(frozen.snapshot.article_id),
        "doi": ids.get("doi"),
        "pmid": ids.get("pmid"),
        "pmcid": ids.get("pmcid"),
        "title": frozen.source["title"],
        "authors": list(frozen.source.get("authors") or []),
        "pub_types": list(frozen.source.get("pub_types") or []),
        "rights": rights,
        "snapshot": {
            "id": str(frozen.snapshot.id),
            "sha256": frozen.snapshot.sha256,
            "raw_availability": frozen.snapshot.raw_artifact.availability,
        },
    }


def _evidence(frozen: FrozenSource, p: Proposal) -> dict[str, Any]:
    r = p.receipt
    return {
        "prediction_id": None,
        "answer_ref": None,
        "evidence_ref": str(stable_uuid("evidence", p.finding_id)),
        "quote": p.quote,
        "match": {
            "ref": r["ref"],
            "status": r["status"],
            "view_ref": r["view_ref"],
            "start": r["start"],
            "end": r["end"],
        },
        "snapshot_id": str(frozen.snapshot.id),
    }


def producer_input(
    cases: list[tuple[FrozenSource, list[Proposal]]],
    commit: str,
    declared_rights: dict[str, dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Input of the production ``build_packet``: latest revision, **no reviews**."""
    declared_rights = declared_rights or {}
    papers, findings = [], []
    for frozen, proposals in cases:
        art = str(frozen.snapshot.article_id)
        papers.append(_paper_input(frozen, declared_rights.get(art)))
        for p in proposals:
            findings.append(
                {
                    "finding_id": p.finding_id,
                    "article_id": art,
                    "revision": 1,
                    "is_latest": True,
                    "content": p.content
                    if quotes_redistributable(declared_rights.get(art))
                    else redacted_content(p),
                    "proposer_ref": PROPOSER_REF,
                    "created_at": frozen.snapshot.captured_at.isoformat(),
                    "evidence": [_evidence(frozen, p)],
                    "reviews": [],
                }
            )
    return {
        "producer_commit": commit,
        "access_level": "restricted",
        "metadata_license": None,
        "papers": papers,
        "findings": findings,
    }


def _pid(p: Proposal) -> str:
    return "passage:" + str(stable_uuid("passage", p.finding_id))


def build_exploratory_packet(
    cases: list[tuple[FrozenSource, list[Proposal]]],
    commit: str,
    rights_by_article: dict[str, dict[str, Any] | None],
) -> PacketResult:
    """Contract-valid packet of proposed/unreviewed findings. No assessments, ever."""
    papers, passages, findings = [], [], []
    rights_used: dict[str, str] = {}
    stamps = []
    for frozen, proposals in cases:
        if not proposals:
            continue
        art = str(frozen.snapshot.article_id)
        rights = rights_by_article.get(art)
        include = quotes_redistributable(rights)
        paper_id = "paper:" + art
        snap = frozen.snapshot
        ids = {
            k: _text(frozen.source["identifiers"].get(k)) or NA for k in ("doi", "pmid", "pmcid")
        }
        papers.append(
            {
                "paper_id": paper_id,
                "identifiers": ids,
                "title": frozen.source["title"],
                "authors": list(frozen.source.get("authors") or []),
                "source_type": _source_type(list(frozen.source.get("pub_types") or [])),
                "source_snapshot": {
                    "state": "captured",
                    "snapshot_ref": str(snap.id),
                    "sha256": "sha256:" + snap.sha256,
                },
                "rights": {**_paper_rights(rights), "full_text_included": False},
                "producer_ids": {"article_ref": art},
            }
        )
        if include:
            rights_used[_paper_rights(rights)["license"]] = art
        created = _ts(snap.captured_at.isoformat())
        stamps.append(created)
        for p in proposals:
            exact = p.receipt["status"] in ("exact", "normalized")
            passages.append(
                {
                    "passage_id": _pid(p),
                    "paper_id": paper_id,
                    "quote_state": "included" if include else "withheld-rights",
                    "quote": p.quote if include else "withheld",
                    "quote_sha256": _sha(p.quote.encode("utf-8")),
                    "source": {
                        "snapshot_ref": str(snap.id),
                        "view_id": p.receipt["view_ref"] or NA,
                        "start": p.receipt["start"] if exact else None,
                        "end": p.receipt["end"] if exact else None,
                    },
                    "match": {
                        "receipt_ref": p.receipt["ref"],
                        "status": p.receipt["status"],
                        "matcher": "quote-matcher-v1",
                    },
                    "producer_ids": {"evidence_ref": str(stable_uuid("evidence", p.finding_id))},
                }
            )
            findings.append(
                {
                    "finding_id": "finding:" + p.finding_id,
                    "revision": 1,
                    "paper_id": paper_id,
                    "statement": (p.content if include else redacted_content(p))["statement"],
                    "origin": (p.content if include else redacted_content(p))["origin"],
                    "export_state": "proposed",
                    "review_state": "unreviewed",
                    "verification_level": "unverified",
                    "context": p.content["context"],
                    "numeric_results": p.content["numeric_results"],
                    "passage_ids": [_pid(p)],
                    "producer_ids": {"evidence_refs": [str(stable_uuid("evidence", p.finding_id))]},
                    "created_at": created,
                }
            )
    records = {
        "papers": sorted(papers, key=lambda r: r["paper_id"]),
        "findings": sorted(findings, key=lambda r: (r["finding_id"], r["revision"])),
        "passages": sorted(
            {r["passage_id"]: r for r in passages}.values(), key=lambda r: r["passage_id"]
        ),
        "assessments": [],
    }
    files = {f"{n}.jsonl": _jsonl(records[n]) for n in ARTIFACTS}
    artifacts = {
        n: {
            "path": f"{n}.jsonl",
            "sha256": _sha(files[f"{n}.jsonl"]),
            "bytes": len(files[f"{n}.jsonl"]),
            "record_count": len(records[n]),
        }
        for n in ARTIFACTS
    }
    digest = hashlib.sha256(
        "\n".join(artifacts[n]["sha256"] for n in ARTIFACTS).encode()
    ).hexdigest()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "packet_id": "packet:" + digest[:32],
        "created_at": max(stamps) if stamps else EMPTY,
        "creation_method": "manual",
        "producer": {
            "name": "diggypaper",
            "commit": commit,
            "schema_refs": [
                {"name": "evidence-envelope-v1", "version": "1"},
                {"name": "quote-matcher-v1", "version": "1"},
            ],
        },
        "artifacts": artifacts,
        "licenses": [
            {"license_id": "unknown", "applies_to": "packet-metadata"},
            *({"license_id": lic, "applies_to": "quotes"} for lic in sorted(rights_used)),
        ],
        "access_scope": {"level": "restricted", "redistributable_full_text": False},
        "export_status": {
            "state": "complete",
            "withheld_counts": {"papers": 0, "findings": 0, "passages": 0, "assessments": 0},
            "reasons": [],
        },
    }
    files["manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()
    report = {
        "state": "exploratory-unreviewed",
        "findings": len(findings),
        "assessments": 0,
        "quotes_included": sum(1 for p in records["passages"] if p["quote_state"] == "included"),
        "quotes_withheld": sum(1 for p in records["passages"] if p["quote_state"] != "included"),
    }
    return PacketResult(files=dict(sorted(files.items())), report=report)
