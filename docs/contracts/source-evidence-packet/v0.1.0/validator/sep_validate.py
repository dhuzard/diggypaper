#!/usr/bin/env python3
"""Offline, deterministic validator for source-evidence-packet/0.1.0.

Usage:
    sep_validate.py PACKET_DIR [--for-import | --allow-restricted] [--mapping-report]
                    [--min-verification LEVEL]

Exit codes: 0 valid, 1 invalid, 2 usage error.

Only the standard library and ``jsonschema`` are used. No network access, no clock
reads and no randomness: the output depends only on the packet bytes and the schemas.
Importable entry point: ``validate_packet(directory, mode)``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_VERSION = "source-evidence-packet/0.1.0"
SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"

MODE_FOR_IMPORT = "for-import"
MODE_ALLOW_RESTRICTED = "allow-restricted"

MANIFEST_FILE = "manifest.json"
ARTIFACT_FILES = {
    "papers": "papers.jsonl",
    "findings": "findings.jsonl",
    "passages": "passages.jsonl",
    "assessments": "assessments.jsonl",
}
SCHEMA_FILES = {
    MANIFEST_FILE: "manifest.schema.json",
    "papers.jsonl": "paper.schema.json",
    "findings.jsonl": "finding.schema.json",
    "passages.jsonl": "passage-evidence.schema.json",
    "assessments.jsonl": "evidence-assessment.schema.json",
}
ID_FIELDS = {
    "papers.jsonl": "paper_id",
    "findings.jsonl": "finding_id",
    "passages.jsonl": "passage_id",
    "assessments.jsonl": "assessment_id",
}
IGNORED_FILES = {"expected.json"}  # fixture metadata, never part of a packet

VERIFICATION_ORDER = ["unverified", "human-reviewed", "source-verified", "independently-verified"]
JUDGMENT_STATUSES = {"supports", "contradicts", "insufficient", "unclear"}
SUPPORT_OR_CONTRA = {"supports", "contradicts"}
NON_EVIDENCE_STATUSES = {"not_assessed", "unknown", "restricted"}
GOOD_MATCH = {"exact", "normalized"}
BAD_MATCH = {"not_found", "ambiguous", "invalid", "incomplete"}
NOT_REDISTRIBUTABLE = {"metadata-only", "restricted", "unknown"}
_CROSS_FIELD_CODES = {
    ("passages.jsonl", "/source/start"): "match-offset-inconsistent",
    ("passages.jsonl", "/source/end"): "match-offset-inconsistent",
    ("passages.jsonl", "/quote"): "withheld-quote-inconsistent",
}
RECORD_KINDS = ("papers", "findings", "passages", "assessments")


class _Report:
    """Collects findings; sorting happens once at the end so output is stable."""

    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []

    def error(self, code: str, file: str, rid: str, message: str) -> None:
        self.errors.append({"code": code, "file": file, "id": rid, "message": message})

    def warn(self, code: str, file: str, rid: str, message: str) -> None:
        self.warnings.append({"code": code, "file": file, "id": rid, "message": message})

    def has(self, *codes: str) -> bool:
        return any(e["code"] in codes for e in self.errors)

    def has_prefix(self, prefix: str) -> bool:
        return any(e["code"].startswith(prefix) for e in self.errors)


def _sort_key(item: dict[str, str]) -> tuple[str, str, str, str]:
    return (item["code"], item["file"], item["id"], item["message"])


def _dedupe_sorted(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[dict[str, str]] = []
    for item in sorted(items, key=_sort_key):
        key = _sort_key(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _canonical_line(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _load_schema_validators() -> dict[str, Draft202012Validator]:
    schemas: dict[str, dict[str, Any]] = {}
    registry: Registry = Registry()
    for fname in sorted(set(SCHEMA_FILES.values())):
        schema = json.loads((SCHEMAS_DIR / fname).read_text(encoding="utf-8"))
        schemas[fname] = schema
        resource = Resource.from_contents(schema, default_specification=DRAFT202012)
        registry = registry.with_resource(fname, resource)
        if "$id" in schema:
            registry = registry.with_resource(schema["$id"], resource)
    return {
        file: Draft202012Validator(schemas[sf], registry=registry)
        for file, sf in SCHEMA_FILES.items()
    }


def _json_path(error: Any) -> str:
    parts = "".join(f"/{p}" for p in error.absolute_path)
    return parts or "/"


def _rid(record: Any, file: str, lineno: int) -> str:
    if isinstance(record, dict):
        val = record.get(ID_FIELDS.get(file, ""))
        if isinstance(val, str):
            return val
    return f"line:{lineno}"


def _read_manifest(directory: Path, rep: _Report) -> dict[str, Any] | None:
    path = directory / MANIFEST_FILE
    if not path.is_file():
        rep.error("artifact-missing", MANIFEST_FILE, "-", "manifest.json is missing")
        return None
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        manifest = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        rep.error("invalid-json", MANIFEST_FILE, "-", f"manifest.json is not valid JSON: {exc}")
        return None
    if not isinstance(manifest, dict):
        rep.error("invalid-json", MANIFEST_FILE, "-", "manifest.json must be a JSON object")
        return None
    canonical_ok = text.endswith("\n") and "\r" not in text
    if canonical_ok:
        canonical_ok = any(
            text == json.dumps(manifest, indent=ind, sort_keys=True, ensure_ascii=False) + "\n"
            for ind in (2, 4)
        )
    if not canonical_ok:
        rep.error(
            "non-canonical-encoding",
            MANIFEST_FILE,
            "-",
            "manifest.json must be sorted-key pretty JSON with LF line ends and a final newline",
        )
    return manifest


def _read_jsonl(
    directory: Path, file: str, rep: _Report
) -> tuple[list[tuple[int, Any]] | None, bytes | None]:
    path = directory / file
    if not path.is_file():
        return None, None
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rep.error("invalid-json", file, "-", f"file is not valid UTF-8: {exc}")
        return None, raw
    records: list[tuple[int, Any]] = []
    canonical = True
    if text and not text.endswith("\n"):
        canonical = False
    if "\r" in text:
        canonical = False
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    broken = False
    for i, line in enumerate(lines, start=1):
        if line == "":
            rep.error("invalid-json", file, f"line:{i}", "blank line")
            broken = True
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            rep.error("invalid-json", file, f"line:{i}", f"line is not valid JSON: {exc}")
            broken = True
            continue
        if line != _canonical_line(obj):
            canonical = False
        records.append((i, obj))
    if not canonical:
        rep.error(
            "non-canonical-encoding",
            file,
            "-",
            "JSONL must be one compact sorted-key JSON object per line, LF, final newline",
        )
    return (None if broken else records), raw


def _check_raw_flags(manifest: dict[str, Any], rep: _Report) -> None:
    scope = manifest.get("access_scope")
    if isinstance(scope, dict) and scope.get("redistributable_full_text") is True:
        rep.error(
            "full-text-flag",
            MANIFEST_FILE,
            "access_scope",
            "access_scope.redistributable_full_text must be false",
        )


def _gather(directory: Path, manifest: dict[str, Any], rep: _Report) -> dict[str, list[Any]] | None:
    """Structure, hash and schema checks. Returns records or None if unusable."""
    validators = _load_schema_validators()
    usable = True

    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    declared = {MANIFEST_FILE, *ARTIFACT_FILES.values()}
    ro = manifest.get("ro_crate")
    if isinstance(ro, dict) and isinstance(ro.get("path"), str):
        declared.add(ro["path"].split("/")[0])
    for entry in sorted(p.name for p in directory.iterdir()):
        if entry not in declared and entry not in IGNORED_FILES:
            rep.error("unexpected-file", entry, "-", f"{entry} is not declared by the manifest")

    data: dict[str, list[Any]] = {}
    for kind, file in ARTIFACT_FILES.items():
        records, raw = _read_jsonl(directory, file, rep)
        art = artifacts.get(kind) if isinstance(artifacts.get(kind), dict) else {}
        if raw is None:
            rep.error("artifact-missing", file, "-", f"{file} is missing")
            usable = False
            continue
        if _sha256_hex(raw) != art.get("sha256"):
            rep.error(
                "artifact-hash-mismatch",
                file,
                "-",
                f"sha256 of file bytes is {_sha256_hex(raw)}, "
                f"manifest declares {art.get('sha256')}",
            )
        if len(raw) != art.get("bytes"):
            rep.error(
                "artifact-length-mismatch",
                file,
                "-",
                f"file has {len(raw)} bytes, manifest declares {art.get('bytes')}",
            )
        if records is None:
            usable = False
            continue
        if len(records) != art.get("record_count"):
            rep.error(
                "record-count-mismatch",
                file,
                "-",
                f"file has {len(records)} records, manifest declares {art.get('record_count')}",
            )
        validator = validators[file]
        for lineno, rec in records:
            errs = sorted(
                validator.iter_errors(rec),
                key=lambda e: (_json_path(e), e.message),
            )
            for e in errs:
                rep.error(
                    f"schema-invalid:{file}",
                    file,
                    _rid(rec, file, lineno),
                    f"{_json_path(e)}: {e.message}",
                )
                # The schema enforces these cross-field rules itself; surface the semantic
                # code as well so consumers see the specific defect.
                cross = _CROSS_FIELD_CODES.get((file, _json_path(e)))
                if cross:
                    rep.error(cross, file, _rid(rec, file, lineno), e.message)
            if errs:
                usable = False
        data[kind] = [rec for _, rec in records]

    if isinstance(ro, dict) and isinstance(ro.get("path"), str):
        crate = directory / ro["path"]
        if crate.is_file():
            if _sha256_hex(crate.read_bytes()) != ro.get("sha256"):
                rep.error(
                    "artifact-hash-mismatch", ro["path"], "-", "ro_crate sha256 does not match"
                )
        else:
            rep.error("artifact-missing", ro["path"], "-", "ro_crate file is missing")
    return data if usable else None


def _check_duplicates(data: dict[str, list[Any]], rep: _Report) -> None:
    for kind, file in ARTIFACT_FILES.items():
        seen: set[Any] = set()
        for rec in data.get(kind, []):
            if kind == "findings":
                key: Any = (rec["finding_id"], rec["revision"])
                rid = f"{rec['finding_id']}@{rec['revision']}"
            else:
                key = rec[ID_FIELDS[file]]
                rid = key
            if key in seen:
                rep.error("duplicate-id", file, rid, f"duplicate identifier {rid}")
            seen.add(key)


def _semantic_checks(
    manifest: dict[str, Any], data: dict[str, list[Any]], mode: str, rep: _Report
) -> None:
    papers = {p["paper_id"]: p for p in data["papers"]}
    passages = {p["passage_id"]: p for p in data["passages"]}
    findings = data["findings"]
    fkey = {(f["finding_id"], f["revision"]): f for f in findings}
    assessments_by_rev: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for a in data["assessments"]:
        assessments_by_rev.setdefault((a["finding_id"], a["finding_revision"]), []).append(a)

    scope = manifest["access_scope"]
    status = manifest["export_status"]
    wc = status["withheld_counts"]
    state = status["state"]

    # --- packet-level rights and completeness ---------------------------------------
    if scope["level"] in ("private", "embargoed"):
        msg = f"access_scope.level is {scope['level']}"
        if mode == MODE_ALLOW_RESTRICTED:
            rep.warn("private-packet-not-exportable", MANIFEST_FILE, "access_scope", msg)
        else:
            rep.error("private-packet-not-exportable", MANIFEST_FILE, "access_scope", msg)
    if scope["level"] == "embargoed":
        until = scope.get("embargo_until")
        if not until:
            rep.error(
                "embargo-expired-or-missing", MANIFEST_FILE, "access_scope", "no embargo_until"
            )
        elif until <= manifest["created_at"]:
            rep.error(
                "embargo-expired-or-missing",
                MANIFEST_FILE,
                "access_scope",
                "embargo_until is not after created_at",
            )
    if state == "complete" and any(v > 0 for v in wc.values()):
        rep.error(
            "completeness-fabricated",
            MANIFEST_FILE,
            "export_status",
            "state is complete but withheld_counts are non-zero",
        )
    if state == "withheld":
        if (
            any(art["record_count"] != 0 for art in manifest["artifacts"].values())
            or not status["reasons"]
        ):
            rep.error(
                "silent-omission",
                MANIFEST_FILE,
                "export_status",
                "state withheld requires zero records and non-empty reasons",
            )

    # --- papers ----------------------------------------------------------------------
    for p in data["papers"]:
        if p["rights"].get("full_text_included") is True:
            rep.error("full-text-flag", "papers.jsonl", p["paper_id"], "full_text_included is true")

    # --- passages --------------------------------------------------------------------
    for ps in data["passages"]:
        pid = ps["passage_id"]
        paper = papers.get(ps["paper_id"])
        if paper is None:
            rep.error("dangling-paper-ref", "passages.jsonl", pid, f"unknown {ps['paper_id']}")
        else:
            if ps["source"]["snapshot_ref"] != paper["source_snapshot"]["snapshot_ref"]:
                rep.error(
                    "snapshot-ref-mismatch",
                    "passages.jsonl",
                    pid,
                    "passage snapshot_ref differs from paper source_snapshot.snapshot_ref",
                )
            if (
                ps["quote_state"] == "included"
                and paper["rights"]["redistribution"] in NOT_REDISTRIBUTABLE
            ):
                rep.error(
                    "restricted-quote-leak",
                    "passages.jsonl",
                    pid,
                    "quote included although redistribution is "
                    f"{paper['rights']['redistribution']}",
                )
        src = ps["source"]
        start, end = src["start"], src["end"]
        if ps["match"]["status"] in GOOD_MATCH:
            if start is None or end is None or end <= start:
                rep.error(
                    "match-offset-inconsistent",
                    "passages.jsonl",
                    pid,
                    "exact/normalized match needs non-null offsets with end > start",
                )
        elif start is not None or end is not None:
            rep.error(
                "match-offset-inconsistent",
                "passages.jsonl",
                pid,
                f"match status {ps['match']['status']} requires null offsets",
            )
        if ps["quote_state"] == "included":
            if ps["quote"] == "withheld":
                rep.error(
                    "withheld-quote-inconsistent",
                    "passages.jsonl",
                    pid,
                    "quote_state included but quote is the literal 'withheld'",
                )
            elif _sha256_hex(ps["quote"].encode("utf-8")) != ps["quote_sha256"]:
                rep.error(
                    "quote-hash-mismatch", "passages.jsonl", pid, "sha256(quote) != quote_sha256"
                )
        elif ps["quote"] != "withheld":
            rep.error(
                "withheld-quote-inconsistent",
                "passages.jsonl",
                pid,
                "quote_state withheld-rights requires quote to be the literal 'withheld'",
            )

    # --- findings --------------------------------------------------------------------
    revs: dict[str, list[int]] = {}
    for f in findings:
        revs.setdefault(f["finding_id"], []).append(f["revision"])
    for fid in sorted(revs):
        if sorted(revs[fid]) != list(range(1, len(revs[fid]) + 1)):
            rep.error(
                "revision-gap",
                "findings.jsonl",
                fid,
                f"revisions {sorted(revs[fid])} are not 1..{len(revs[fid])}",
            )

    for f in findings:
        fid = f["finding_id"]
        rid = f"{fid}@{f['revision']}"
        paper = papers.get(f["paper_id"])
        if paper is None:
            rep.error("dangling-paper-ref", "findings.jsonl", rid, f"unknown {f['paper_id']}")
        fps: list[dict[str, Any]] = []
        for pid in f["passage_ids"]:
            ps = passages.get(pid)
            if ps is None:
                rep.error("dangling-passage-ref", "findings.jsonl", rid, f"unknown {pid}")
                if state != "partial":
                    rep.error(
                        "completeness-fabricated",
                        "findings.jsonl",
                        rid,
                        f"references absent passage {pid} but export_status.state is {state}",
                    )
                continue
            fps.append(ps)
            if ps["paper_id"] != f["paper_id"]:
                rep.error(
                    "passage-paper-mismatch",
                    "findings.jsonl",
                    rid,
                    f"passage {pid} belongs to {ps['paper_id']}, finding to {f['paper_id']}",
                )
        arecs = assessments_by_rev.get((fid, f["revision"]), [])
        level = VERIFICATION_ORDER.index(f["verification_level"])
        judged = [a for a in arecs if a["status"] in JUDGMENT_STATUSES]
        has_good_passage = any(p["match"]["status"] in GOOD_MATCH for p in fps)

        if f["export_state"] == "approved-for-export":
            if f["review_state"] != "human-reviewed":
                rep.error("approved-without-review", "findings.jsonl", rid, "not human-reviewed")
            if not has_good_passage:
                rep.error(
                    "unmatched-passage-approved",
                    "findings.jsonl",
                    rid,
                    "no passage with match exact or normalized",
                )
                if any(p["match"]["status"] in BAD_MATCH for p in fps):
                    rep.error(
                        "ambiguous-passage-approved",
                        "findings.jsonl",
                        rid,
                        "supported only by ambiguous/not_found/invalid/incomplete passages",
                    )
            if paper is not None and paper["rights"]["redistribution"] in {"restricted", "unknown"}:
                rep.error(
                    "approved-in-restricted-packet",
                    "findings.jsonl",
                    rid,
                    f"paper redistribution is {paper['rights']['redistribution']}",
                )
        if f["review_state"] == "human-reviewed" and not arecs:
            rep.error("review-without-assessment", "findings.jsonl", rid, "no assessment")

        # verification semantics
        problems: list[str] = []
        if f["verification_level"] == "unverified":
            if f["review_state"] == "human-reviewed" and judged:
                problems.append("unverified conflicts with human-reviewed state and judgments")
        if level >= 1 and not judged:
            problems.append("needs >=1 supports/contradicts/insufficient/unclear assessment")
        if level >= 2:
            snap_ok = paper is not None and paper["source_snapshot"]["state"] == "captured"
            matched = any(
                a["status"] in SUPPORT_OR_CONTRA
                and passages.get(a["passage_id"], {}).get("match", {}).get("status") in GOOD_MATCH
                for a in arecs
            )
            if not (snap_ok and matched):
                problems.append(
                    "needs a supports/contradicts assessment on an exact/normalized passage "
                    "and a captured snapshot"
                )
        if level >= 3 and len({a["reviewer_ref"] for a in judged}) < 2:
            problems.append("needs >=2 distinct reviewer_refs with judgments")
        for prob in problems:
            rep.error(
                "verification-overclaim",
                "findings.jsonl",
                rid,
                f"{f['verification_level']}: {prob}",
            )
        if level >= 2 and not judged:
            rep.error(
                "quote-presence-as-support",
                "findings.jsonl",
                rid,
                "verification rests on quote presence; evidence assessments are only "
                "not_assessed/unknown/restricted or absent",
            )

    # --- assessments -----------------------------------------------------------------
    for a in data["assessments"]:
        aid = a["assessment_id"]
        f_any = [k for k in fkey if k[0] == a["finding_id"]]
        if not f_any:
            rep.error("dangling-finding-ref", "assessments.jsonl", aid, a["finding_id"])
        elif (a["finding_id"], a["finding_revision"]) not in fkey:
            rep.error(
                "dangling-revision-ref",
                "assessments.jsonl",
                aid,
                f"{a['finding_id']} has no revision {a['finding_revision']}",
            )
        if a["passage_id"] not in passages:
            rep.error("dangling-passage-ref", "assessments.jsonl", aid, a["passage_id"])
        f_rev = fkey.get((a["finding_id"], a["finding_revision"]))
        if f_rev is not None:
            paper = papers.get(f_rev["paper_id"])
            if paper is not None and (
                a["assessed_snapshot_sha256"] != paper["source_snapshot"]["sha256"]
            ):
                rep.error(
                    "assessed-snapshot-mismatch",
                    "assessments.jsonl",
                    aid,
                    "assessed_snapshot_sha256 differs from the paper snapshot sha256",
                )


def _counts(manifest: dict[str, Any], data: dict[str, list[Any]], rep: _Report) -> dict[str, Any]:
    findings = data.get("findings", [])
    rejected: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    file_kind = {v: k for k, v in ARTIFACT_FILES.items()}
    for e in rep.errors:
        kind = file_kind.get(e["file"])
        if kind and e["id"] != "-" and (kind, e["id"]) not in seen:
            seen.add((kind, e["id"]))
            rejected[kind] += 1
    wc = manifest.get("export_status", {}).get("withheld_counts", {})
    return {
        **{k: len(data.get(k, [])) for k in RECORD_KINDS},
        "findings_by_export_state": dict(
            sorted(Counter(f["export_state"] for f in findings).items())
        ),
        "findings_by_verification": dict(
            sorted(Counter(f["verification_level"] for f in findings).items())
        ),
        "omitted": {k: wc.get(k, 0) for k in RECORD_KINDS},
        "rejected": {k: rejected.get(k, 0) for k in RECORD_KINDS},
    }


def mapping_report(
    data: dict[str, list[Any]], manifest: dict[str, Any], min_verification: str
) -> dict[str, Any]:
    """Consumer-side view: which records an importer would reject or omit, and why."""
    papers = {p["paper_id"]: p for p in data["papers"]}
    passages = {p["passage_id"]: p for p in data["passages"]}
    threshold = VERIFICATION_ORDER.index(min_verification)
    packet_private = manifest["access_scope"]["level"] in ("private", "embargoed")
    rows: list[dict[str, Any]] = []
    totals = {"incompatible": 0, "restricted": 0, "importable": 0}
    for f in sorted(data["findings"], key=lambda x: (x["finding_id"], x["revision"])):
        incompatible: list[str] = []
        restricted: list[str] = []
        if VERIFICATION_ORDER.index(f["verification_level"]) < threshold:
            incompatible.append("verification-below-threshold")
        if f["review_state"] == "unreviewed":
            incompatible.append("unreviewed")
        if f["export_state"] == "proposed":
            incompatible.append("proposed")
        if any(
            passages.get(pid, {}).get("quote_state") == "withheld-rights"
            for pid in f["passage_ids"]
        ):
            restricted.append("withheld-quote")
        paper = papers.get(f["paper_id"])
        if paper is not None and paper["rights"]["redistribution"] in NOT_REDISTRIBUTABLE:
            restricted.append("private-rights")
        if packet_private:
            restricted.append("private-rights")
        restricted = sorted(set(restricted))
        outcome = "importable"
        if incompatible:
            outcome = "incompatible"
        elif restricted:
            outcome = "restricted"
        totals["importable" if outcome == "importable" else outcome] += 1
        rows.append(
            {
                "finding_id": f["finding_id"],
                "revision": f["revision"],
                "outcome": outcome,
                "incompatible_reasons": sorted(incompatible),
                "restricted_reasons": restricted,
            }
        )
    withheld_passages = sorted(
        p["passage_id"] for p in data["passages"] if p["quote_state"] == "withheld-rights"
    )
    return {
        "min_verification": min_verification,
        "findings": rows,
        "totals": totals,
        "withheld_quote_passages": withheld_passages,
    }


def validate_packet(
    directory: str | Path,
    mode: str = MODE_FOR_IMPORT,
    *,
    with_mapping_report: bool = False,
    min_verification: str = "human-reviewed",
) -> dict[str, Any]:
    """Validate the packet in ``directory``; returns the deterministic result object."""
    if mode not in (MODE_FOR_IMPORT, MODE_ALLOW_RESTRICTED):
        raise ValueError(f"unknown mode {mode!r}")
    directory = Path(directory)
    rep = _Report()
    data: dict[str, list[Any]] = {}
    manifest = _read_manifest(directory, rep)
    manifest_ok = False
    if manifest is not None:
        _check_raw_flags(manifest, rep)
        if manifest.get("schema_version") != SCHEMA_VERSION:
            rep.error(
                "unsupported-schema-version",
                MANIFEST_FILE,
                "schema_version",
                f"expected {SCHEMA_VERSION}, got {manifest.get('schema_version')!r}",
            )
        else:
            for e in sorted(
                _load_schema_validators()[MANIFEST_FILE].iter_errors(manifest),
                key=lambda e: (_json_path(e), e.message),
            ):
                rep.error(
                    f"schema-invalid:{MANIFEST_FILE}",
                    MANIFEST_FILE,
                    "-",
                    f"{_json_path(e)}: {e.message}",
                )
            manifest_ok = not rep.has_prefix("schema-invalid:")
    usable: dict[str, list[Any]] | None = None
    if manifest is not None and manifest_ok:
        usable = _gather(directory, manifest, rep)
        if usable is not None:
            data = usable
            _check_duplicates(data, rep)
            _semantic_checks(manifest, data, mode, rep)
    elif manifest is not None:
        # Manifest unusable: still report undeclared files for orientation.
        pass

    errors = _dedupe_sorted(rep.errors)
    rep.errors = errors
    result: dict[str, Any] = {
        "valid": not errors,
        "errors": errors,
        "warnings": _dedupe_sorted(rep.warnings),
        "counts": _counts(manifest or {}, data, rep),
    }
    if result["valid"]:
        # Packet digest (README section 2.1): SHA-256 of the exact manifest.json bytes. Emitted only
        # for a valid packet, because only then does the manifest bind every artifact it lists.
        result["packet_digest"] = _sha256_hex((directory / MANIFEST_FILE).read_bytes())
    if with_mapping_report and usable is not None and manifest is not None:
        result["mapping_report"] = mapping_report(data, manifest, min_verification)
    return result


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sep_validate.py", description="Validate a source-evidence-packet/0.1.0 directory."
    )
    parser.add_argument("packet_dir", help="directory holding manifest.json and the JSONL files")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--for-import",
        dest="mode",
        action="store_const",
        const=MODE_FOR_IMPORT,
        help="refuse private/embargoed packets (default)",
    )
    group.add_argument(
        "--allow-restricted",
        dest="mode",
        action="store_const",
        const=MODE_ALLOW_RESTRICTED,
        help="private/embargoed packets only produce a warning",
    )
    parser.add_argument(
        "--mapping-report",
        action="store_true",
        help="add consumer rejected/omitted counts per record",
    )
    parser.add_argument(
        "--min-verification",
        choices=VERIFICATION_ORDER,
        default="human-reviewed",
        help="importer verification threshold used by --mapping-report",
    )
    parser.set_defaults(mode=MODE_FOR_IMPORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)  # argparse exits with status 2 on usage errors
    directory = Path(args.packet_dir)
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return 2
    result = validate_packet(
        directory,
        args.mode,
        with_mapping_report=args.mapping_report,
        min_verification=args.min_verification,
    )
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
