"""The crate companion keeps replayable abstract evidence and explicit gaps."""

import base64
import copy
import json
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evidence_crate import build_bundle, validate_archive, validate_bundle
from app.evidence_prediction import prediction_envelope
from app.provenance import (
    Components,
    InvocationV1,
    OriginSpan,
    SourceSnapshotV1,
    TextView,
    canonical_json,
    digest,
    text_digest,
)
from app.provenance_render import legacy_methods_view, render_chat, title_abstract_view
from app.quote_checks import check_prediction_quotes

DATA = json.loads(
    (Path(__file__).parents[1] / "fixtures/provenance_v1.json").read_text(encoding="utf-8")
)


def fixture():
    original = DATA["snapshot"]
    components = Components.model_validate(
        {**original["components"], "methods_text": None, "sections": []}
    )
    views = (title_abstract_view(components), legacy_methods_view(components))
    source = SourceSnapshotV1.model_validate(
        {
            **original,
            "components": components.model_dump(mode="json"),
            "views": [view.model_dump(mode="json") for view in views],
        }
    )
    rendered = render_chat(source, views[0].id, "System", "{article_text}")
    old_call = DATA["invocations"][4]["envelope"]
    call = InvocationV1.model_validate(
        {
            **old_call,
            "source_snapshot_id": str(source.id),
            "source_view_ids": [str(views[0].id)],
            "input_spans": [span.model_dump(mode="json") for span in rendered.input_spans],
            "request": {
                "model": "fixture-model",
                "messages": rendered.messages,
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            },
            "request_sha256": digest(
                {
                    "model": "fixture-model",
                    "messages": rendered.messages,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                }
            ),
            "search_id": str(uuid.UUID("00000000-0000-0000-0000-000000000077")),
        }
    )
    source_data = source.model_dump(mode="json")
    source_row = SimpleNamespace(
        id=source.id,
        project_id=source.project_id,
        article_id=source.article_id,
        canonical_bytes=canonical_json(source_data),
        snapshot_sha256=digest(source_data),
        payload=source_data,
    )
    call_data = call.model_dump(mode="json")
    call_row = SimpleNamespace(
        **{name: getattr(call, name) for name in (
            "id", "project_id", "article_id", "source_snapshot_id", "group_id",
            "parent_id", "search_id", "inference_job_id", "benchmark_run_id",
            "variant", "stage", "call_kind", "sequence", "attempt", "schema_version",
            "request_sha256",
        )},
        canonical_bytes=canonical_json(call_data),
        envelope_sha256=digest(call_data),
        request_envelope=call_data,
        outcome="succeeded",
    )
    decision = {
        "use_ai_behavior": True,
        "animal_sex": "BOTH_SEXES",
        "evidence_quotes": ["No mice were housed."],
        "evidence_links": [{"field_key": "use_ai_behavior", "quote_index": 0}],
    }
    checks = json.loads(json.dumps(check_prediction_quotes(call, decision)))
    pred_id = uuid.uuid4()
    graph = prediction_envelope(
        prediction_id=pred_id,
        project_id=source.project_id,
        article_id=source.article_id,
        project=SimpleNamespace(question=None, fields=None),
        decision=decision,
        invocation_id=call.id,
        source_snapshot_id=source.id,
        quote_checks=checks,
    )
    pred = SimpleNamespace(
        id=pred_id,
        article_id=source.article_id,
        search_id=call.search_id,
        provenance_project_id=source.project_id,
        source_snapshot_id=source.id,
        invocation_id=call.id,
        decision_json=decision,
        quote_checks_json=checks,
        evidence_graph_json=graph,
    )
    link = graph["envelope"]["links"][0]
    support = SimpleNamespace(
        id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        graph_revision_ref=pred_id,
        answer_ref=uuid.UUID(link["answer_ref"]),
        evidence_ref=uuid.UUID(link["evidence_ref"]),
        actor_ref="a" * 64,
        status="unclear",
        protocol_version="seip-support-v1",
        rationale="Private reviewer note",
    )
    return pred, source_row, call_row, support


