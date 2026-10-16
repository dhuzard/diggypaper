"""Storage-only provenance: retained bytes, origins, isolation, and guarded lifecycle."""

import base64
import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.article_text import fill_prompt
from app.db.models import (
    Article,
    ArticlePrediction,
    HumanAnnotation,
    Invocation,
    Project,
    SourceSnapshot,
)
from app.provenance import (
    InputSpan,
    ModelIdentity,
    RawArtifact,
    Reconciliation,
    ResponseCapture,
    SourceDescription,
    SourceSnapshotV1,
    canonical_json,
    complete_invocation,
    digest,
    invocation_record,
    prepare_invocation,
    review_binding,
    source_record,
    text_digest,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/provenance_v1.json").read_text("utf-8"))


def snapshot(**changes):
    return SourceSnapshotV1.model_validate({**copy.deepcopy(FIXTURE["snapshot"]), **changes})


def prepared(source, case=0, **changes):
    data = copy.deepcopy(FIXTURE["invocations"][case]["envelope"])
    for key in ("project_id", "article_id", "source_snapshot_id", "request_sha256"):
        data.pop(key)
    data.update(changes)
    return prepare_invocation(source, **data)


@pytest.fixture
async def stored(session):
    source = snapshot()
    session.add(Project(id=source.project_id, title="Provenance"))
    await session.flush()
    session.add(
        Article(id=source.article_id, project_id=source.project_id, title="Mutable current title")
    )
    await session.flush()
    row = source_record(source)
    session.add(row)
    await session.commit()
    return source, row


def test_canonical_encoding_and_exact_unicode_hashes():
    assert canonical_json({"z": "é🐭", "a": [1, 0.0, None]}) == (
        '{"a":[1,0.0,null],"z":"é🐭"}'.encode()
    )
    assert text_digest("é") != text_digest("e\u0301")
    for invalid in ({1: "not string"}, {"x": float("nan")}, {"x": float("inf")}, (1, 2)):
        with pytest.raises(ValueError):
            canonical_json(invalid)
    source = snapshot()
    expected = hashlib.sha256(
        json.dumps(
            FIXTURE["snapshot"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert source.sha256 == FIXTURE["snapshot_sha256"] == expected


def test_equal_text_different_attribution_has_distinct_identity():
    first = snapshot()
    second = snapshot(
        id=str(uuid.uuid4()),
        source={
            **FIXTURE["snapshot"]["source"],
            "type": "pdf:accepted",
            "public_url": "https://example.org/accepted.pdf",
        },
    )
    assert first.components == second.components
    assert first.views == second.views
    assert first.id != second.id and first.sha256 != second.sha256


def test_section_dictionaries_are_retained_and_component_identities_are_canonical():
    data = copy.deepcopy(FIXTURE["snapshot"])
    data["components"]["sections"][0]["extractor_metadata"] = {"page": 3, "label": "unchanged"}
    source = SourceSnapshotV1.model_validate(data)
    assert (
        source.model_dump(mode="json")["components"]["sections"] == data["components"]["sections"]
    )
    data["views"][0]["origins"][2]["component_path"] = ["sections", -1, "text"]
    with pytest.raises(ValueError, match="canonical component identity"):
        SourceSnapshotV1.model_validate(data)


@pytest.mark.parametrize("case", range(6), ids=[v["name"] for v in FIXTURE["invocations"]])
def test_retained_request_fixtures_and_rendering_semantics(case):
    is_literal = FIXTURE["invocations"][case]["name"] == "literal_inside_article"
    source = (
        SourceSnapshotV1.model_validate(FIXTURE["literal_snapshot"]) if is_literal else snapshot()
    )
    call = prepared(source, case)
    data = FIXTURE["invocations"][case]["envelope"]
    expected = hashlib.sha256(
        json.dumps(
            data["request"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert call.model_dump(mode="json") == data
    assert call.request_sha256 == expected
    name = FIXTURE["invocations"][case]["name"]
    article = SimpleNamespace(**source.components.model_dump(mode="json"))
    if name == "literal_inside_article":
        assert fill_prompt("{article_text}", article) == call.request["messages"][0]["content"]
        injected = [s for s in call.input_spans if s.component_path == ("methods_text",)]
        assert len(injected) == 1
    elif name == "repeated_placeholders":
        assert (
            fill_prompt("{article_text}|{article_text}|{methods_text}", article)
            == (call.request["messages"][0]["content"])
        )
    elif name == "missing_placeholder":
        assert not any(span.origin == "target" for span in call.input_spans)
    elif name == "example_separation":
        assert any(span.origin == "example" for span in call.input_spans)
        assert all(span.view_id is None for span in call.input_spans if span.origin == "example")


def test_origins_reject_synthetic_heading_as_target_and_wrong_component_location():
    source = snapshot()
    data = copy.deepcopy(FIXTURE["invocations"][0]["envelope"])
    # Reuse an equal-length target but claim a different source component.
    data["input_spans"][0]["component_path"] = ["abstract"]
    with pytest.raises(ValueError, match="component origin"):
        prepared(source, input_spans=data["input_spans"])
    forged = copy.deepcopy(FIXTURE["snapshot"])
    forged["views"][0]["origins"][1] = {
        "start": 7,
        "end": 8,
        "origin": "synthetic",
        "component_path": ["abstract"],
        "source_start": 0,
        "source_end": 1,
    }
    with pytest.raises(ValueError, match="synthetic"):
        SourceSnapshotV1.model_validate(forged)


@pytest.mark.parametrize("availability", ["retained", "digest_only", "not_captured", "unavailable"])
def test_raw_availability_is_explicit_and_retention_opt_in(availability, monkeypatch):
    data = b"licensed raw artifact"
    artifact = {"availability": availability}
    if availability in ("retained", "digest_only"):
        artifact.update(sha256=hashlib.sha256(data).hexdigest(), media_type="application/pdf")
    if availability == "unavailable":
        artifact["reason"] = "license does not permit retention"
    source = snapshot(raw_artifact=artifact)
    row = source_record(source, raw_data=data if availability == "retained" else None)
    assert row.raw_availability == availability
    assert row.raw_data == (data if availability == "retained" else None)
    with pytest.raises(ValueError):
        source_record(source, raw_data=None if availability == "retained" else data)
    if availability == "retained":
        monkeypatch.setattr("app.provenance.MAX_RAW_BYTES", 2)
        with pytest.raises(ValueError, match="bounded"):
            source_record(source, raw_data=data)


def test_raw_digest_and_response_truthfulness():
    with pytest.raises(ValueError):
        RawArtifact(availability="digest_only")
    with pytest.raises(ValueError):
        RawArtifact(availability="not_captured", sha256="a" * 64)
    with pytest.raises(ValueError):
        source_record(
            snapshot(
                raw_artifact={
                    "availability": "retained",
                    "sha256": "a" * 64,
                    "media_type": "application/pdf",
                }
            ),
            raw_data=b"other bytes",
        )
    parsed = ResponseCapture(
        origin="driver_parsed",
        raw_availability="unavailable",
        parsed={"result": True},
        reason="driver exposes parsed output only",
    )
    assert parsed.raw_base64 is None and parsed.raw_sha256 is None
    with pytest.raises(ValueError):
        ResponseCapture(origin="driver_parsed", raw_availability="retained", parsed={"ok": True})
    raw = b'{ "ok" : true }\n'
    captured = ResponseCapture(
        origin="driver_raw",
        raw_availability="retained",
        raw_base64=base64.b64encode(raw).decode(),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert base64.b64decode(captured.raw_base64) == raw


def test_safe_source_url_policy_preserves_public_query_and_rejects_secrets():
    url = "https://example.org/article?id=123&format=pdf"
    assert SourceDescription(type="pdf", public_url=url, url_state="retained").public_url == url
    for url in (
        "https://user:password@example.org/a",
        "https://example.org/a?token=secret",
        "https://example.org/a#token",
        "https://example.org/a?api_key=secret",
        "https://example.org/a?id=BearerABCDEFGHIJ",
        "https://example.org/a?id=" + "a8f3b7c9" * 30,
    ):
        with pytest.raises(ValueError):
            SourceDescription(type="pdf", public_url=url, url_state="retained")
    unavailable = SourceDescription(
        type="pdf",
        url_state="unavailable",
        url_unavailable_reason="signed location cannot be retained safely",
    )
    assert unavailable.public_url is None


@pytest.mark.parametrize("key", ["headers", "Authorization", "api_key", "base_url", "settings"])
def test_request_allowlist_rejects_transport_secrets_and_settings(key):
    request = copy.deepcopy(FIXTURE["invocations"][0]["envelope"]["request"])
    request[key] = "SECRET-MUST-NOT-BE-STORED"
    with pytest.raises(ValueError, match="unsupported"):
        prepared(snapshot(), request=request)
    with pytest.raises(ValueError):
        ModelIdentity(backend="OPENAI_API", identifier="https://secret@example.org/v1#model")


@pytest.mark.parametrize(
    "kind", ["systemone_relevance", "systemone_detail", "systemone_combined", "onnx"]
)
def test_per_actual_call_kinds_preserve_effective_payload(kind):
    if kind == "onnx":
        request = {
            "output_names": None,
            "feeds": {
                "input_ids": {"shape": [1, 3], "dtype": "int64", "values": [101, 55, 102]},
                "attention_mask": {"shape": [1, 3], "dtype": "int64", "values": [1, 1, 1]},
            },
        }
    else:
        request = {
            "state": "  exact effective window  ",
            "questions": {"relevance": {"type": "noul", "instructions": "Does it concern mice?"}},
            "max_len": 768,
        }
    spans = (
        ()
        if kind == "onnx"
        else (
            InputSpan(
                payload_path=("state",), start=0, end=len(request["state"]), origin="template"
            ),
            InputSpan(
                payload_path=("questions", "relevance", "instructions"),
                start=0,
                end=len(request["questions"]["relevance"]["instructions"]),
                origin="template",
            ),
        )
    )
    invocation = prepared(
        snapshot(),
        call_kind=kind,
        request=request,
        input_spans=spans,
        source_view_ids=(),
        quotation_producer="not_produced",
    )
    assert invocation.request == request and invocation.request_sha256 == digest(request)
    with pytest.raises(ValueError):
        prepared(
            snapshot(),
            call_kind=kind,
            request=request,
            input_spans=(),
            quotation_producer="may_produce",
        )


def test_origin_maps_require_complete_disjoint_coverage_and_empty_messages():
    source = snapshot()
    with pytest.raises(ValueError, match="complete origin coverage"):
        prepared(source, input_spans=())
    spans = [span.model_dump(mode="json") for span in prepared(source).input_spans]
    spans[0]["end"] -= 1
    spans[0]["view_end"] -= 1
    spans[0]["source_end"] -= 1
    with pytest.raises(ValueError, match="complete origin coverage"):
        prepared(source, input_spans=spans)
    with pytest.raises(ValueError, match="overlapping"):
        prepared(source, input_spans=spans + spans)
    request = {"model": "fixture-model", "messages": [{"role": "user", "content": ""}]}
    assert prepared(source, request=request, input_spans=(), source_view_ids=()).input_spans == ()


async def test_sqlite_storage_immutability_lifecycle_and_legacy(session, stored):
    source, row = stored
    invocation = invocation_record(prepared(source))
    session.add(invocation)
    await session.commit()
    legacy = ArticlePrediction(article_id=source.article_id, model_used="legacy")
    bound = ArticlePrediction(
        article_id=source.article_id,
        model_used="captured",
        provenance_project_id=source.project_id,
        source_snapshot_id=source.id,
        invocation_id=invocation.id,
        invocation_group_id=invocation.group_id,
    )
    review = HumanAnnotation(
        article_id=source.article_id, **review_binding(source, source.views[1].id)
    )
    session.add_all([legacy, bound, review])
    await session.commit()
    assert legacy.provenance_state == "legacy_unversioned"
    assert (
        bound.provenance_state == "invocation_captured" and review.provenance_state == "view_bound"
    )
    for statement in (
        update(SourceSnapshot).where(SourceSnapshot.id == row.id).values(payload={"forged": True}),
        update(Invocation).where(Invocation.id == invocation.id).values(request_sha256="0" * 64),
        update(ArticlePrediction)
        .where(ArticlePrediction.id == bound.id)
        .values(
            source_snapshot_id=None,
            provenance_project_id=None,
            invocation_id=None,
            invocation_group_id=None,
        ),
        update(HumanAnnotation)
        .where(HumanAnnotation.id == review.id)
        .values(source_view_sha256="0" * 64),
        update(ArticlePrediction)
        .where(ArticlePrediction.id == legacy.id)
        .values(source_snapshot_id=source.id, provenance_project_id=source.project_id),
        delete(SourceSnapshot).where(SourceSnapshot.id == source.id),
    ):
        async with session.begin_nested():
            with pytest.raises(IntegrityError):
                await session.execute(statement)
    complete_invocation(invocation, "outcome_unknown")
    await session.commit()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                update(Invocation)
                .where(Invocation.id == invocation.id)
                .values(
                    outcome="succeeded",
                    completed_at=datetime.now(UTC),
                    response_capture={"origin": "driver_parsed", "raw_availability": "unavailable"},
                    reconciliation={
                        "actor_ref": "test",
                        "reason": "claimed recovery",
                        "recorded_at": datetime.now(UTC).isoformat(),
                    },
                )
            )
    response = ResponseCapture(
        origin="driver_parsed",
        raw_availability="unavailable",
        parsed={"answer": True},
        reason="parsed driver only",
    )
    with pytest.raises(ValueError, match="audited"):
        complete_invocation(invocation, "succeeded", response=response)
    complete_invocation(
        invocation,
        "succeeded",
        response=response,
        reconciliation=Reconciliation(actor_ref="reviewer:local-1", reason="recovered call output"),
    )
    await session.commit()
    with pytest.raises(ValueError, match="terminal"):
        complete_invocation(invocation, "failed", response=response)
    async with session.begin_nested():
        with pytest.raises(IntegrityError):
            await session.execute(
                update(Invocation).where(Invocation.id == invocation.id).values(outcome="failed")
            )
    assert await session.scalar(select(SourceSnapshot.snapshot_sha256)) == source.sha256


async def test_scoped_fks_reject_wrong_article_project_search_and_view(session, stored):
    from app.db.models import Search

    source, _ = stored
    other_project = Project(title="Other project")
    session.add(other_project)
    await session.flush()
    other_article = Article(project_id=other_project.id)
    other_search = Search(project_id=other_project.id, name="Other search")
    session.add_all([other_article, other_search])
    await session.commit()
    invalid = [
        {
            "article_id": other_article.id,
            "provenance_project_id": source.project_id,
            "source_snapshot_id": source.id,
        },
        {
            "article_id": source.article_id,
            "provenance_project_id": other_project.id,
            "source_snapshot_id": source.id,
        },
        {
            "article_id": source.article_id,
            "provenance_project_id": source.project_id,
            "source_snapshot_id": source.id,
            "search_id": other_search.id,
        },
    ]
    for values in invalid:
        async with session.begin_nested():
            with pytest.raises(IntegrityError):
                await session.execute(insert(ArticlePrediction).values(model_used="bad", **values))
    async with session.begin_nested():
        with pytest.raises(IntegrityError):
            await session.execute(
                insert(HumanAnnotation).values(
                    article_id=source.article_id,
                    **{
                        **review_binding(source, source.views[1].id),
                        "source_view_id": uuid.uuid4(),
                    },
                )
            )
    assert (await session.scalars(select(ArticlePrediction))).all() == []


async def test_owner_deletion_is_explicit_retention_boundary(session, stored):
    source, _ = stored
    session.add(invocation_record(prepared(source)))
    await session.commit()
    await session.execute(delete(Project).where(Project.id == source.project_id))
    await session.commit()
    assert await session.scalar(select(SourceSnapshot.id)) is None
    assert await session.scalar(select(Invocation.id)) is None
