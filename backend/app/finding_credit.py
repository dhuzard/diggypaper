"""Pure builder of a finding-credit-companion/0.1.0 for one source-evidence-packet/0.1.0.

No database, clock, environment or randomness: the same input yields identical bytes. The
companion is bound to the exact packet bytes (packet digest = sha256 of manifest.json) and to
each finding revision, paper snapshot and native seip-support-v1 assessment in that packet.

This builder never discloses a person. Every contributor is a project-scoped pseudonym
(app.pseudonym.reviewer_reference) or ``attribution-withheld``: disclosure needs per-person
consent and controller authorization records that do not exist yet (ORAtlas #202). Nothing is
inferred: the proposer comes from the finding record, assessors from the packet's own
assessments, curators, team members and every CRediT role only from explicit declarations.
Cited paper authors stay bibliographic data in the packet and are never credited here.

It is not exposed through any route; wiring it into an export waits on ORAtlas #202.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.finding_packet import _reviewer_ref, _ts

SCHEMA_VERSION = "finding-credit-companion/0.1.0"
PACKET_SCHEMA_VERSION = "source-evidence-packet/0.1.0"
NA = "not-available"
AUDIENCES = {"public", "named-recipient", "project-members"}
ACTIVE_STATUSES = {"source-declared", "self-declared", "confirmed"}
DECLARATION_STATUSES = ACTIVE_STATUSES | {"withheld", "disputed", "withdrawn"}
DECLARED_CAPACITIES = {"finding-proposer", "finding-curator", "support-assessor", "project-team"}
CREDIT_ROLES = {
    "Conceptualization",
    "Data curation",
    "Formal analysis",
    "Funding acquisition",
    "Investigation",
    "Methodology",
    "Project administration",
    "Resources",
    "Software",
    "Supervision",
    "Validation",
    "Visualization",
    "Writing – original draft",
    "Writing – review & editing",
}
NOT_REDISTRIBUTABLE = {"restricted", "unknown"}
# Packet access levels each audience may be handed (companion README section 6).
AUDIENCE_PACKET_LEVELS = {
    "public": {"public"},
    "named-recipient": {"public", "restricted"},
    "project-members": {"public", "restricted", "private", "embargoed"},
}
WITHHELD = {"kind": "withheld", "ref": "attribution-withheld"}
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_PSEUDONYM = re.compile(r"^rv-[0-9a-f]{16,64}$")
_DECLARATION_REF = re.compile(r"^declaration:[A-Za-z0-9._-]{1,100}$")


@dataclass(frozen=True)
class CompanionResult:
    data: bytes
    report: dict[str, Any] = field(default_factory=dict)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _records(files: dict[str, bytes], name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in files[f"{name}.jsonl"].decode("utf-8").splitlines()]


def _pseudonym(ref: Any) -> str | None:
    if not isinstance(ref, str) or not ref:
        return None
    out = _reviewer_ref(ref)
    return out if _PSEUDONYM.match(out) else None


def _roles(declaration: dict[str, Any]) -> list[dict[str, Any]]:
    """Explicit CRediT roles of an active declaration; anything else carries none."""
    if declaration.get("status") not in ACTIVE_STATUSES:
        return []
    roles: dict[str, dict[str, Any]] = {}
    for r in declaration.get("roles", []):
        if r.get("role") not in CREDIT_ROLES:
            raise ValueError(f"{r.get('role')!r} is not a CRediT role")
        if not _DECLARATION_REF.match(str(r.get("declaration_ref", ""))):
            raise ValueError("a CRediT role needs its declaration_ref")
        roles.setdefault(
            r["role"],
            {
                "role": r["role"],
                "degree": r.get("degree") or NA,
                "declaration_ref": r["declaration_ref"],
            },
        )
    return [roles[k] for k in sorted(roles)]


def _contribution(
    cid: str,
    capacity: str,
    status: str,
    ref: str | None,
    roles: list[dict[str, Any]],
    assessments: tuple[str, ...] | list[str] = (),
    blinding: str = "not-applicable",
) -> dict[str, Any]:
    contributor = {"kind": "pseudonym", "ref": ref} if ref and status != "withheld" else WITHHELD
    return {
        "contribution_id": cid,
        "capacity": capacity,
        "status": status,
        "contributor": contributor,
        "credit_roles": roles,
        "assessment_ids": sorted(assessments),
        "review_blinding": blinding,
        "disclosure": {
            "person_consent": "not-granted",
            "controller_authorization": "not-granted",
            "membership": "unknown",
            "consent_ref": NA,
        },
    }


def _production(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in items:
        if p.get("agent_type") not in ("software", "ml-model"):
            raise ValueError("a production agent is software or ml-model, never a person")
        out.append(
            {
                "agent_type": p["agent_type"],
                "name": str(p["name"]),
                "version": str(p.get("version") or "unknown"),
                "activity": p["activity"],
                "run_ref": str(p.get("run_ref") or NA),
            }
        )
    return out


def _blinding(review_ids: list[str], blinded: dict[str, Any]) -> str:
    values = {blinded.get(r) for r in review_ids}
    if values == {True}:
        return "blinded"
    if values == {False}:
        return "unblinded"
    return "unknown"


def build_credit_companion(packet_files: dict[str, bytes], data: dict[str, Any]) -> CompanionResult:
    commit = data.get("producer_commit")
    if not (isinstance(commit, str) and _HEX40.match(commit)):
        raise ValueError("a companion needs the 40-hex producer commit")
    audience = dict(data.get("audience") or {})
    level = audience.get("level")
    if level not in AUDIENCES:
        raise ValueError(f"unknown audience {level!r}")
    purpose = audience.get("purpose")
    if not (isinstance(purpose, str) and purpose.strip()):
        raise ValueError("a handoff needs a stated purpose")
    expires = _ts(audience["expires_at"]) if audience.get("expires_at") else NA
    if level != "public" and expires == NA:
        raise ValueError(f"a {level} handoff needs expires_at")
    recipient = audience.get("recipient_ref") or NA
    if (level == "named-recipient") != (recipient != NA):
        raise ValueError("recipient_ref is required for, and only for, a named recipient")
    release_in = data.get("release")
    if release_in:
        release = {
            "state": "controller-approved",
            "approval_ref": release_in["approval_ref"],
            "approved_at": _ts(release_in["approved_at"]),
        }
    elif level == "project-members":
        release = {"state": "not-released", "approval_ref": NA, "approved_at": NA}
    else:
        # Release of evidence and credit is a controller decision, never a default.
        raise ValueError(f"a {level} handoff needs a controller-approved release")

    manifest_bytes = packet_files["manifest.json"]
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("schema_version") != PACKET_SCHEMA_VERSION:
        raise ValueError("the companion binds source-evidence-packet/0.1.0 only")
    if manifest["access_scope"]["level"] not in AUDIENCE_PACKET_LEVELS[level]:
        raise ValueError(f"a {level} audience exceeds a {manifest['access_scope']['level']} packet")
    digest = _sha(manifest_bytes)
    papers = {p["paper_id"]: p for p in _records(packet_files, "papers")}
    passages = {p["passage_id"]: p for p in _records(packet_files, "passages")}
    assessments = _records(packet_files, "assessments")
    findings = _records(packet_files, "findings")
    latest: dict[str, int] = {}
    for f in findings:
        latest[f["finding_id"]] = max(latest.get(f["finding_id"], 0), f["revision"])

    inputs = {(i["finding_id"], i["revision"]): i for i in data.get("findings", [])}
    declarations: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for d in data.get("declarations", []):
        if d.get("capacity") not in DECLARED_CAPACITIES:
            raise ValueError(f"unknown capacity {d.get('capacity')!r}")
        if d.get("status") not in DECLARATION_STATUSES:
            raise ValueError(f"unknown declaration status {d.get('status')!r}")
        declarations.setdefault((d["finding_id"], d["revision"]), []).append(d)

    entries: list[dict[str, Any]] = []
    omitted: dict[str, int] = {}
    unmatched = 0
    for f in sorted(findings, key=lambda x: (x["finding_id"], x["revision"])):
        fid, rev = f["finding_id"], f["revision"]
        if rev != latest[fid]:
            continue  # credit binds the latest revision only; older ones are not handed off
        paper = papers[f["paper_id"]]
        reason = None
        if level != "project-members":
            if f["export_state"] != "approved-for-export":
                reason = "unapproved-finding"
            elif paper["rights"]["redistribution"] in NOT_REDISTRIBUTABLE:
                reason = "restricted-redistribution"
        if reason:
            omitted[reason] = omitted.get(reason, 0) + 1
            continue
        bare = fid.removeprefix("finding:")
        key = (bare, rev)
        source = inputs.get(key, {})
        decls = declarations.get(key, [])
        native = sorted(
            (a for a in assessments if (a["finding_id"], a["finding_revision"]) == (fid, rev)),
            key=lambda a: a["assessment_id"],
        )
        stem = f"{bare}-r{rev}"
        used: set[int] = set()

        def declared(capacity: str, ref: str | None, _decls=decls, _used=used):
            for n, d in enumerate(_decls):
                if d["capacity"] == capacity and _pseudonym(d.get("person_ref")) == ref:
                    _used.add(n)
                    return d
            return None

        contributions: list[dict[str, Any]] = []
        proposer = _pseudonym(source.get("proposer_ref"))
        d = declared("finding-proposer", proposer) if proposer else None
        contributions.append(
            _contribution(
                f"contribution:{stem}-proposer",
                "finding-proposer",
                d["status"] if d else ("source-declared" if proposer else "withheld"),
                proposer,
                _roles(d) if d else [],
            )
        )
        by_reviewer: dict[str, list[dict[str, Any]]] = {}
        for a in native:
            by_reviewer.setdefault(a["reviewer_ref"], []).append(a)
        blind = source.get("review_blinding") or {}
        for n, (ref, items) in enumerate(sorted(by_reviewer.items()), start=1):
            d = declared("support-assessor", ref)
            contributions.append(
                _contribution(
                    f"contribution:{stem}-assessor-{n}",
                    "support-assessor",
                    d["status"] if d else "source-declared",
                    ref,
                    _roles(d) if d else [],
                    [a["assessment_id"] for a in items],
                    _blinding([a["producer_ids"]["support_assessment_ref"] for a in items], blind),
                )
            )
        extra = [
            (n, d)
            for n, d in enumerate(decls)
            if n not in used and d["capacity"] in ("finding-curator", "project-team")
        ]
        for i, (n, d) in enumerate(extra, start=1):
            used.add(n)
            contributions.append(
                _contribution(
                    f"contribution:{stem}-{d['capacity']}-{i}",
                    d["capacity"],
                    d["status"],
                    _pseudonym(d.get("person_ref")),
                    _roles(d),
                )
            )
        unmatched += len(decls) - len(used)
        production = _production(source.get("production", []))
        if any(passages[p]["match"]["matcher"] == "quote-matcher-v1" for p in f["passage_ids"]):
            production.append(
                {
                    "agent_type": "software",
                    "name": "quote-matcher-v1",
                    "version": "1",
                    "activity": "quote-matching",
                    "run_ref": NA,
                }
            )
        snap = paper["source_snapshot"]
        entries.append(
            {
                "entry_id": f"credit-entry:{stem}",
                "binding": {
                    "packet_digest": digest,
                    "finding_id": fid,
                    "finding_revision": rev,
                    "finding_origin": f["origin"],
                    "paper_id": f["paper_id"],
                    "source_snapshot_ref": snap["snapshot_ref"],
                    "source_snapshot_sha256": snap["sha256"],
                    "passage_ids": list(f["passage_ids"]),
                    "assessment_protocol": "seip-support-v1",
                    "assessment_ids": [a["assessment_id"] for a in native],
                },
                "contributions": contributions,
                "production": production,
            }
        )

    n_omitted = sum(omitted.values())
    if not entries:
        state = "withheld"
        reasons = [f"{k}={v}" for k, v in sorted(omitted.items())] or ["no-findings-input"]
    elif n_omitted:
        state, reasons = "partial", [f"{k}={v}" for k, v in sorted(omitted.items())]
    else:
        state, reasons = "complete", []
    created = manifest["created_at"]
    if release["approved_at"] != NA:
        created = max(created, release["approved_at"])
    if expires != NA and expires <= created:
        raise ValueError("the handoff expires before the companion is created")
    companion_key = json.dumps([digest, level, recipient, expires], ensure_ascii=False)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "companion_id": "credit:" + hashlib.sha256(companion_key.encode()).hexdigest()[:32],
        "created_at": created,
        "creation_method": "producer-export",
        "producer": {"name": "diggypaper", "commit": commit},
        "packet": {
            "schema_version": PACKET_SCHEMA_VERSION,
            "packet_id": manifest["packet_id"],
            "packet_digest": digest,
        },
        "audience": {
            "level": level,
            "recipient_ref": recipient,
            "purpose": purpose.strip(),
            "expires_at": expires,
        },
        "release": release,
        "credit_semantics": {
            "role_vocabulary": "CRediT ANSI/NISO Z39.104-2022",
            "implies_authorship": False,
        },
        "entries": entries,
        "export_status": {
            "state": state,
            "withheld_counts": {"entries": n_omitted, "contributions": 0},
            "reasons": reasons,
        },
    }
    out = (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    report = {
        "state": state,
        "entries": len(entries),
        "omitted_by_reason": dict(sorted(omitted.items())),
        "unmatched_declarations": unmatched,
        "packet_digest": digest,
    }
    return CompanionResult(data=out, report=report)
