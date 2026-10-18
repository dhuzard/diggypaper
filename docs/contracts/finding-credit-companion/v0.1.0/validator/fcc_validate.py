#!/usr/bin/env python3
"""Offline, deterministic validator for finding-credit-companion/0.1.0.

Usage:
    fcc_validate.py COMPANION_JSON --packet PACKET_DIR [--as-of TIMESTAMP]

Exit codes: 0 valid, 1 invalid, 2 usage error.

A companion is never checked on its own: it is meaningful only against the exact
source-evidence-packet/0.1.0 it binds, so ``--packet`` is required. The packet is checked
with the frozen packet validator (``sep_validate.py``, allow-restricted mode, since audience
versus packet scope is judged here) and must validate before any binding is trusted.

Only the standard library and ``jsonschema`` are used. No network access, no clock reads and
no randomness: the output depends only on the input bytes, the schemas and ``--as-of``.
Importable entry point: ``validate_companion(companion_path, packet_dir, as_of=None)``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "finding-credit-companion/0.1.0"
HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE.parent / "schemas" / "finding-credit-companion.schema.json"
SEP_VALIDATOR = (
    HERE.parents[2] / "source-evidence-packet" / "v0.1.0" / "validator" / "sep_validate.py"
)
FILE = "companion"
PACKET = "packet"

ACTIVE_STATUSES = {"source-declared", "self-declared", "confirmed"}
INACTIVE_STATUSES = {"withheld", "disputed", "withdrawn"}
NOT_REDISTRIBUTABLE = {"restricted", "unknown"}
# Packet access levels a companion audience may be shared from (README section 6).
AUDIENCE_PACKET_LEVELS = {
    "public": {"public"},
    "named-recipient": {"public", "restricted"},
    "project-members": {"public", "restricted", "private", "embargoed"},
}
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_BARE_DIGEST = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
# Fields whose patterns legitimately carry 64 hex: digests and project-scoped pseudonyms.
_DIGEST_KEYS = {"packet_digest", "source_snapshot_sha256", "ref"}


def _load_sep():
    spec = importlib.util.spec_from_file_location("sep_validate_fcc", SEP_VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sep_validate_fcc"] = module
    spec.loader.exec_module(module)
    return module


sep = _load_sep()


class _Report:
    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []

    def error(self, code: str, rid: str, message: str, file: str = FILE) -> None:
        self.errors.append({"code": code, "file": file, "id": rid, "message": message})

    def warn(self, code: str, rid: str, message: str, file: str = FILE) -> None:
        self.warnings.append({"code": code, "file": file, "id": rid, "message": message})


def _sort_key(item: dict[str, str]) -> tuple[str, str, str, str]:
    return (item["code"], item["file"], item["id"], item["message"])


def _dedupe_sorted(items: list[dict[str, str]]) -> list[dict[str, str]]:
    out: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for item in items:
        out.setdefault(_sort_key(item), item)
    return [out[k] for k in sorted(out)]


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _t(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def orcid_checksum_ok(orcid: str) -> bool:
    """ISO 7064 MOD 11-2 check digit, as specified by ORCID."""
    digits = orcid.replace("-", "")
    total = 0
    for ch in digits[:-1]:
        total = (total + int(ch)) * 2
    result = (12 - total % 11) % 11
    return digits[-1] == ("X" if result == 10 else str(result))


def _read_companion(path: Path, rep: _Report) -> tuple[dict[str, Any] | None, bytes | None]:
    if not path.is_file():
        rep.error("artifact-missing", "-", f"{path.name} is missing")
        return None, None
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        doc = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        rep.error("invalid-json", "-", f"companion is not valid JSON: {exc}")
        return None, raw
    if not isinstance(doc, dict):
        rep.error("invalid-json", "-", "companion must be a JSON object")
        return None, raw
    canonical = "\r" not in text and text == (
        json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    if not canonical:
        rep.error(
            "non-canonical-encoding",
            "-",
            "companion must be sorted-key JSON, indent 2, LF line ends, final newline",
        )
    return doc, raw


def _strings(obj: Any, path: str = ""):
    if isinstance(obj, str):
        yield path or "/", obj
    elif isinstance(obj, dict):
        for k in sorted(obj):
            yield from _strings(obj[k], f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{path}/{i}")


def _check_leaks(doc: dict[str, Any], rep: _Report) -> None:
    """No email or bare 64-hex digest (a cross-project correlator) anywhere in the companion."""
    for path, value in _strings(doc):
        if _EMAIL.search(value):
            rep.error("private-identifier-leak", path, "value looks like an email address")
        elif path.rsplit("/", 1)[-1] not in _DIGEST_KEYS and _BARE_DIGEST.search(value):
            rep.error("private-identifier-leak", path, "bare 64-hex digest (possible correlator)")


def _check_header(doc: dict[str, Any], rep: _Report, as_of: str | None) -> None:
    created = doc["created_at"]
    audience, release, status = doc["audience"], doc["release"], doc["export_status"]
    level = audience["level"]
    named = audience["recipient_ref"] != "not-available"
    if (level == "named-recipient") != named:
        rep.error("audience-inconsistent", "audience", "recipient_ref iff named-recipient")
    expires = audience["expires_at"]
    if expires == "not-available":
        if level != "public":
            rep.error("handoff-expiry-missing", "audience", f"{level} handoff needs expires_at")
    else:
        if _t(expires) <= _t(created):
            rep.error("handoff-expired", "audience", "expires_at is not after created_at")
        if as_of is not None and _t(expires) <= _t(as_of):
            rep.error("handoff-expired", "audience", f"expired at {expires} (as of {as_of})")
    approved = release["state"] == "controller-approved"
    if approved:
        if release["approval_ref"] == "not-available" or release["approved_at"] == "not-available":
            rep.error("release-inconsistent", "release", "approval needs approval_ref and time")
        elif _t(release["approved_at"]) > _t(created):
            rep.error("release-inconsistent", "release", "approved_at is after created_at")
    elif release["approval_ref"] != "not-available" or release["approved_at"] != "not-available":
        rep.error("release-inconsistent", "release", "not-released carries approval data")
    if level != "project-members" and not approved:
        rep.error("release-not-approved", "release", f"{level} handoff needs controller approval")
    counts = status["withheld_counts"]
    omitted = counts["entries"] + counts["contributions"]
    if status["state"] == "complete" and omitted:
        rep.error("completeness-fabricated", "export_status", "complete with withheld counts")
    if status["state"] == "partial" and not omitted:
        rep.error("completeness-fabricated", "export_status", "partial with no withheld count")
    if status["state"] == "withheld" and (doc["entries"] or not status["reasons"]):
        rep.error("silent-omission", "export_status", "withheld needs no entries and reasons")


def _check_contribution(
    c: dict[str, Any],
    entry: dict[str, Any],
    assessments: dict[str, dict[str, Any]],
    authors: set[str],
    rep: _Report,
) -> None:
    cid = c["contribution_id"]
    who = c["contributor"]
    kind = who["kind"]
    status = c["status"]
    disc = c["disclosure"]
    binding = entry["binding"]
    if status == "withheld" and kind != "withheld":
        rep.error("withheld-status-inconsistent", cid, "withheld status with a visible identity")
    if c["credit_roles"] and status in INACTIVE_STATUSES:
        rep.error("credit-role-not-asserted", cid, f"CRediT roles on a {status} contribution")
    roles = [r["role"] for r in c["credit_roles"]]
    if len(roles) != len(set(roles)):
        rep.error("duplicate-credit-role", cid, "a CRediT role is listed twice")

    # Capacity and assessment binding: only an assessor carries assessments.
    if c["capacity"] == "support-assessor":
        if not c["assessment_ids"]:
            rep.error("assessor-binding-mismatch", cid, "support-assessor without assessments")
        if c["review_blinding"] == "not-applicable":
            rep.error("assessor-binding-mismatch", cid, "support-assessor needs review_blinding")
        for aid in c["assessment_ids"]:
            if aid not in binding["assessment_ids"]:
                rep.error("assessor-binding-mismatch", cid, f"{aid} is not bound by the entry")
            elif kind == "pseudonym" and aid in assessments:
                if assessments[aid]["reviewer_ref"] != who["ref"]:
                    rep.error("assessor-pseudonym-mismatch", cid, f"{aid} has another reviewer")
    else:
        if c["assessment_ids"]:
            rep.error("assessor-binding-mismatch", cid, f"{c['capacity']} carries assessments")
        if c["review_blinding"] != "not-applicable":
            rep.error("assessor-binding-mismatch", cid, "review_blinding is assessor-only")

    if kind == "disclosed":
        if c["capacity"] == "support-assessor" and c["review_blinding"] != "unblinded":
            rep.error("blind-reviewer-disclosed", cid, "a blinded or unknown review is disclosed")
        if status in INACTIVE_STATUSES:
            rep.error("inactive-assertion-disclosed", cid, f"identity disclosed while {status}")
        revoked = "revoked" in (
            disc["person_consent"],
            disc["controller_authorization"],
            disc["membership"],
        )
        if revoked:
            rep.error("consent-revoked", cid, "consent, authorization or membership revoked")
        elif (
            disc["person_consent"] != "granted"
            or disc["controller_authorization"] != "granted"
            or disc["membership"] != "active"
            or disc["consent_ref"] == "not-available"
        ):
            rep.error("consent-missing", cid, "disclosure needs consent, authorization, membership")
        orcid = who["orcid"]
        if isinstance(orcid, dict):
            verified = orcid["verification"] == "oauth-verified"
            has_proof = (
                orcid["verified_at"] != "not-available"
                and orcid["verification_ref"] != "not-available"
            )
            no_proof = (
                orcid["verified_at"] == "not-available"
                and orcid["verification_ref"] == "not-available"
            )
            if (verified and not has_proof) or (not verified and not no_proof):
                rep.error("orcid-verification-inconsistent", cid, "verification and proof differ")
            if not orcid_checksum_ok(orcid["id"]):
                rep.error("orcid-checksum-invalid", cid, f"{orcid['id']} fails MOD 11-2")
        name = who["display_name"].casefold().strip()
        if any(name == p["name"].casefold().strip() for p in entry["production"]):
            rep.error("software-as-person", cid, "a production agent is credited as a person")
        if name in authors:
            rep.warn(
                "cited-author-name-match",
                cid,
                "display name equals a cited author; credit is never inferred from authorship",
            )


def _check_entries(
    doc: dict[str, Any], packet: dict[str, list[Any]], manifest: dict[str, Any], rep: _Report
) -> None:
    digest = doc["packet"]["packet_digest"]
    level = doc["audience"]["level"]
    findings: dict[tuple[str, int], dict[str, Any]] = {
        (f["finding_id"], f["revision"]): f for f in packet["findings"]
    }
    latest: dict[str, int] = {}
    for fid, rev in findings:
        latest[fid] = max(latest.get(fid, 0), rev)
    papers = {p["paper_id"]: p for p in packet["papers"]}
    assessments = {a["assessment_id"]: a for a in packet["assessments"]}
    if manifest["access_scope"]["level"] not in AUDIENCE_PACKET_LEVELS[level]:
        rep.error(
            "audience-exceeds-packet-scope",
            "audience",
            f"{level} audience on a {manifest['access_scope']['level']} packet",
        )
    seen_entries: set[str] = set()
    seen_contribs: set[str] = set()
    seen_bindings: set[tuple[str, int]] = set()
    for entry in doc["entries"]:
        eid = entry["entry_id"]
        b = entry["binding"]
        if eid in seen_entries:
            rep.error("duplicate-id", eid, "duplicate entry_id")
        seen_entries.add(eid)
        for c in entry["contributions"]:
            if c["contribution_id"] in seen_contribs:
                rep.error("duplicate-id", c["contribution_id"], "duplicate contribution_id")
            seen_contribs.add(c["contribution_id"])
        key = (b["finding_id"], b["finding_revision"])
        if key in seen_bindings:
            rep.error("duplicate-entry", eid, f"{key[0]} rev {key[1]} has two entries")
        seen_bindings.add(key)
        if b["packet_digest"] != digest:
            rep.error("packet-digest-mismatch", eid, "entry is bound to another packet")
        authors: set[str] = set()
        bound_assessments: dict[str, dict[str, Any]] = {}
        f = findings.get(key)
        if b["finding_id"] not in latest:
            rep.error("dangling-finding-ref", eid, f"{b['finding_id']} is not in the packet")
        elif f is None:
            rep.error("dangling-finding-ref", eid, f"{key[0]} rev {key[1]} is not in the packet")
        else:
            if key[1] < latest[key[0]]:
                rep.error("stale-finding-revision", eid, f"latest is rev {latest[key[0]]}")
            if f["paper_id"] != b["paper_id"] or f["origin"] != b["finding_origin"]:
                rep.error("finding-binding-mismatch", eid, "paper_id or origin differs")
            if level != "project-members" and f["export_state"] != "approved-for-export":
                rep.error("unapproved-finding-shared", eid, f"{level} entry on a proposed finding")
            if sorted(b["passage_ids"]) != sorted(f["passage_ids"]):
                rep.error("evidence-binding-mismatch", eid, "passage_ids differ from the finding")
            native = sorted(
                a["assessment_id"]
                for a in packet["assessments"]
                if (a["finding_id"], a["finding_revision"]) == key
            )
            if sorted(b["assessment_ids"]) != native:
                rep.error("evidence-binding-mismatch", eid, "assessment_ids differ from packet")
            bound_assessments = {
                aid: assessments[aid] for aid in b["assessment_ids"] if aid in assessments
            }
            paper = papers.get(f["paper_id"])
            if paper is not None:
                authors = {a.casefold().strip() for a in paper["authors"]}
                snap = paper["source_snapshot"]
                if (
                    snap["snapshot_ref"] != b["source_snapshot_ref"]
                    or snap["sha256"] != b["source_snapshot_sha256"]
                ):
                    rep.error("evidence-digest-changed", eid, "source snapshot differs")
                if (
                    level != "project-members"
                    and paper["rights"]["redistribution"] in NOT_REDISTRIBUTABLE
                ):
                    rep.error(
                        "restricted-redistribution",
                        eid,
                        f"paper redistribution is {paper['rights']['redistribution']}",
                    )
            for a in bound_assessments.values():
                if a["assessed_snapshot_sha256"] != b["source_snapshot_sha256"]:
                    rep.error("evidence-digest-changed", eid, f"{a['assessment_id']} snapshot")
        for c in entry["contributions"]:
            _check_contribution(c, entry, bound_assessments, authors, rep)


def _counts(doc: dict[str, Any] | None) -> dict[str, Any]:
    if not doc or not isinstance(doc.get("entries"), list):
        return {"entries": 0}
    contribs = [
        c
        for e in doc["entries"]
        if isinstance(e, dict)
        for c in e.get("contributions", [])
        if isinstance(c, dict)
    ]

    def tally(key):
        return dict(sorted(Counter(str(key(c)) for c in contribs).items()))

    return {
        "entries": len(doc["entries"]),
        "contributions": len(contribs),
        "contributions_by_capacity": tally(lambda c: c.get("capacity")),
        "contributions_by_status": tally(lambda c: c.get("status")),
        "contributors_by_kind": tally(lambda c: (c.get("contributor") or {}).get("kind")),
        "orcid_by_verification": dict(
            sorted(
                Counter(
                    c["contributor"]["orcid"]["verification"]
                    for c in contribs
                    if isinstance((c.get("contributor") or {}).get("orcid"), dict)
                ).items()
            )
        ),
        "credit_roles": sum(len(c.get("credit_roles", [])) for c in contribs),
    }


def validate_companion(
    companion_path: str | Path, packet_dir: str | Path, as_of: str | None = None
) -> dict[str, Any]:
    """Validate a companion against the packet it binds; deterministic result object."""
    companion_path, packet_dir = Path(companion_path), Path(packet_dir)
    rep = _Report()
    doc, raw = _read_companion(companion_path, rep)
    packet_result = sep.validate_packet(packet_dir, sep.MODE_ALLOW_RESTRICTED)
    packet_ok = packet_result["valid"]
    if not packet_ok:
        codes = sorted({e["code"] for e in packet_result["errors"]})
        rep.error("packet-invalid", "-", f"bound packet fails validation: {codes}", PACKET)
    schema_ok = False
    if doc is not None:
        if doc.get("schema_version") != SCHEMA_VERSION:
            rep.error(
                "unsupported-schema-version",
                "schema_version",
                f"expected {SCHEMA_VERSION}, got {doc.get('schema_version')!r}",
            )
        else:
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            errs = sorted(
                Draft202012Validator(schema).iter_errors(doc),
                key=lambda e: ("".join(f"/{p}" for p in e.absolute_path), e.message),
            )
            for e in errs:
                path = "".join(f"/{p}" for p in e.absolute_path) or "/"
                rep.error("schema-invalid", path, e.message)
            schema_ok = not errs
        _check_leaks(doc, rep)
    if doc is not None and schema_ok:
        _check_header(doc, rep, as_of)
        if packet_ok:
            if doc["packet"]["packet_digest"] != packet_result["packet_digest"]:
                rep.error("packet-digest-mismatch", "packet", "digest differs from the packet")
            manifest = json.loads((packet_dir / "manifest.json").read_text(encoding="utf-8"))
            if doc["packet"]["packet_id"] != manifest["packet_id"]:
                rep.error("packet-digest-mismatch", "packet", "packet_id differs from the packet")
            records = {
                kind: [
                    json.loads(line)
                    for line in (packet_dir / f"{kind}.jsonl").read_text("utf-8").splitlines()
                ]
                for kind in ("papers", "findings", "assessments")
            }
            _check_entries(doc, records, manifest, rep)
    errors = _dedupe_sorted(rep.errors)
    result: dict[str, Any] = {
        "valid": not errors,
        "errors": errors,
        "warnings": _dedupe_sorted(rep.warnings),
        "counts": _counts(doc if schema_ok else None),
    }
    if result["valid"] and raw is not None:
        # Same digest rule as the packet (source-evidence-packet README section 2.1).
        result["companion_digest"] = _sha256_hex(raw)
        result["packet_digest"] = packet_result["packet_digest"]
    return result


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fcc_validate.py",
        description="Validate a finding-credit-companion/0.1.0 against its source evidence packet.",
    )
    parser.add_argument("companion", help="path to the companion JSON file")
    parser.add_argument("--packet", required=True, help="the source-evidence-packet directory")
    parser.add_argument(
        "--as-of",
        help="RFC 3339 UTC time the consumer uses; an expired handoff is refused",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)  # argparse exits with status 2 on usage errors
    if not Path(args.packet).is_dir():
        print(f"error: {args.packet} is not a directory", file=sys.stderr)
        return 2
    if args.as_of is not None and not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", args.as_of
    ):
        print("error: --as-of must be RFC 3339 UTC, e.g. 2027-01-01T00:00:00Z", file=sys.stderr)
        return 2
    result = validate_companion(args.companion, args.packet, args.as_of)
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
