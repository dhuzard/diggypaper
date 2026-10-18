"""Pure builder of a source-evidence-packet/0.1.0 export from curated findings.

No database, clock, environment or randomness: the same input yields identical bytes.
A finding is exported only when it is the latest revision, approved by independent
reviewers, supported by a human supports/contradicts judgment on an exact/normalized
matched passage of a paper with a public identifier and a captured snapshot. Everything
else is omitted and counted by reason; nothing is guessed or defaulted into approval.

Rights (app.rights): a quotation is included only when the article's latest rights
declaration allows redistribution under an allowlisted licence. Without one, quotations are
withheld (hash only) and the paper is ``metadata-only``. A ``restricted`` declaration omits
the article's findings, because an approved finding may not sit on a restricted paper.
The packet is ``restricted`` unless the caller explicitly asks for ``public``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.rights import ACCESS_LEVELS, PACKET_METADATA_LICENSES, quotes_redistributable

SCHEMA_VERSION = "source-evidence-packet/0.1.0"
EMPTY_TIMESTAMP = "1970-01-01T00:00:00Z"
GOOD_MATCH = {"exact", "normalized"}
MATCH_STATUSES = {
    "exact",
    "normalized",
    "not_found",
    "ambiguous",
    "invalid",
    "source_unavailable",
    "not_checked",
    "incomplete",
}
JUDGMENTS = {"supports", "contradicts", "insufficient", "unclear"}
SUPPORT_OR_CONTRA = {"supports", "contradicts"}
NA = "not-available"
ARTIFACTS = ("papers", "findings", "passages", "assessments")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PacketResult:
    files: dict[str, bytes]
    report: dict[str, Any] = field(default_factory=dict)


def _line(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _jsonl(records: list[dict[str, Any]]) -> bytes:
    return "".join(_line(r) + "\n" for r in records).encode("utf-8")


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _ts(value: str) -> str:
    """Normalize an ISO timestamp to RFC 3339 UTC with a Z suffix (naive means UTC)."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _reviewer_ref(ref: str) -> str:
    """`reviewer-<64 hex>` (app.pseudonym.reviewer_reference) -> packet `rv-<64 hex>`."""
    return "rv-" + ref.removeprefix("reviewer-").removeprefix("rv-")


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _source_type(pub_types: list[str]) -> str:
    lowered = [t.lower() for t in pub_types]
    if any("preprint" in t for t in lowered):
        return "preprint"
    if any("review" in t for t in lowered):
        return "review"
    if any("journal article" in t for t in lowered):
        return "journal-article"
    return "other"


def _snapshot_sha(snapshot: dict[str, Any] | None) -> str | None:
    if not snapshot or not _text(snapshot.get("id")):
        return None
    digest = str(snapshot.get("sha256") or "").removeprefix("sha256:")
    return "sha256:" + digest if _HEX64.match(digest) else None


def _passage_state(ev: dict[str, Any], snapshot_id: str | None) -> dict[str, Any] | None:
    """Packet view of one evidence link, or None if it cannot be expressed faithfully."""
    quote = ev.get("quote")
    if not isinstance(quote, str) or not quote or snapshot_id is None:
        return None
    if ev.get("snapshot_id") != snapshot_id:
        return None
    match = ev.get("match") or None
    status = match.get("status") if match else None
    if match is None or status not in MATCH_STATUSES:
        return {
            "status": "not_checked",
            "receipt_ref": NA,
            "view_id": NA,
            "start": None,
            "end": None,
            "matcher": "unknown",
        }
    start, end = match.get("start"), match.get("end")
    view = _text(match.get("view_ref"))
    if status in GOOD_MATCH:
        ok = (
            view is not None
            and isinstance(start, int)
            and isinstance(end, int)
            and not isinstance(start, bool)
            and 0 <= start < end
        )
        if not ok:
            return None
    else:
        start = end = None
    return {
        "status": status,
        "receipt_ref": _text(match.get("ref")) or NA,
        "view_id": view or NA,
        "start": start,
        "end": end,
        "matcher": "quote-matcher-v1",
    }