def test_abstract_capture_replays_without_pdf_response_or_actor_identity():
    pred, source, call, support = fixture()
    manifest, history = build_bundle(
        source.project_id, [pred], {source.id: source}, {call.id: call}, {pred.id: [support]}
    )
    assert validate_bundle(manifest, history)["status"] == "bounded_occurrence_replay"
    assert manifest["results"][0]["capture_state"] == "captured"
    assert manifest["results"][0]["quote_state"] == "recorded"
    assert history[0]["evidence_graph"] == pred.evidence_graph_json
    assert history[0]["support_assessments"][0]["status"] == "unclear"
    assert "Private reviewer note" not in json.dumps([manifest, history])
    assert manifest["invocations"][0]["response_capture"]["raw_availability"] == "unavailable"
    assert validate_bundle(manifest, history)["quotation_emission"] == "unverified"


def test_legacy_and_fulltext_capture_are_explicit_without_frozen_bytes():
    pred, source, call, _ = fixture()
    legacy = copy.copy(pred)
    legacy.id = uuid.uuid4()
    legacy.source_snapshot_id = legacy.invocation_id = None
    legacy.evidence_graph_json = legacy.quote_checks_json = None
    fulltext = copy.copy(pred)
    fulltext.id = uuid.uuid4()
    fulltext.evidence_graph_json = None
    source.payload["components"]["methods_text"] = "Restricted fulltext"
    # Retained source must remain internally valid; regenerate its canonical bytes.
    source.payload["views"] = [
        view for view in source.payload["views"] if view["kind"] != "legacy_methods"
    ]
    source.canonical_bytes = canonical_json(source.payload)
    source.snapshot_sha256 = digest(source.payload)
    manifest, history = build_bundle(
        source.project_id, [legacy, fulltext], {source.id: source}, {call.id: call}, {}
    )
    assert {r["capture_state"] for r in manifest["results"]} == {
        "legacy_unversioned", "source_unavailable"
    }
    assert manifest["sources"] == manifest["invocations"] == []
    assert all(item["evidence_graph"] is None for item in history)


def test_corrupt_hash_reference_and_exported_link_fail_closed():
    pred, source, call, support = fixture()
    with pytest.raises(ValueError, match="digest"):
        bad = copy.copy(source)
        bad.snapshot_sha256 = "0" * 64
        build_bundle(source.project_id, [pred], {source.id: bad}, {call.id: call}, {})
    with pytest.raises(ValueError, match="dangling"):
        build_bundle(source.project_id, [pred], {}, {call.id: call}, {})
    manifest, history = build_bundle(
        source.project_id, [pred], {source.id: source}, {call.id: call}, {pred.id: [support]}
    )
    history[0]["support_assessments"][0]["evidence_ref"] = str(uuid.uuid4())
    with pytest.raises(ValueError, match="no graph link"):
        validate_bundle(manifest, history)


def test_two_runs_retain_distinct_results_and_source_replacement():
    pred, source, call, _ = fixture()
    later = copy.copy(pred)
    later.id = uuid.uuid4()
    later.evidence_graph_json = None
    manifest, history = build_bundle(
        source.project_id, [later, pred], {source.id: source}, {call.id: call}, {}
    )
    assert len(manifest["results"]) == len(history) == 2
    assert {item["id"] for item in manifest["results"]} == {str(pred.id), str(later.id)}
    assert len(manifest["sources"]) == len(manifest["invocations"]) == 1
    # Mutable article text is never read: both runs keep the captured abstract.
    frozen = json.loads(base64.b64decode(manifest["sources"][0]["canonical_base64"]))
    assert frozen["components"]["abstract"] == source.payload["components"]["abstract"]
    assert validate_bundle(manifest, history)["results"] == 2


