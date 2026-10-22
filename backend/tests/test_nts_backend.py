"""The NTS extractor as a DETERMINISTIC inference backend (Phase 1 of the DB integration)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime

import pytest
from nts_support import ANIMALS, section
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    ArticlePrediction,
    BackendType,
    InferenceJob,
    InferenceJobStatus,
    Invocation,
    ModelRegistry,
    PredictionStatus,
    Project,
    ProjectStatus,
    PromptTemplate,
)
from app.evidence_prediction import prediction_envelope
from app.nts import backend
from app.nts.fields import NTS_FIELDS, NTS_QUESTION, decision_from_extraction, is_nts_project
from app.nts.source import freeze_snapshot
from app.provenance import (
    Components,
    RawArtifact,
    Section,
    SourceDescription,
    SourceSnapshotV1,
    invocation_from_record,
)
from app.provenance_render import composed_view
from app.tasks.inference import _run_inference

ETHICS = section(
    "Ethics statement",
    "The project was carried out with the approval of the Bioethics and Research Ethics "
    "Committee of the University (435/13 March 2024), and the project authorization was issued "
    "by the National Sanitary Veterinary and Food Safety Authority (406/29 April 2024). The "
    "non-technical summary is NTS-RO-406074 v.1, 8 October 2025.",
)


def _snapshot(sections, *, methods_text=None):
    comps = Components(
        title="T",
        abstract=None,
        methods_text=methods_text,
        sections=tuple(Section(**s) for s in sections),
    )
    view = composed_view(comps, ["methods"])
    snapshot = SourceSnapshotV1(
        project_id=uuid.uuid4(),
        article_id=uuid.uuid4(),
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        pmid="1",
        pmcid=None,
        doi="10.5555/x",
        source=SourceDescription(
            type="test",
            public_url=None,
            url_state="not_captured",
            extraction_method="test",
            extraction_version="0",
            software_build="unknown",
        ),
        components=comps,
        raw_artifact=RawArtifact(availability="not_captured"),
        views=(view,),
    )
    return snapshot, view.id


def test_template_is_an_nts_project_and_default_fields_are_not():
    assert is_nts_project(NTS_FIELDS)
    assert not is_nts_project([{"key": "use_ai_behavior", "type": "boolean"}])
    assert NTS_QUESTION


def test_extraction_over_platform_snapshot_links_quotes_to_fields():
    snapshot, view_id = _snapshot([ETHICS, ANIMALS])
    decision = backend.extract_decision(snapshot, view_id)
    assert decision["nts_identifier"].startswith("NTS-RO-406074 v1")
    assert "NTS-ES-111111 v2" in decision["nts_identifier"]
    assert "406/29 April 2024" in decision["national_authorization"]
    assert decision["animal_counts"] != "none reported"
    assert "confidence_score" not in decision  # never auto-accepted
    quotes = decision["evidence_quotes"]
    assert quotes and len(set(quotes)) == len(quotes)
    keys = {link["field_key"] for link in decision["evidence_links"]}
    assert keys == {"nts_identifier", "national_authorization", "animal_counts"}
    view_text = next(v.text for v in snapshot.views if v.id == view_id)
    assert all(q in view_text for q in quotes)  # every quote is inside the captured view
    ex = decision["nts_extraction"]
    assert ex["schema"] == "nts-extraction/1.0"
    assert all(p["quote_index"] < len(quotes) for p in ex["proposals"])
    json.dumps(decision)  # JSON-serialisable as stored in decision_json


def test_sections_outside_the_view_are_not_extractable():
    intro = section("Introduction", "Background mentions NTS-FR-999999 v.1.", kind="introduction")
    snapshot, view_id = _snapshot([intro, ETHICS])
    frozen = freeze_snapshot(snapshot, view_id)
    assert frozen.source["sections"][0]["text"] == ""  # kept index, no text
    decision = backend.extract_decision(snapshot, view_id)
    assert "NTS-FR-999999" not in decision["nts_identifier"]


def test_methods_text_only_article_is_extractable():
    snapshot, view_id = _snapshot([], methods_text=ETHICS["text"])
    decision = backend.extract_decision(snapshot, view_id)
    assert decision["nts_identifier"].startswith("NTS-RO-406074")


def test_empty_extraction_reports_none_and_no_links():
    snapshot, view_id = _snapshot([section("Methods", "Cells were cultured.")])
    decision = backend.extract_decision(snapshot, view_id)
    assert decision["nts_identifier"] == "none reported"
    assert decision["evidence_quotes"] == [] and decision["evidence_links"] == []


def test_decision_builder_rejects_unknown_kind():
    class P:
        quote, kind, finding_id, section_index = "q", "other", "f", 0
        window, view_window = (0, 1), (0, 1)
        receipt, content, typed, observation_ids = {}, {}, {}, []

    extraction = {
        "proposals": [P()],
        "nts_mentions": [],
        "authorizations": [],
        "observations": [],
        "reconciliation": {},
        "missing_counts": True,
    }
    with pytest.raises(KeyError):
        decision_from_extraction(extraction)


class NeverCalled:
    calls = 0

    async def infer(self, *a, **k):
        self.calls += 1
        raise AssertionError("deterministic backend must not call the chat service")


async def _nts_project(
    session: AsyncSession, *, fields=None, name=backend.MODEL_NAME, identifier=None
):
    project = Project(
        title="NTS",
        created_by=None,
        status=ProjectStatus.FROZEN,
        question=NTS_QUESTION,
        fields=fields if fields is not None else NTS_FIELDS,
    )
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="deterministic extraction",
        user_prompt_schema="Article text:\n{article_text}",
    )
    model = ModelRegistry(
        name=name,
        backend_type=BackendType.DETERMINISTIC,
        model_identifier=identifier or backend.EXTRACTORS[name][0],
    )
    session.add_all([prompt, model])
    await session.commit()
    article = Article(
        project_id=project.id,
        pmid="13304911",
        doi="10.3390/ph19060846",
        title="Case B",
        sections=[ETHICS, ANIMALS],
    )
    session.add(article)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used=name,
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    return project, article, job


async def test_inference_job_runs_the_extractor_with_full_provenance(engine, session):
    project, article, job = await _nts_project(session)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = NeverCalled()
    result = await _run_inference(str(job.id), session_factory=factory, inference=fake)

    assert result["processed"] == 1 and result["needs_review"] == 1 and fake.calls == 0
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED
    pred = (await session.execute(select(ArticlePrediction))).scalar_one()
    assert pred.status == PredictionStatus.NEEDS_HUMAN_REVIEW
    assert pred.model_used == backend.MODEL_NAME
    assert pred.decision_json["nts_identifier"].startswith("NTS-RO-406074 v1")
    assert pred.source_snapshot_id and pred.invocation_id
    checks = pred.quote_checks_json
    assert checks["state"] == "checked" and checks["input_ref"] == str(pred.invocation_id)
    assert {item["result"]["status"] for item in checks["items"]} == {"exact"}
    graph = pred.evidence_graph_json["envelope"]
    assert graph["state"] == "structured"
    field_of = {a["answer_ref"]: a["field_key"] for a in graph["answers"]}
    assert {field_of[link["answer_ref"]] for link in graph["links"]} == {
        "nts_identifier",
        "national_authorization",
        "animal_counts",
    }
    assert all(e["origin"] == "captured_input_context" for e in graph["evidence"])

    call = (await session.execute(select(Invocation))).scalar_one()
    assert call.outcome == "succeeded"
    assert call.response_capture["origin"] == "driver_raw"
    envelope = invocation_from_record(call)
    assert envelope.model.backend == "DETERMINISTIC"
    assert envelope.model.identifier == backend.MODEL_IDENTIFIER
    assert "deterministic-program" in call.reconciliation["reason"]
    # the recorded response is byte-for-byte the stored decision
    raw = base64.b64decode(call.response_capture["raw_base64"])
    assert json.loads(raw) == pred.decision_json

    # the envelope builder, called on the stored data, reproduces the structured graph
    rebuilt = prediction_envelope(
        prediction_id=pred.id,
        project_id=project.id,
        article_id=article.id,
        project=project,
        decision=pred.decision_json,
        invocation_id=pred.invocation_id,
        source_snapshot_id=pred.source_snapshot_id,
        quote_checks=checks,
    )
    assert rebuilt["envelope"]["links"] == graph["links"]


async def test_extractor_refuses_a_project_without_nts_fields(engine, session):
    _project, _article, job = await _nts_project(session, fields=[])
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with pytest.raises(Exception, match="deterministic_extractor_requires_nts_fields"):
        await _run_inference(str(job.id), session_factory=factory, inference=NeverCalled())
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED  # misconfiguration is loud, never silent
    assert (await session.execute(select(ArticlePrediction))).scalars().all() == []


GROUPS = section(
    "Dose finding",
    "The reported values reflect combined data (two females + two males per group; total n = 4). "
    "Doses were escalated. "
    "The reported values reflect combined data (two females + two males per group; total n = 4).",
)


def _proposals(decision):
    return decision["nts_extraction"]["proposals"]


def test_v2_words_short_statements_over_the_same_evidence_as_v1():
    snapshot, view_id = _snapshot([ETHICS, ANIMALS])
    v1 = backend.extract_decision(snapshot, view_id, "verbatim")
    v2 = backend.extract_decision(snapshot, view_id, "short")
    # same quotes, links, answers and typed values: only the statement and its origin differ
    for key in ("evidence_quotes", "evidence_links", "nts_identifier", "animal_counts"):
        assert v1[key] == v2[key]
    pairs = list(zip(_proposals(v1), _proposals(v2), strict=True))
    for a, b in pairs:
        assert (a["finding_id"], a["window"], a["receipt"]) == (
            b["finding_id"],
            b["window"],
            b["receipt"],
        )
        assert a["content"]["numeric_results"] == b["content"]["numeric_results"]
        assert a["content"]["origin"] == "source-explicit"
        assert a["content"]["statement"] == v1["evidence_quotes"][a["quote_index"]]
        assert b["content"]["origin"] == "ai-paraphrase"
        assert len(b["content"]["statement"]) < 300
    by_kind = {}
    for p in _proposals(v2):
        by_kind.setdefault(p["kind"], []).append(p["content"]["statement"])
    assert any("NTS-RO-406074 version 1 (8 October 2025)" in s for s in by_kind["nts-identifier"])
    assert all(s.endswith("(not an NTS identifier).") for s in by_kind["national-authorization"])
    assert any(
        s.startswith("Animal numbers reported") and "40" in s for s in by_kind["animal-counts"]
    )


def test_v2_count_statement_names_the_scope_and_lists_repeats_once():
    snapshot, view_id = _snapshot([GROUPS])
    decision = backend.extract_decision(snapshot, view_id, "short")
    counts = [p for p in _proposals(decision) if p["kind"] == "animal-counts"]
    assert counts
    statement = counts[0]["content"]["statement"]
    assert "2 female per group" in statement and "2 male per group" in statement
    assert statement.count("2 female per group") == 1
    assert len(counts[0]["content"]["numeric_results"]) >= 2  # every typed value is kept


def test_v2_statement_keeps_the_form_the_article_used():
    text = "Procedures were approved (ALURES #nts-es-414463)."
    snapshot, view_id = _snapshot([section("Ethics", text)])
    decision = backend.extract_decision(snapshot, view_id, "short")
    statement = next(
        p["content"]["statement"] for p in _proposals(decision) if p["kind"] == "nts-identifier"
    )
    assert 'NTS-ES-414463, written "nts-es-414463"' in statement


@pytest.mark.parametrize(
    ("name", "origin"),
    [
        ("nts-deterministic-extractor-v1", "source-explicit"),
        ("nts-deterministic-extractor-v2", "ai-paraphrase"),
    ],
)
async def test_each_registered_version_runs_its_own_output(engine, session, name, origin):
    _project, _article, job = await _nts_project(session, name=name)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _run_inference(str(job.id), session_factory=factory, inference=NeverCalled())
    pred = (await session.execute(select(ArticlePrediction))).scalar_one()
    assert pred.model_used == name
    origins = {p["content"]["origin"] for p in _proposals(pred.decision_json)}
    assert origins == {origin}
    call = (await session.execute(select(Invocation))).scalar_one()
    assert invocation_from_record(call).model.identifier == backend.EXTRACTORS[name][0]


async def test_a_registry_row_naming_another_version_is_refused(engine, session):
    _project, _article, job = await _nts_project(
        session, identifier=backend.EXTRACTORS["nts-deterministic-extractor-v1"][0]
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with pytest.raises(Exception, match="deterministic_extractor_unknown_version"):
        await _run_inference(str(job.id), session_factory=factory, inference=NeverCalled())
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED
    assert (await session.execute(select(ArticlePrediction))).scalars().all() == []
