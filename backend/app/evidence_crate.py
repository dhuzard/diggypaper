"""Bounded SEIP evidence companion for an RO-Crate archive.

The portable manifest uses the existing replay contract. Only article title and
abstract captures without retained raw artifacts or prompt examples are copied.
All other records remain explicit unavailable/legacy entries. No provider raw
response, uploaded PDF, account identity, or assessment rationale is copied.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import sys
import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from app.evidence_links import (
    EnvelopeScope,
    EvidenceEnvelopeV1,
    ExpectedAnswer,
    FrozenFieldDefinition,
    QuoteMatchReceipt,
    SourceBindingReceipt,
    ValidationContext,
    validate_envelope,
)
from app.portable_replay import SCHEMA_VERSION, validate_manifest, validate_manifest_bytes
from app.provenance import digest, invocation_from_record, source_from_record
from app.provenance_render import composed_view, legacy_methods_view, title_abstract_view

_PRIVATE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:bearer|api[_ -]?key|secret)\b", re.I)
_ACTOR_REF = re.compile(r"[0-9a-f]{64}\Z")
_SUPPORT_STATUSES = {"supports", "contradicts", "insufficient", "unclear", "not_assessed"}
_HISTORY_KEYS = {
    "prediction_id", "article_id", "search_id", "capture_state", "graph_state",
    "evidence_graph", "support_assessments", "support_assessments_state",
    "support_assessments_withheld_count", "decision_state", "decision_json",
}
_ASSESSMENT_KEYS = {
    "id", "graph_revision_ref", "answer_ref", "evidence_ref", "actor_ref",
    "status", "created_at", "protocol_version", "rationale_state",
}


def _frozen(row, checksum: str) -> dict:
    return {
        "id": str(row.id),
        "canonical_base64": base64.b64encode(row.canonical_bytes).decode("ascii"),
        "sha256": checksum,
    }


def _permitted(snapshot, call) -> bool:
    """Existing crate already distributes titles/abstracts; do not add full text."""
    if snapshot.components.methods_text or snapshot.components.sections:
        return False
    if snapshot.raw_artifact.availability == "retained":
        return False
    # A snapshot can retain additional views whose text is absent from components.
    # Regenerate every exported view, including synthetic spans, from the two
    # distributable components and reject any unfamiliar or altered view.
    for view in snapshot.views:
        if view.availability != "retained":
            return False
        if view.kind == "title_abstract":
            expected = title_abstract_view(snapshot.components)
        elif view.kind == "legacy_methods":
            expected = legacy_methods_view(snapshot.components)
        elif view.kind == "composed":
            expected = composed_view(snapshot.components, list(view.selection))
        else:
            return False
        if view.model_dump(exclude={"id"}) != expected.model_dump(exclude={"id"}):
            return False
    if any(span.origin == "example" for span in call.input_spans):
        return False
    if any(
        getattr(call.execution, key) is not None
        for key in ("winning_call_id", "confidence_call_id", "answers_call_id")
    ) or call.parent_id is not None:
        return False  # A partial invocation group cannot be replayed.
    return not (_PRIVATE.search(row_text(snapshot)) or _PRIVATE.search(row_text(call)))


def row_text(value) -> str:
    return value.model_dump_json()


def build_bundle(
    project_id,
    predictions: Iterable,
    source_rows: dict,
    invocation_rows: dict,
    assessments: dict,
) -> tuple[dict, list[dict]]:
    """Build and replay a self-contained, bounded manifest plus claim lineage.

    Corrupt stored references fail the export. Legitimate historical or
    nondistributable captures remain visible with an explicit state.
    """
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "canonical_encoding": "seip-json-v1",
        "sources": [],
        "invocations": [],
        "results": [],
    }
    history = []
    exported_sources: set = set()
    exported_calls: set = set()
    for pred in sorted(predictions, key=lambda p: (str(p.article_id), str(p.id))):
        state = "legacy_unversioned"
        reason = "prediction has no frozen source and producing invocation"
        source = call = None
        if pred.source_snapshot_id is not None or pred.invocation_id is not None:
            if pred.source_snapshot_id is None or pred.invocation_id is None:
                raise ValueError("prediction has a partial frozen reference")
            source_row = source_rows.get(pred.source_snapshot_id)
            call_row = invocation_rows.get(pred.invocation_id)
            if source_row is None or call_row is None:
                raise ValueError("prediction frozen reference is dangling")
            source = source_from_record(source_row)
            call = invocation_from_record(call_row)
            if (
                source.id != pred.source_snapshot_id
                or call.id != pred.invocation_id
                or call.source_snapshot_id != source.id
                or source.article_id != pred.article_id
                or call.article_id != pred.article_id
                or source.project_id != project_id
                or source.project_id != pred.provenance_project_id
                or call.project_id != pred.provenance_project_id
                or call.search_id != pred.search_id
                or call_row.outcome != "succeeded"
            ):
                raise ValueError("prediction frozen reference scope/outcome mismatch")
            state = "source_unavailable"
            reason = "frozen input is outside bounded distribution policy"
            if _permitted(source, call) and not _PRIVATE.search(str(pred.decision_json or {})):
                state, reason = "captured", None
                if source.id not in exported_sources:
                    manifest["sources"].append(_frozen(source_row, source_row.snapshot_sha256))
                    exported_sources.add(source.id)
                if call.id not in exported_calls:
                    # Raw provider output is never copied into this archive.
                    response = {
                        "origin": "unavailable",
                        "raw_availability": "unavailable",
                        "raw_base64": None,
                        "raw_sha256": None,
                        "parsed": None,
                        "reason": "response excluded from evidence crate",
                    }
                    manifest["invocations"].append(
                        {
                            **_frozen(call_row, call_row.envelope_sha256),
                            "outcome": call_row.outcome,
                            "response_capture": response,
                            "response_sha256": digest(response),
                        }
                    )
                    exported_calls.add(call.id)
        graph = pred.evidence_graph_json
        graph_state = "not_recorded"
        links = set()
        if graph is not None:
            envelope = EvidenceEnvelopeV1.model_validate(graph["envelope"])
            if (
                envelope.scope.record_ref != str(pred.id)
                or envelope.scope.revision_ref != str(pred.id)
                or envelope.scope.article_ref != str(pred.article_id)
                or envelope.scope.project_ref != str(pred.provenance_project_id)
            ):
                raise ValueError("prediction evidence graph scope mismatch")
            links = {(link.answer_ref, link.evidence_ref) for link in envelope.links}
            graph_state = (
                "recorded"
                if state == "captured" and not _PRIVATE.search(str(graph))
                else "withheld_with_source"
            )
        human = []
        for assessment in sorted(assessments.get(pred.id, ()), key=lambda a: (a.created_at, a.id)):
            if (
                assessment.graph_revision_ref != pred.id
                or (str(assessment.answer_ref), str(assessment.evidence_ref)) not in links
            ):
                raise ValueError("support assessment has a dangling graph link")
            if graph_state == "recorded":
                human.append(
                {
                    "id": str(assessment.id),
                    "graph_revision_ref": str(assessment.graph_revision_ref),
                    "answer_ref": str(assessment.answer_ref),
                    "evidence_ref": str(assessment.evidence_ref),
                    "actor_ref": assessment.actor_ref,
                    "status": assessment.status,
                    "created_at": assessment.created_at.isoformat(),
                    "protocol_version": assessment.protocol_version,
                    "rationale_state": "withheld_from_portable_export",
                }
                )
        quote_state = "legacy_unversioned" if state == "legacy_unversioned" else "unavailable"
        quote_reason = None if state == "legacy_unversioned" else "quote checks not distributable"
        quote_field = checks = checks_sha = None
        if state == "captured" and call.quotation_producer == "not_produced":
            quote_state, quote_reason = "not_produced", None
        elif state == "captured" and pred.quote_checks_json is not None:
            quote_state, quote_reason = "recorded", None
            quote_field = {
                "present": "evidence_quotes" in (pred.decision_json or {}),
                "value": (pred.decision_json or {}).get("evidence_quotes"),
            }
            checks = pred.quote_checks_json
            checks_sha = digest(checks)
        manifest["results"].append(
            {
                "id": str(pred.id),
                "project_id": str(project_id),
                "article_id": str(pred.article_id),
                "search_id": str(pred.search_id) if pred.search_id else None,
                "capture_state": state,
                "source_snapshot_ref": str(source.id) if state == "captured" else None,
                "invocation_ref": str(call.id) if state == "captured" else None,
                "reason": reason,
                "quote_state": quote_state,
                "quote_reason": quote_reason,
                "quotation_field": quote_field,
                "quote_checks": checks,
                "quote_checks_sha256": checks_sha,
            }
        )
        history.append(
            {
                "prediction_id": str(pred.id),
                "article_id": str(pred.article_id),
                "search_id": str(pred.search_id) if pred.search_id else None,
                "capture_state": state,
                "graph_state": graph_state,
                "evidence_graph": graph if graph_state == "recorded" else None,
                "support_assessments": human,
                "support_assessments_state": (
                    "recorded" if graph_state == "recorded" else "withheld_or_unavailable"
                ),
                "support_assessments_withheld_count": (
                    0 if graph_state == "recorded" else len(assessments.get(pred.id, ()))
                ),
                "decision_state": "legacy_or_withheld" if state != "captured" else "recorded",
                "decision_json": pred.decision_json if state == "captured" else None,
            }
        )
    validate_bundle(manifest, history)
    return manifest, history


def validate_bundle(manifest: dict, history: list[dict]) -> dict:
    """Offline validation of the replay manifest and graph/assessment references."""
    result = validate_manifest(manifest)
    by_id = {entry["id"]: entry for entry in manifest["results"]}
    if len(by_id) != len(history):
        raise ValueError("prediction history and replay result counts differ")
    seen = set()
    assessment_ids = set()
    for entry in history:
        if not isinstance(entry, dict) or set(entry) != _HISTORY_KEYS:
            raise ValueError("prediction history has missing or unsupported fields")
        ident = entry["prediction_id"]
        if ident in seen or ident not in by_id:
            raise ValueError("prediction history references unknown/duplicate result")
        seen.add(ident)
        replay = by_id[ident]
        if (
            entry["capture_state"] != replay["capture_state"]
            or entry["article_id"] != replay["article_id"]
            or entry["search_id"] != replay.get("search_id")
        ):
            raise ValueError("prediction history and replay scopes differ")
        decision = entry["decision_json"]
        if replay["capture_state"] == "captured":
            if entry["decision_state"] != "recorded" or not isinstance(decision, dict):
                raise ValueError("captured prediction requires its recorded decision")
            field = replay["quotation_field"]
            if field is not None and (
                ("evidence_quotes" in decision) != field["present"]
                or decision.get("evidence_quotes") != field["value"]
            ):
                raise ValueError("decision quotation field disagrees with replay")
        elif entry["decision_state"] != "legacy_or_withheld" or decision is not None:
            raise ValueError("uncaptured prediction cannot disclose a decision")
        graph = entry["evidence_graph"]
        if not isinstance(entry["support_assessments"], list):
            raise ValueError("support assessments must be a list")
        if (
            type(entry["support_assessments_withheld_count"]) is not int
            or entry["support_assessments_withheld_count"] < 0
        ):
            raise ValueError("invalid support-assessment withheld count")
        if graph is None:
            if entry["graph_state"] == "recorded":
                raise ValueError("recorded graph is missing")
            if entry["support_assessments"]:
                raise ValueError("support assessments require a distributed graph")
            if entry["support_assessments_state"] != "withheld_or_unavailable":
                raise ValueError("undistributed support-assessment state is inconsistent")
            continue
        if entry["graph_state"] != "recorded":
            raise ValueError("distributed graph has an unavailable state")
        if not isinstance(graph, dict) or set(graph) != {"envelope", "field_definition"}:
            raise ValueError("distributed graph has unsupported fields")
        if (
            entry["support_assessments_state"] != "recorded"
            or entry["support_assessments_withheld_count"] != 0
        ):
            raise ValueError("distributed support-assessment state is inconsistent")
        envelope = EvidenceEnvelopeV1.model_validate(graph["envelope"])
        if (
            replay["capture_state"] != "captured"
            or envelope.scope.record_ref != ident
            or envelope.scope.revision_ref != ident
            or envelope.scope.article_ref != replay["article_id"]
            or envelope.scope.project_ref != replay["project_id"]
        ):
            raise ValueError("distributed graph scope disagrees with replay result")
        definition_raw = graph.get("field_definition")
        definition = (
            FrozenFieldDefinition.model_validate(definition_raw)
            if definition_raw is not None else None
        )
        scope = EnvelopeScope(
            project_ref=replay["project_id"],
            article_ref=replay["article_id"],
            record_kind="prediction",
            record_ref=ident,
            revision_ref=ident,
        )
        checks = replay["quote_checks"]
        context = ValidationContext(
            scope=scope,
            field_definition=definition,
            answers=tuple(
                ExpectedAnswer(field_key=field.key, value=decision[field.key])
                for field in definition.fields
            ) if definition else (),
            allowed_input_refs=(replay["invocation_ref"],),
            source_bindings=(SourceBindingReceipt(
                binding_ref=replay["invocation_ref"],
                project_ref=replay["project_id"],
                article_ref=replay["article_id"],
                snapshot_ref=replay["source_snapshot_ref"],
                input_ref=replay["invocation_ref"],
                input_kind="invocation",
            ),),
            quote_matches=tuple(
                QuoteMatchReceipt(
                    match_ref=item["match_ref"],
                    project_ref=replay["project_id"],
                    article_ref=replay["article_id"],
                    snapshot_ref=replay["source_snapshot_ref"],
                    input_ref=replay["invocation_ref"],
                    quote=item["result"]["quote"],
                    status=item["result"]["status"],
                ) for item in (checks or {}).get("items", [])
            ),
        )
        validate_envelope(envelope, context)
        if envelope.state == "legacy_unlinked" and [
            item.quote for item in envelope.evidence
        ] != decision.get("evidence_quotes"):
            raise ValueError("legacy graph quotation list disagrees with decision")
        links = {(link.answer_ref, link.evidence_ref) for link in envelope.links}
        for assessment in entry["support_assessments"]:
            if not isinstance(assessment, dict) or set(assessment) != _ASSESSMENT_KEYS:
                raise ValueError("support assessment has missing or unsupported fields")
            try:
                assessment_id = str(uuid.UUID(assessment["id"]))
                created = datetime.fromisoformat(assessment["created_at"])
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("support assessment has invalid ID or time") from exc
            if (
                assessment_id != assessment["id"]
                or assessment_id in assessment_ids
                or created.tzinfo is None
                or created.utcoffset() is None
                or not isinstance(assessment["actor_ref"], str)
                or _ACTOR_REF.fullmatch(assessment["actor_ref"]) is None
                or assessment["status"] not in _SUPPORT_STATUSES
                or assessment["protocol_version"] != "seip-support-v1"
                or assessment["rationale_state"] != "withheld_from_portable_export"
            ):
                raise ValueError("support assessment has invalid identity, status, or protocol")
            assessment_ids.add(assessment_id)
            if (
                assessment["graph_revision_ref"] != ident
                or (assessment["answer_ref"], assessment["evidence_ref"]) not in links
            ):
                raise ValueError("distributed support assessment has no graph link")
    return result


def validate_archive(path: Path) -> dict:
    """Replay the evidence files in a crate without a database or network."""
    with ZipFile(path) as archive:
        manifest_name = "evidence/portable-replay.json"
        history_name = "evidence/prediction-history.json"
        if (
            archive.namelist().count(manifest_name) != 1
            or archive.namelist().count(history_name) != 1
        ):
            raise ValueError("archive must contain one copy of each evidence file")
        if any(
            archive.getinfo(name).file_size > 25_000_000
            for name in (manifest_name, history_name)
        ):
            raise ValueError("evidence file exceeds archive validation bound")
        raw_manifest = archive.read(manifest_name)
        raw_history = archive.read(history_name)
    validate_manifest_bytes(raw_manifest)

    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key in evidence history")
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError(f"nonfinite JSON constant in evidence history: {value}")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON number in evidence history")
        return number

    manifest = json.loads(raw_manifest)
    history = json.loads(
        raw_history,
        object_pairs_hook=unique_pairs,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )
    if not isinstance(history, list):
        raise ValueError("prediction history must be a list")
    return validate_bundle(manifest, history)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a bounded SEIP evidence crate")
    parser.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_archive(args.archive)
    except (OSError, BadZipFile, ValueError, KeyError, TypeError) as exc:
        print(f"evidence crate validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