def _evaluate(f: dict[str, Any], papers: dict[str, dict[str, Any]]) -> tuple[str | None, dict]:
    """Return (omit_reason | None, detail). detail carries what the exporter needs."""
    if not f.get("is_latest"):
        return "not-latest", {}
    proposer = f.get("proposer_ref")
    reviews = [r for r in f.get("reviews", []) if r.get("reviewer_ref") != proposer]
    if any(r.get("decision") == "reject" for r in reviews):
        return "rejected", {}
    approvers = [r for r in reviews if r.get("decision") == "approve"]
    if not approvers:
        return "not-approved", {}

    evidence = {e["evidence_ref"]: e for e in f.get("evidence", [])}
    paper = papers.get(f.get("article_id"))
    snap_id = _text(paper["snapshot"]["id"]) if paper and paper.get("snapshot") else None
    # A passage counts as matched on its own match data, independent of snapshot state.
    matched = {
        ref
        for ref, e in evidence.items()
        if _passage_state(e, e.get("snapshot_id") or None) is not None
        and (e.get("match") or {}).get("status") in GOOD_MATCH
        and isinstance(e.get("quote"), str)
        and e["quote"]
    }
    judged = {
        r["reviewer_ref"]: [j for j in r.get("judgments", []) if j.get("evidence_ref") in matched]
        for r in approvers
    }
    if not any(j.get("status") in SUPPORT_OR_CONTRA for js in judged.values() for j in js):
        return "no-matched-passage", {}
    if paper is None or not any(_text(paper.get(k)) for k in ("doi", "pmid", "pmcid")):
        return "no-public-identifier", {}
    if (paper.get("rights") or {}).get("redistribution") == "restricted":
        return "paper-rights-restricted", {}
    snap_sha = _snapshot_sha(paper.get("snapshot"))
    if snap_id is None or snap_sha is None:
        return "no-source-snapshot", {}
    passages = {ref: _passage_state(e, snap_id) for ref, e in evidence.items()}
    passages = {ref: p for ref, p in passages.items() if p is not None}
    # Re-check support with the snapshot constraint: the matched passage must belong to it.
    if not any(
        j.get("status") in SUPPORT_OR_CONTRA
        and passages.get(j.get("evidence_ref"), {}).get("status") in GOOD_MATCH
        for r in approvers
        for j in r.get("judgments", [])
    ):
        return "no-source-snapshot", {}
    independent = {
        r["reviewer_ref"]
        for r in approvers
        if any(
            j.get("status") not in (None, "not_assessed") and j.get("evidence_ref") in passages
            for j in r.get("judgments", [])
        )
    }
    level = "independently-verified" if len(independent) >= 2 else "source-verified"
    return None, {"passages": passages, "snap_sha": snap_sha, "level": level}


def _paper_rights(rights: dict[str, Any] | None) -> dict[str, Any]:
    """Packet rights block. Only an allowlisted ``allowed`` declaration is ever ``allowed``."""
    if quotes_redistributable(rights):
        return {"license": rights["license_id"], "redistribution": "allowed"}
    license_id = (rights or {}).get("license_id")
    return {
        "license": license_id if isinstance(license_id, str) and license_id else "unknown",
        "redistribution": "metadata-only",
    }


