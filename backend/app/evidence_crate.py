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
from collections.abc import Iterable
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from app.evidence_links import EvidenceEnvelopeV1, FrozenFieldDefinition
from app.portable_replay import SCHEMA_VERSION, validate_manifest, validate_manifest_bytes
from app.provenance import digest, invocation_from_record, source_from_record

_PRIVATE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:bearer|api[_ -]?key|secret)\b", re.I)


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
    for entry in history:
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
        if graph is None:
            if entry["graph_state"] == "recorded":
                raise ValueError("recorded graph is missing")
            if entry["support_assessments"]:
                raise ValueError("support assessments require a distributed graph")
            continue
        if entry["graph_state"] != "recorded":
            raise ValueError("distributed graph has an unavailable state")
        envelope = EvidenceEnvelopeV1.model_validate(graph["envelope"])
        if (
            replay["capture_state"] != "captured"
            or envelope.scope.record_ref != ident
            or envelope.scope.revision_ref != ident
            or envelope.scope.article_ref != replay["article_id"]
            or envelope.scope.project_ref != replay["project_id"]
        ):
            raise ValueError("distributed graph scope disagrees with replay result")
        definition = graph.get("field_definition")
        if definition is None:
            if envelope.field_definition_ref is not None:
                raise ValueError("distributed graph has a dangling field definition")
        elif (
            FrozenFieldDefinition.model_validate(definition).identity
            != envelope.field_definition_ref
        ):
            raise ValueError("distributed graph field definition hash mismatch")
        answers = {answer.answer_ref for answer in envelope.answers}
        evidence = {item.evidence_ref for item in envelope.evidence}
        if len(answers) != len(envelope.answers) or len(evidence) != len(envelope.evidence):
            raise ValueError("distributed graph has duplicate node references")
        if any(decision.get(answer.field_key) != answer.value for answer in envelope.answers):
            raise ValueError("distributed answer disagrees with recorded decision")
        links = {(link.answer_ref, link.evidence_ref) for link in envelope.links}
        if any(answer not in answers or quote not in evidence for answer, quote in links):
            raise ValueError("distributed graph has a dangling link")
        checks = replay["quote_checks"]
        matches = {
            item["match_ref"]: item["result"]["quote"]
            for item in checks["items"]
        } if checks is not None else {}
        for item in envelope.evidence:
            if item.origin == "captured_input_context":
                if item.source_binding_ref != replay["invocation_ref"]:
                    raise ValueError("distributed evidence has an alien input binding")
                if (
                    item.quote_match_ref is not None
                    and matches.get(item.quote_match_ref) != item.quote
                ):
                    raise ValueError("distributed evidence has a dangling quote match")
        for assessment in entry["support_assessments"]:
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