def test_archive_consumer_detects_hash_and_link_tampering(tmp_path):
    pred, source, call, support = fixture()
    manifest, history = build_bundle(
        source.project_id, [pred], {source.id: source}, {call.id: call}, {pred.id: [support]}
    )

    def archive(name, replay, lineage):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as output:
            output.writestr("evidence/portable-replay.json", json.dumps(replay))
            output.writestr("evidence/prediction-history.json", json.dumps(lineage))
        return path

    assert validate_archive(archive("valid.zip", manifest, history))["results"] == 1
    wrong_hash = copy.deepcopy(manifest)
    wrong_hash["sources"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        validate_archive(archive("hash.zip", wrong_hash, history))
    wrong_link = copy.deepcopy(history)
    wrong_link[0]["support_assessments"][0]["answer_ref"] = str(uuid.uuid4())
    with pytest.raises(ValueError, match="graph link"):
        validate_archive(archive("link.zip", manifest, wrong_link))
    wrong_match = copy.deepcopy(history)
    wrong_match[0]["evidence_graph"]["envelope"]["evidence"][0]["quote_match_ref"] = str(
        uuid.uuid4()
    )
    with pytest.raises(ValueError, match="quote_match"):
        validate_archive(archive("match.zip", manifest, wrong_match))
    wrong_kind = copy.deepcopy(history)
    wrong_kind[0]["evidence_graph"]["envelope"]["scope"]["record_kind"] = "annotation"
    with pytest.raises(ValueError, match="scope_mismatch"):
        validate_archive(archive("kind.zip", manifest, wrong_kind))
    wrong_state = copy.deepcopy(history)
    wrong_state[0]["evidence_graph"]["envelope"]["state"] = "legacy_unlinked"
    with pytest.raises(ValueError, match="legacy_must_remain_unlinked"):
        validate_archive(archive("state.zip", manifest, wrong_state))
    for field, value in (("status", "verified_gold"), ("actor_ref", "person@example.org")):
        wrong_assessment = copy.deepcopy(history)
        wrong_assessment[0]["support_assessments"][0][field] = value
        with pytest.raises(ValueError, match="invalid identity, status, or protocol"):
            validate_archive(archive(f"assessment-{field}.zip", manifest, wrong_assessment))


def test_replacement_source_text_cannot_replay_old_invocation():
    pred, source, call, _ = fixture()
    changed = copy.deepcopy(source)
    changed.payload["components"]["abstract"] = "Replacement abstract"
    components = Components.model_validate(changed.payload["components"])
    changed.payload["views"] = [
        title_abstract_view(components).model_dump(mode="json"),
        legacy_methods_view(components).model_dump(mode="json"),
    ]
    changed.canonical_bytes = canonical_json(changed.payload)
    changed.snapshot_sha256 = digest(changed.payload)
    with pytest.raises(ValueError, match="origin replay|replay"):
        build_bundle(source.project_id, [pred], {source.id: changed}, {call.id: call}, {})


def test_extra_synthetic_retained_view_is_withheld_even_with_abstract_only_components():
    pred, source, call, _ = fixture()
    hidden = "Licensed full text absent from components"
    extra = TextView(
        kind="review",
        availability="retained",
        text=hidden,
        sha256=text_digest(hidden),
        selection=("abstract",),
        fallback=None,
        composition_version="synthetic-test",
        origins=(OriginSpan(start=0, end=len(hidden), origin="synthetic"),),
    )
    source.payload["views"].append(extra.model_dump(mode="json"))
    source.canonical_bytes = canonical_json(source.payload)
    source.snapshot_sha256 = digest(source.payload)
    manifest, history = build_bundle(
        source.project_id, [pred], {source.id: source}, {call.id: call}, {}
    )
    assert manifest["results"][0]["capture_state"] == "source_unavailable"
    assert manifest["sources"] == manifest["invocations"] == []
    assert hidden not in json.dumps([manifest, history])
