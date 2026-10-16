"""Actual scorer calls, not reconstructed article text, are the provenance unit."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    ArticlePrediction,
    BackendType,
    InferenceJob,
    Invocation,
    ModelRegistry,
    Project,
    PromptTemplate,
)
from app.inference import InferenceError, onnx_driver
from app.inference.systemone import decide
from app.provenance import invocation_from_record
from app.provenance_capture import capture_article_source
from app.provenance_scorer import PersistedScorerTrace
from app.tasks.inference import _run_inference


async def context(session, backend, text):
    project = Project(title="P", question="Relevant?")
    session.add(project)
    await session.flush()
    article = Article(project_id=project.id, title="T", abstract=text)
    prompt = PromptTemplate(project_id=project.id, version_number="v1", system_prompt="S")
    model = ModelRegistry(
        name="scorer",
        backend_type=backend,
        model_identifier="http://service#checkpoint"
        if backend == BackendType.SYSTEMONE
        else "model.onnx",
    )
    session.add_all([article, prompt, model])
    await session.flush()
    job = InferenceJob(project_id=project.id, prompt_template_id=prompt.id, model_used="scorer")
    session.add(job)
    await session.commit()
    snapshot, main_view = await capture_article_source(
        session, project_id=project.id, article_id=article.id, text_sections=["abstract"]
    )
    await session.commit()
    view_id = next(v.id for v in snapshot.views if v.kind == "title_abstract")
    trace = PersistedScorerTrace(
        session,
        snapshot=snapshot,
        view_id=view_id,
        job=job,
        prompt=prompt,
        model=model,
        field_schema=[],
        group_id=uuid.uuid4(),
        stage="main",
    )
    return trace, next(v.text for v in snapshot.views if v.id == view_id)


async def test_systemone_captures_each_actual_window_and_detail(session):
    trace, text = await context(session, BackendType.SYSTEMONE, "a" * 4300)
    seen = []

    def handler(request):
        body = json.loads(request.read())
        seen.append(body)
        score = [0.2, 0.9, 0.1][min(len(seen) - 1, 2)]
        answers = {"relevance": {"noul": score}}
        if "field" in body["questions"]:
            answers["field"] = {"noul": 0.8}
        return httpx.Response(200, json={"answers": answers})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await decide(
            "http://service#checkpoint",
            text,
            "Relevant?",
            [{"key": "field", "label": "Field?", "type": "boolean"}],
            http=http,
            windows=3,
            trace=trace,
        )
    calls = (
        (await session.execute(select(Invocation).order_by(Invocation.sequence))).scalars().all()
    )
    assert len(calls) == len(seen) == 4
    assert [invocation_from_record(c).request for c in calls] == seen
    assert [c.call_kind for c in calls] == ["systemone_relevance"] * 3 + ["systemone_detail"]
    assert all(c.outcome == "succeeded" for c in calls)
    assert all(invocation_from_record(c).quotation_producer == "not_produced" for c in calls)
    assert invocation_from_record(calls[-1]).execution.confidence_call_id == calls[1].id
    assert trace.producing_invocation.id == calls[-1].id
    assert result["field"] is True and result["evidence_quotes"] == []


async def test_systemone_without_details_binds_winning_window(session):
    trace, text = await context(session, BackendType.SYSTEMONE, "a" * 4300)
    scores = iter((0.2, 0.9, 0.1))

    def handler(request):
        return httpx.Response(200, json={"answers": {"relevance": {"noul": next(scores)}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await decide(
            "http://service#checkpoint", text, "Relevant?", [], http=http, windows=3, trace=trace
        )
    calls = (
        (await session.execute(select(Invocation).order_by(Invocation.sequence))).scalars().all()
    )
    assert trace.producing_invocation.id == calls[1].id
    assert all(c.outcome == "succeeded" for c in calls)


async def test_systemone_transport_loss_keeps_unknown_call(session):
    trace, text = await context(session, BackendType.SYSTEMONE, "abstract")

    def handler(request):
        raise httpx.ConnectError("connection lost")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(InferenceError, match="connection_error"):
            await decide("http://service#checkpoint", text, "Relevant?", [], http=http, trace=trace)
    call = (await session.execute(select(Invocation))).scalar_one()
    assert call.outcome == "outcome_unknown" and trace.producing_invocation is None


async def test_onnx_retains_only_actual_named_token_feeds(session, monkeypatch):
    np = pytest.importorskip("numpy")
    trace, text = await context(session, BackendType.ONNX_LOCAL, "abstract")

    class Tokenizer:
        def encode(self, value):
            assert value == text
            return type("Encoded", (), {"ids": [7, 8], "attention_mask": [1, 1]})()

    class Session:
        def run(self, names, feeds):
            assert names is None
            assert set(feeds) == {"input_ids", "attention_mask"}
            return [np.array([[0.0]])]

    monkeypatch.setitem(
        onnx_driver._SESSIONS,
        "model.onnx",
        (Session(), Tokenizer(), ["input_ids", "attention_mask"]),
    )
    assert await onnx_driver.score_relevance("model.onnx", text, trace=trace) == 0.5
    call = (await session.execute(select(Invocation))).scalar_one()
    actual = invocation_from_record(call)
    assert actual.call_kind == "onnx" and actual.quotation_producer == "not_produced"
    assert actual.request == {
        "feeds": {
            "input_ids": {"shape": [1, 2], "dtype": "int64", "values": [7, 8]},
            "attention_mask": {"shape": [1, 2], "dtype": "int64", "values": [1, 1]},
        },
        "output_names": None,
    }
    assert call.outcome == "succeeded" and trace.producing_invocation.id == call.id


async def test_normal_cascade_binds_scorer_result_to_actual_main_call(engine, session, monkeypatch):
    from app.inference import scoring

    project = Project(title="P", question="Relevant?")
    session.add(project)
    await session.flush()
    prompt = PromptTemplate(project_id=project.id, version_number="v1", system_prompt="S")
    article = Article(project_id=project.id, title="T", abstract="actual abstract")
    pre = ModelRegistry(
        name="pre", backend_type=BackendType.SYSTEMONE, model_identifier="http://service#pre"
    )
    main = ModelRegistry(
        name="main", backend_type=BackendType.SYSTEMONE, model_identifier="http://service#main"
    )
    session.add_all([prompt, article, pre, main])
    await session.flush()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="main",
        prefilter_model="pre",
    )
    session.add(job)
    await session.commit()

    def handler(request):
        body = json.loads(request.read())
        assert body["state"] in (
            "T\nactual abstract",
            "Abstract\nactual abstract",
            "actual abstract",
        )
        p = 0.5 if body["model"] == "pre" else 0.9
        return httpx.Response(200, json={"answers": {"relevance": {"noul": p}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        real_decide = decide

        async def traced_decide(*args, **kwargs):
            return await real_decide(*args, **kwargs, http=client)

        monkeypatch.setattr(scoring, "decide", traced_decide)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        await _run_inference(str(job.id), session_factory=factory, inference=object())

    calls = (
        (await session.execute(select(Invocation).order_by(Invocation.sequence))).scalars().all()
    )
    prediction = (await session.execute(select(ArticlePrediction))).scalar_one()
    assert [(c.sequence, c.stage, c.outcome) for c in calls] == [
        (0, "prefilter", "succeeded"),
        (1, "main", "succeeded"),
    ]
    assert prediction.invocation_id == calls[1].id
    assert prediction.quote_checks_json is None and prediction.evidence_quotes == []


async def test_normal_scorer_without_actual_call_fails_closed(engine, session, monkeypatch):
    from app.inference import scoring

    trace, _ = await context(session, BackendType.SYSTEMONE, "abstract")

    async def skipped_driver(*args, **kwargs):
        return {"confidence_score": 0.9, "decision": "INCLUDED", "evidence_quotes": []}

    monkeypatch.setattr(scoring, "decide", skipped_driver)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with pytest.raises(InferenceError, match="scorer_call_not_captured"):
        await _run_inference(str(trace.job.id), session_factory=factory, inference=object())
    assert (await session.execute(select(ArticlePrediction))).scalars().all() == []
