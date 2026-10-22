#!/usr/bin/env python3
"""Offline, deterministic validator for research-project-descriptor/0.1.0.

Usage:
    rpd_validate.py DESCRIPTOR_JSON [--packet PACKET_DIR ...] [--as-of TIMESTAMP]

Exit codes: 0 valid, 1 invalid, 2 usage error.

A descriptor is checked on its own; each ``--packet`` additionally binds one held
source-evidence-packet/0.1.0 to the artifact reference that names it. The packet is checked
with the frozen packet validator (``sep_validate.py``, allow-restricted mode) first. A valid
descriptor grants nothing: it is not a grant, a membership proof or a release.

Only the standard library and ``jsonschema`` are used. No network access, no clock reads and
no randomness: the output depends only on the input bytes, the schemas and ``--as-of``.
Importable entry point: ``validate_descriptor(path, packet_dirs=(), as_of=None)``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "research-project-descriptor/0.1.0"
HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE.parent / "schemas" / "research-project-descriptor.schema.json"
SEP_VALIDATOR = (
    HERE.parents[2] / "source-evidence-packet" / "v0.1.0" / "validator" / "sep_validate.py"
)
FILE = "descriptor"
PACKET = "packet"
MAX_TTL = timedelta(days=7)
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_BARE_DIGEST = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def _load_sep():
    spec = importlib.util.spec_from_file_location("sep_validate_rpd", SEP_VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sep_validate_rpd"] = module
    spec.loader.exec_module(module)
    return module


sep = _load_sep()


class _Report:
    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []

    def error(self, code: str, rid: str, message: str, file: str = FILE) -> None:
        self.errors.append({"code": code, "file": file, "id": rid, "message": message})


def _dedupe_sorted(items: list[dict[str, str]]) -> list[dict[str, str]]:
    out: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for item in items:
        out.setdefault((item["code"], item["file"], item["id"], item["message"]), item)
    return [out[k] for k in sorted(out)]


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _t(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _read(path: Path, rep: _Report) -> tuple[dict[str, Any] | None, bytes | None]:
    if not path.is_file():
        rep.error("artifact-missing", "-", f"{path.name} is missing")
        return None, None
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        doc = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        rep.error("invalid-json", "-", f"descriptor is not valid JSON: {exc}")
        return None, raw
    if not isinstance(doc, dict):
        rep.error("invalid-json", "-", "descriptor must be a JSON object")
        return None, raw
    canonical = "\r" not in text and text == (
        json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    if not canonical:
        rep.error(
            "non-canonical-encoding",
            "-",
            "descriptor must be sorted-key JSON, indent 2, LF line ends, final newline",
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
    """No email or bare 64-hex digest (a correlator) anywhere, disclosed values included."""
    for path, value in _strings(doc):
        if _EMAIL.search(value):
            rep.error("private-identifier-leak", path, "value looks like an email address")
        elif path.rsplit("/", 1)[-1] != "digest" and _BARE_DIGEST.search(value):
            rep.error("private-identifier-leak", path, "bare 64-hex digest (possible correlator)")


def _check_header(doc: dict[str, Any], rep: _Report, as_of: str | None) -> None:
    issued, expires = _t(doc["issued_at"]), _t(doc["expires_at"])
    if expires <= issued:
        rep.error("descriptor-expired", "expires_at", "expires_at is not after issued_at")
    elif expires - issued > MAX_TTL:
        rep.error("ttl-exceeded", "expires_at", "a descriptor is valid for at most 7 days")
    if as_of is not None and expires <= _t(as_of):
        rep.error(
            "descriptor-expired", "expires_at", f"expired at {doc['expires_at']} (as of {as_of})"
        )
    if as_of is not None and issued > _t(as_of):
        rep.error("descriptor-expired", "issued_at", f"issued after {as_of}")
    if (
        doc["creation_method"] == "synthetic-fixture"
        and doc["producer"]["commit"] != "synthetic-fixture"
    ):
        rep.error("producer-inconsistent", "producer", "a synthetic fixture has no producer commit")
    if (
        doc["creation_method"] == "producer-export"
        and doc["producer"]["commit"] == "synthetic-fixture"
    ):
        rep.error("producer-inconsistent", "producer", "a producer export needs its 40-hex commit")
    project = doc["project"]
    disclosed = [k for k in ("title", "description") if project[k]["state"] == "disclosed"]
    if disclosed and doc["audience"]["grant_ref"] == "not-available":
        for k in disclosed:
            rep.error("disclosure-without-grant", f"project/{k}", f"{k} disclosed without a grant")
    prefix = f"/projects/{project['project_id']}"
    if project["deep_link"] != prefix:
        rep.error("deep-link-scope-mismatch", "project", "deep link is not this project")
    seen: Counter[str] = Counter()
    for i, a in enumerate(doc["artifacts"]):
        seen[a["digest"]] += 1
        seen[a["packet_id"]] += 1
        if a["deep_link"] != prefix and not a["deep_link"].startswith(prefix + "/"):
            rep.error("deep-link-scope-mismatch", f"artifacts/{i}", "deep link leaves the project")
    for key, n in sorted(seen.items()):
        if n > 1:
            rep.error("duplicate-artifact", key, "an artifact is listed more than once")


def _check_packets(doc: dict[str, Any], packet_dirs: list[Path], rep: _Report) -> int:
    by_id = {a["packet_id"]: a for a in doc["artifacts"]}
    verified = 0
    for packet_dir in packet_dirs:
        result = sep.validate_packet(packet_dir, sep.MODE_ALLOW_RESTRICTED)
        if not result["valid"]:
            codes = sorted({e["code"] for e in result["errors"]})
            rep.error(
                "packet-invalid", packet_dir.name, f"packet fails validation: {codes}", PACKET
            )
            continue
        manifest = json.loads((packet_dir / "manifest.json").read_text(encoding="utf-8"))
        ref = by_id.get(manifest["packet_id"])
        if ref is None:
            rep.error("artifact-not-listed", manifest["packet_id"], "held packet is not listed")
            continue
        ok = True
        if ref["digest"] != result["packet_digest"]:
            rep.error(
                "artifact-digest-mismatch", ref["packet_id"], "digest differs from the packet"
            )
            ok = False
        if ref["access_level"] != manifest["access_scope"]["level"]:
            rep.error(
                "artifact-scope-mismatch", ref["packet_id"], "access level differs from packet"
            )
            ok = False
        verified += ok
    return verified


def validate_descriptor(
    path: str | Path, packet_dirs: Iterable[str | Path] = (), as_of: str | None = None
) -> dict[str, Any]:
    """Validate a descriptor and any held packets it references; deterministic result."""
    path, dirs = Path(path), [Path(p) for p in packet_dirs]
    rep = _Report()
    doc, raw = _read(path, rep)
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
                rep.error(
                    "schema-invalid", "".join(f"/{p}" for p in e.absolute_path) or "/", e.message
                )
            schema_ok = not errs
        _check_leaks(doc, rep)
    verified = 0
    if doc is not None and schema_ok:
        _check_header(doc, rep, as_of)
        verified = _check_packets(doc, dirs, rep)
    errors = _dedupe_sorted(rep.errors)
    artifacts = doc["artifacts"] if schema_ok and doc else []
    result: dict[str, Any] = {
        "valid": not errors,
        "errors": errors,
        "counts": {"artifacts": len(artifacts), "artifacts_verified": verified},
    }
    if result["valid"] and raw is not None:
        result["descriptor_digest"] = _sha256_hex(raw)
    return result


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rpd_validate.py",
        description="Validate a research-project-descriptor/0.1.0 and the packets it references.",
    )
    parser.add_argument("descriptor", help="path to the descriptor JSON file")
    parser.add_argument(
        "--packet", action="append", default=[], help="a held source-evidence-packet directory"
    )
    parser.add_argument(
        "--as-of", help="RFC 3339 UTC time the consumer uses; an expired descriptor is refused"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)  # argparse exits with status 2 on usage errors
    for p in args.packet:
        if not Path(p).is_dir():
            print(f"error: {p} is not a directory", file=sys.stderr)
            return 2
    if args.as_of is not None and not _TIMESTAMP.fullmatch(args.as_of):
        print("error: --as-of must be RFC 3339 UTC, e.g. 2027-01-01T00:00:00Z", file=sys.stderr)
        return 2
    result = validate_descriptor(args.descriptor, args.packet, args.as_of)
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
