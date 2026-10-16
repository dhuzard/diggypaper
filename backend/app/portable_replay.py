"""Offline bounded replay for an unsigned SEIP evidence manifest.

This module reads no database, mutable article, network service, or export ZIP.
It checks frozen artifacts and quote occurrences. Only retained raw legacy
OpenAI-compatible chat JSON can verify quote-list emission; it does not assess
service authenticity, scientific support, or adjudication.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

from app.provenance import (
    MAX_CANONICAL_BYTES,
    InvocationV1,
    ResponseCapture,
    SourceSnapshotV1,
    canonical_json,
    digest,
    prepare_invocation,
)
from app.quote_checks import check_prediction_quotes

SCHEMA_VERSION = "seip-portable-replay-v1"
MAX_MANIFEST_BYTES = 128 * 1024 * 1024
MAX_SOURCES = 10_000
MAX_INVOCATIONS = 20_000
MAX_RESULTS = 20_000
MAX_CHAT_RESPONSE_BYTES = 8 * 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class ReplayError(ValueError):
    """An exported claim cannot be replayed from its retained artifacts."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReplayError(message)


def _keys(value, required: set[str], optional: set[str] = frozenset()) -> None:
    _require(isinstance(value, dict), "manifest entry must be an object")
    _require(
        required <= value.keys() and value.keys() <= required | optional,
        "manifest entry has missing or unsupported keys",
    )