def build_packet(data: dict[str, Any]) -> PacketResult:
    access_level = data.get("access_level") or "restricted"
    metadata_license = data.get("metadata_license")
    if access_level not in ACCESS_LEVELS:
        raise ValueError(f"unknown access level {access_level!r}")
    if metadata_license is not None and metadata_license not in PACKET_METADATA_LICENSES:
        raise ValueError(f"metadata licence {metadata_license!r} is not allowed")
    if access_level == "public" and metadata_license is None:
        raise ValueError("a public packet needs a packet-metadata licence")
    commit = data.get("producer_commit")
    if not (isinstance(commit, str) and _HEX40.match(commit)):
        # The frozen 0.1.0 contract has no "unknown" commit, and producers never invent one.
        raise ValueError("a packet needs the 40-hex producer commit")
    papers = {p["article_id"]: p for p in data.get("papers", [])}

    exported: list[tuple[dict[str, Any], dict[str, Any]]] = []
    omitted: dict[str, int] = {}
    per_finding: list[dict[str, Any]] = []
    for f in sorted(data.get("findings", []), key=lambda x: (x["finding_id"], x["revision"])):
        reason, detail = _evaluate(f, papers)
        entry = {"finding_id": f["finding_id"], "revision": f["revision"]}
        if reason is None:
            exported.append((f, detail))
            per_finding.append({**entry, "exported": True, "reason": None})
        else:
            omitted[reason] = omitted.get(reason, 0) + 1
            per_finding.append({**entry, "exported": False, "reason": reason})

    paper_recs: dict[str, dict[str, Any]] = {}
    passage_recs: dict[str, dict[str, Any]] = {}
    finding_recs: list[dict[str, Any]] = []
    assessment_recs: list[dict[str, Any]] = []
    stamps: list[str] = []
    rights_used: dict[str, dict[str, Any]] = {}

    for f, detail in exported:
        article = papers[f["article_id"]]
        paper_id = "paper:" + article["article_id"]
        snap = article["snapshot"]
        rights = article.get("rights")
        include_quotes = quotes_redistributable(rights)
        if paper_id not in paper_recs:
            ids = {k: _text(article.get(k)) or NA for k in ("doi", "pmid", "pmcid")}
            paper_recs[paper_id] = {
                "paper_id": paper_id,
                "identifiers": ids,
                "title": _text(article.get("title")) or "unknown",
                "authors": [a for a in (_text(x) for x in article.get("authors", [])) if a],
                "source_type": _source_type(list(article.get("pub_types") or [])),
                "source_snapshot": {
                    "state": "captured",
                    "snapshot_ref": str(snap["id"]),
                    "sha256": detail["snap_sha"],
                },
                "rights": {**_paper_rights(rights), "full_text_included": False},
                "producer_ids": {"article_ref": article["article_id"]},
            }
        evidence = {e["evidence_ref"]: e for e in f["evidence"]}
        passages = detail["passages"]
        for ref in sorted(passages):
            pid = "passage:" + ref
            if pid in passage_recs:
                continue
            p = passages[ref]
            quote = evidence[ref]["quote"]
            used = rights_used.setdefault(
                article["article_id"],
                {
                    "article_id": article["article_id"],
                    "declaration_revision": (rights or {}).get("revision"),
                    "redistribution": _paper_rights(rights)["redistribution"],
                    "license_id": _paper_rights(rights)["license"],
                    "quotes_included": 0,
                    "quotes_withheld": 0,
                },
            )
            used["quotes_included" if include_quotes else "quotes_withheld"] += 1
            passage_recs[pid] = {
                "passage_id": pid,
                "paper_id": paper_id,
                "quote_state": "included" if include_quotes else "withheld-rights",
                "quote": quote if include_quotes else "withheld",
                "quote_sha256": _sha(quote.encode("utf-8")),
                "source": {
                    "snapshot_ref": str(snap["id"]),
                    "view_id": p["view_id"],
                    "start": p["start"],
                    "end": p["end"],
                },
                "match": {
                    "receipt_ref": p["receipt_ref"],
                    "status": p["status"],
                    "matcher": p["matcher"],
                },
                "producer_ids": {"evidence_ref": ref},
            }
        content = f["content"]
        producer_ids: dict[str, Any] = {"evidence_refs": sorted(passages)}
        answers = {evidence[r]["answer_ref"] for r in passages}
        predictions = {evidence[r]["prediction_id"] for r in passages}
        if len(answers) == 1:
            producer_ids["answer_ref"] = next(iter(answers))
        if len(predictions) == 1:
            producer_ids["prediction_ref"] = str(next(iter(predictions)))
        created = _ts(f["created_at"])
        stamps.append(created)
        finding_id = "finding:" + f["finding_id"]
        finding_recs.append(
            {
                "finding_id": finding_id,
                "revision": f["revision"],
                "paper_id": paper_id,
                "statement": content["statement"],
                "origin": content["origin"],
                "export_state": "approved-for-export",
                "review_state": "human-reviewed",
                "verification_level": detail["level"],
                "context": content["context"],
                "numeric_results": content.get("numeric_results", []),
                "passage_ids": ["passage:" + r for r in sorted(passages)],
                "producer_ids": producer_ids,
                "created_at": created,
            }
        )
        for review in sorted(f["reviews"], key=lambda r: str(r["id"])):
            judgments = sorted(review.get("judgments", []), key=lambda j: j["evidence_ref"])
            assessed_at = _ts(review["created_at"])
            for n, j in enumerate(judgments, start=1):
                if j["evidence_ref"] not in passages:
                    continue
                status = j["status"]
                rationale = _text(j.get("rationale")) if status in JUDGMENTS else None
                if status in JUDGMENTS and rationale is None:
                    continue  # a judgment without rationale cannot be expressed; never invent
                stamps.append(assessed_at)
                assessment_recs.append(
                    {
                        "assessment_id": f"assessment:{review['id']}-{n}",
                        "finding_id": finding_id,
                        "finding_revision": f["revision"],
                        "passage_id": "passage:" + j["evidence_ref"],
                        "protocol": "seip-support-v1",
                        "status": status,
                        "rationale": rationale or NA,
                        "reviewer_ref": _reviewer_ref(review["reviewer_ref"]),
                        "assessed_at": assessed_at,
                        "assessed_snapshot_sha256": detail["snap_sha"],
                        "producer_ids": {"support_assessment_ref": str(review["id"])},
                    }
                )

    records = {
        "papers": sorted(paper_recs.values(), key=lambda r: r["paper_id"]),
        "findings": sorted(finding_recs, key=lambda r: (r["finding_id"], r["revision"])),
        "passages": sorted(passage_recs.values(), key=lambda r: r["passage_id"]),
        "assessments": sorted(assessment_recs, key=lambda r: r["assessment_id"]),
    }
    files: dict[str, bytes] = {f"{name}.jsonl": _jsonl(records[name]) for name in ARTIFACTS}
    artifacts = {
        name: {
            "path": f"{name}.jsonl",
            "sha256": _sha(files[f"{name}.jsonl"]),
            "bytes": len(files[f"{name}.jsonl"]),
            "record_count": len(records[name]),
        }
        for name in ARTIFACTS
    }
    n_omitted = sum(omitted.values())
    if not exported:
        state = "withheld"
        reasons = [f"{k}={v}" for k, v in sorted(omitted.items())] or ["no-findings-input"]
    elif n_omitted:
        state = "partial"
        reasons = [f"{k}={v}" for k, v in sorted(omitted.items())]
    else:
        state = "complete"
        reasons = []
    digest = hashlib.sha256(
        "\n".join(artifacts[n]["sha256"] for n in ARTIFACTS).encode()
    ).hexdigest()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "packet_id": "packet:" + digest[:32],
        "created_at": max(stamps) if stamps else EMPTY_TIMESTAMP,
        "creation_method": "producer-export",
        "producer": {
            "name": "diggypaper",
            "commit": commit,
            "schema_refs": [
                {"name": "evidence-envelope-v1", "version": "1"},
                {"name": "seip-support-v1", "version": "1"},
                {"name": "quote-matcher-v1", "version": "1"},
            ],
        },
        "artifacts": artifacts,
        "licenses": [
            {"license_id": metadata_license or "unknown", "applies_to": "packet-metadata"},
            *(
                {"license_id": license_id, "applies_to": "quotes"}
                for license_id in sorted(
                    {u["license_id"] for u in rights_used.values() if u["quotes_included"]}
                )
            ),
        ],
        "access_scope": {"level": access_level, "redistributable_full_text": False},
        "export_status": {
            "state": state,
            "withheld_counts": {
                "papers": 0,
                "findings": n_omitted,
                "passages": 0,
                "assessments": 0,
            },
            "reasons": reasons,
        },
    }
    files["manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    report = {
        "state": state,
        "exported": len(exported),
        "omitted": n_omitted,
        "omitted_by_reason": dict(sorted(omitted.items())),
        "access_level": access_level,
        "rights": [rights_used[k] for k in sorted(rights_used)],
        "findings": per_finding,
    }
    return PacketResult(files=dict(sorted(files.items())), report=report)