def _id(value, label: str) -> uuid.UUID:
    _require(isinstance(value, str), f"{label} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ReplayError(f"{label} must be a UUID string") from exc
    _require(str(parsed) == value, f"{label} must be a canonical UUID")
    return parsed


def _sha(value, label: str) -> str:
    _require(
        isinstance(value, str) and _HASH.fullmatch(value) is not None,
        f"{label} must be lowercase SHA-256",
    )
    return value


def _frozen(entry, label: str) -> dict:
    _keys(entry, {"id", "canonical_base64", "sha256"})
    _id(entry["id"], f"{label} id")
    _sha(entry["sha256"], f"{label} sha256")
    encoded = entry["canonical_base64"]
    _require(isinstance(encoded, str), f"{label} canonical bytes must be base64")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ReplayError(f"{label} canonical bytes are invalid base64") from exc
    _require(base64.b64encode(data).decode("ascii") == encoded, f"{label} base64 is not canonical")
    _require(len(data) <= MAX_CANONICAL_BYTES, f"{label} canonical bytes exceed bound")
    _require(
        hashlib.sha256(data).hexdigest() == entry["sha256"], f"{label} canonical hash mismatch"
    )
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayError(f"{label} canonical bytes are not UTF-8 JSON") from exc
    _require(isinstance(payload, dict), f"{label} canonical payload must be an object")
    _require(canonical_json(payload) == data, f"{label} bytes violate seip-json-v1")
    _require(payload.get("id") == entry["id"], f"{label} ID disagrees with envelope")
    return payload


def _sources(entries) -> dict[uuid.UUID, SourceSnapshotV1]:
    _require(
        isinstance(entries, list) and len(entries) <= MAX_SOURCES, "source count exceeds bound"
    )
    out = {}
    for entry in entries:
        payload = _frozen(entry, "source")
        try:
            source = SourceSnapshotV1.model_validate(payload)
        except ValueError as exc:
            raise ReplayError("invalid frozen source envelope") from exc
        _require(source.sha256 == entry["sha256"], "source envelope hash mismatch")
        _require(source.id not in out, "duplicate source ID")
        out[source.id] = source
    return out


def _invocations(entries, sources):
    _require(
        isinstance(entries, list) and len(entries) <= MAX_INVOCATIONS,
        "invocation count exceeds bound",
    )
    out = {}
    for entry in entries:
        _keys(
            entry,
            {"id", "canonical_base64", "sha256", "outcome", "response_capture", "response_sha256"},
        )
        payload = _frozen(
            {key: entry[key] for key in ("id", "canonical_base64", "sha256")}, "invocation"
        )
        try:
            call = InvocationV1.model_validate(payload)
        except ValueError as exc:
            raise ReplayError("invalid frozen invocation envelope") from exc
        _require(call.id not in out, "duplicate invocation ID")
        source = sources.get(call.source_snapshot_id)
        _require(source is not None, "invocation source reference is dangling")
        _require(
            (call.project_id, call.article_id) == (source.project_id, source.article_id),
            "invocation source scope mismatch",
        )
        values = dict(payload)
        for key in ("project_id", "article_id", "source_snapshot_id", "request_sha256"):
            values.pop(key)
        try:
            checked = prepare_invocation(source, **values)
        except ValueError as exc:
            raise ReplayError("invocation input origins do not replay") from exc
        _require(
            checked.model_dump(mode="json") == payload,
            "invocation envelope changes during origin replay",
        )
        outcome = entry["outcome"]
        _require(
            outcome in {"prepared", "outcome_unknown", "succeeded", "failed"},
            "unsupported invocation outcome",
        )
        response = entry["response_capture"]
        response_hash = entry["response_sha256"]
        if outcome in {"prepared", "outcome_unknown"}:
            _require(
                response is None and response_hash is None,
                "unfinished invocation cannot claim a response",
            )
        else:
            _require(isinstance(response, dict), "terminal invocation requires response")
            try:
                ResponseCapture.model_validate(response)
            except ValueError as exc:
                raise ReplayError("invalid response capture") from exc
            _require(
                _sha(response_hash, "response sha256") == digest(response),
                "response capture hash mismatch",
            )
        out[call.id] = (call, outcome, response)
    for call, _, _ in out.values():
        for name in ("parent_id", "winning_call_id", "confidence_call_id", "answers_call_id"):
            peer_id = call.parent_id if name == "parent_id" else getattr(call.execution, name)
            if peer_id is None:
                continue
            peer = out.get(peer_id)
            _require(peer is not None, f"{name} reference is dangling")
            other = peer[0]
            _require(
                (call.project_id, call.article_id, call.source_snapshot_id, call.group_id)
                == (other.project_id, other.article_id, other.source_snapshot_id, other.group_id),
                f"{name} scope mismatch",
            )
            _require(peer_id != call.id, f"{name} cannot refer to itself")
    return out


def _emitted_quotes(response: dict | None) -> list[str] | None:
    """Read the exact legacy decision path used by OpenAICompatibleService.infer.

    Duplicate keys, nonfinite numbers, alternate message formats, and repaired
    JSON are deliberately outside this verifier. None means unverified, not an
    assertion that the producer emitted no quote.
    """
    if (
        not isinstance(response, dict)
        or response.get("origin") != "driver_raw"
        or response.get("raw_availability") != "retained"
    ):
        return None
    try:
        raw = base64.b64decode(response["raw_base64"], validate=True)
        if len(raw) > MAX_CHAT_RESPONSE_BYTES:
            return None

        def unique_pairs(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate response JSON key")
                value[key] = item
            return value

        def reject_constant(value):
            raise ValueError(f"nonfinite response JSON value: {value}")

        options = {"object_pairs_hook": unique_pairs, "parse_constant": reject_constant}
        body = json.loads(raw, **options)
        if not isinstance(body, dict):
            return None
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return None
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            return None
        decision = json.loads(message["content"], **options)
        if not isinstance(decision, dict):
            return None
        quotes = decision.get("evidence_quotes")
        return quotes if isinstance(quotes, list) and all(type(q) is str for q in quotes) else None
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error, RecursionError):
        return None


def _results(entries, sources, calls) -> tuple[int, dict[str, str]]:
    _require(
        isinstance(entries, list) and len(entries) <= MAX_RESULTS, "result count exceeds bound"
    )
    seen = set()
    emission = {}
    for entry in entries:
        _keys(
            entry,
            {
                "id",
                "project_id",
                "article_id",
                "capture_state",
                "source_snapshot_ref",
                "invocation_ref",
                "reason",
                "quote_state",
                "quote_reason",
                "quotation_field",
                "quote_checks",
                "quote_checks_sha256",
            },
        )
        result_id = _id(entry["id"], "result id")
        _require(result_id not in seen, "duplicate result ID")
        seen.add(result_id)
        emission[str(result_id)] = "unverified"
        project_id = _id(entry["project_id"], "result project")
        article_id = _id(entry["article_id"], "result article")
        state = entry["capture_state"]
        _require(
            state in {"captured", "legacy_unversioned", "source_unavailable"},
            "unsupported capture state",
        )
        if state != "captured":
            _require(
                entry["source_snapshot_ref"] is None and entry["invocation_ref"] is None,
                "uncaptured result cannot claim frozen references",
            )
            expected_quote_state = (
                "legacy_unversioned" if state == "legacy_unversioned" else "unavailable"
            )
            _require(
                entry["quote_state"] == expected_quote_state,
                "uncaptured quote state disagrees with capture state",
            )
            _require(
                all(
                    entry[key] is None
                    for key in ("quotation_field", "quote_checks", "quote_checks_sha256")
                ),
                "uncaptured result cannot claim occurrence checks",
            )
            _require(
                isinstance(entry["reason"], str) and bool(entry["reason"].strip()),
                "uncaptured result needs an explicit reason",
            )
            _require(
                entry["quote_reason"] is None
                if state == "legacy_unversioned"
                else isinstance(entry["quote_reason"], str) and bool(entry["quote_reason"].strip()),
                "invalid quote unavailability reason",
            )
            continue
        _require(entry["reason"] is None, "captured result cannot have unavailable reason")
        source_id = _id(entry["source_snapshot_ref"], "result source")
        call_id = _id(entry["invocation_ref"], "result invocation")
        source = sources.get(source_id)
        call_row = calls.get(call_id)
        _require(source is not None and call_row is not None, "result reference is dangling")
        call, outcome, response = call_row
        _require(outcome == "succeeded", "result requires a succeeded producing invocation")
        _require(
            (project_id, article_id, source_id)
            == (source.project_id, source.article_id, call.source_snapshot_id),
            "result source scope mismatch",
        )
        _require(
            (project_id, article_id) == (call.project_id, call.article_id),
            "result invocation scope mismatch",
        )
        quote_state = entry["quote_state"]
        if call.quotation_producer == "not_produced":
            _require(entry["quote_reason"] is None, "non-producer needs no quote reason")
            _require(
                quote_state == "not_produced"
                and all(
                    entry[key] is None
                    for key in ("quotation_field", "quote_checks", "quote_checks_sha256")
                ),
                "non-quotation producer cannot claim occurrence checks",
            )
        elif quote_state == "unavailable":
            _require(
                isinstance(entry["quote_reason"], str) and bool(entry["quote_reason"].strip()),
                "unavailable quote checks need an explicit reason",
            )
            _require(
                all(
                    entry[key] is None
                    for key in ("quotation_field", "quote_checks", "quote_checks_sha256")
                ),
                "unavailable quote checks cannot assert occurrences",
            )
        else:
            _require(quote_state == "recorded", "unsupported quotation state")
            _require(entry["quote_reason"] is None, "recorded checks need no unavailable reason")
            field = entry["quotation_field"]
            _keys(field, {"present", "value"})
            _require(type(field["present"]) is bool, "quotation field presence must be boolean")
            _require(
                field["present"] or field["value"] is None,
                "missing quotation field cannot have a value",
            )
            checks = entry["quote_checks"]
            _require(isinstance(checks, dict), "recorded quote checks must be an object")
            _require(
                _sha(entry["quote_checks_sha256"], "quote checks sha256") == digest(checks),
                "quote checks hash mismatch",
            )
            decision = {"evidence_quotes": field["value"]} if field["present"] else {}
            try:
                # Stored JSONB converts dataclass candidate tuples to JSON arrays.
                expected = json.loads(json.dumps(check_prediction_quotes(call, decision)))
            except ValueError as exc:
                raise ReplayError("quote checks cannot replay") from exc
            _require(checks == expected, "quote occurrence claim does not replay")
            if (
                call.call_kind == "chat"
                and call.quotation_producer == "may_produce"
                and field["present"]
                and isinstance(field["value"], list)
                and all(type(q) is str for q in field["value"])
                and _emitted_quotes(response) == field["value"]
            ):
                emission[str(result_id)] = "verified"
    return len(seen), emission


def validate_manifest(manifest: dict) -> dict:
    """Replay artifacts/occurrences and bounded raw chat quote-list emission."""
    _keys(manifest, {"schema_version", "canonical_encoding", "sources", "invocations", "results"})
    _require(
        manifest["schema_version"] == SCHEMA_VERSION
        and manifest["canonical_encoding"] == "seip-json-v1",
        "unsupported manifest version",
    )
    _require(
        all(
            isinstance(manifest[key], list) and len(manifest[key]) <= limit
            for key, limit in (
                ("sources", MAX_SOURCES),
                ("invocations", MAX_INVOCATIONS),
                ("results", MAX_RESULTS),
            )
        ),
        "manifest entry count exceeds bound",
    )
    try:
        _require(len(canonical_json(manifest)) <= MAX_MANIFEST_BYTES, "manifest exceeds byte bound")
        sources = _sources(manifest["sources"])
        calls = _invocations(manifest["invocations"], sources)
        results, emission = _results(manifest["results"], sources, calls)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        if isinstance(exc, ReplayError):
            raise
        raise ReplayError("malformed manifest") from exc
    return {
        "sources": len(sources),
        "invocations": len(calls),
        "results": results,
        "status": "bounded_occurrence_replay",
        "quotation_emission": (
            "verified"
            if emission and all(state == "verified" for state in emission.values())
            else "unverified"
            if emission
            else "not_assessed"
        ),
        "quotation_emission_by_result": emission,
    }


def validate_manifest_bytes(raw: bytes) -> dict:
    """Reject oversized input before JSON parsing; accepts ordinary UTF-8 JSON files."""
    _require(
        isinstance(raw, bytes) and len(raw) <= MAX_MANIFEST_BYTES, "manifest exceeds byte bound"
    )
    try:

        def unique_pairs(pairs):
            value = {}
            for key, item in pairs:
                _require(key not in value, "manifest contains duplicate JSON keys")
                value[key] = item
            return value

        def reject_constant(value):
            raise ReplayError(f"manifest contains nonfinite JSON value: {value}")

        value = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayError("manifest is not UTF-8 JSON") from exc
    return validate_manifest(value)


def main(argv: list[str] | None = None) -> int:
    """Validate a manifest file without a database or network connection."""
    parser = argparse.ArgumentParser(description="Check an unsigned SEIP portable replay manifest")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_manifest_bytes(args.manifest.read_bytes())
    except (OSError, ReplayError) as exc:
        print(f"replay failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
