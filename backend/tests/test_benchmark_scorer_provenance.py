"""Benchmark scorers retain actual calls and frozen inputs, with no invented history."""

import json

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    BackendType,
    BenchmarkPrediction,
    BenchmarkRun,
    BenchmarkStatus,
    Invocation,
    ModelRegistry,
    Project,
    PromptTemplate,
    SourceSnapshot,
)
from app.inference import InferenceError, onnx_driver, scoring
from app.inference.systemone import decide
from app.provenance import invocation_from_record, source_from_record
from app.searches import default_search
from app.tasks.benchmark import _run_benchmark


async def _world(session, *, backend=BackendType.SYSTEMONE, fields=None, models=1):
    project = Project(
        title="P",
        question="Relevant?",
        text_sections=["abstract", "methods"],
        fields=fields or [{"key": "note", "label": "Note?", "type": "text"}],
        read_windows=3,
    )
    session.add(project)
    await session.flush()
    article = Article(
        project_id=project.id, title="T", abstract="a" * 4300, methods_text="frozen methods"
    )
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1",
        system_prompt="CONFIG ONLY",
        user_prompt_schema="{article_text}",
    )
    registered = [
        ModelRegistry(
            name=f"scorer-{i}",
            backend_type=backend,
            model_identifier=f"http://service#checkpoint-{i}"
            if backend == BackendType.SYSTEMONE
            else f"model-{i}.onnx",
        )
        for i in range(models)
    ]
    session.add_all([article, prompt, *registered])
    await session.flush()
    search = await default_search(session, project)
    run = BenchmarkRun(
        project_id=project.id,
        search_id=search.id,
        prompt_template_id=prompt.id,
        models_tested=[m.name for m in registered],
        options={},
        variants=[
            {"name": "frozen-variant", "prompt_template_id": str(prompt.id)},
            {"name": "ignored-scorer-variant", "system_prompt": "ignored"},
        ],
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    return project, article, prompt, run, registered


async def _run(engine, run):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return await _run_benchmark(str(run.id), session_factory=factory, inference=object())


@pytest.mark.parametrize("details", [False, True])
async def test_systemone_benchmark_binds_winning_or_detail_call(
    engine, session, monkeypatch, details
):
    fields = (
        [{"key": "field", "label": "Field?", "type": "boolean"}]
        if details
        else [{"key": "note", "label": "Note?", "type": "text"}]
    )
    project, article, prompt, run, _ = await _world(session, fields=fields)
    seen = []

    def handler(request):
        body = json.loads(request.read())
        seen.append(body)
        score = (0.2, 0.9, 0.1, 0.4)[len(seen) - 1]
        answers = {"relevance": {"noul": score}}
        if "field" in body["questions"]:
            answers["field"] = {"noul": 0.8}
        return httpx.Response(200, json={"answers": answers})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def traced_decide(*args, **kwargs):
            return await decide(*args, **kwargs, http=http)

        monkeypatch.setattr(scoring, "decide", traced_decide)
        result = await _run(engine, run)
    assert result["models"] == 1
    calls = (
        (await session.execute(select(Invocation).order_by(Invocation.sequence))).scalars().all()
    )
    prediction = (await session.execute(select(BenchmarkPrediction))).scalar_one()
    assert len(calls) == len(seen) == (4 if details else 3)
    assert [invocation_from_record(c).request for c in calls] == seen
    assert [c.sequence for c in calls] == list(range(len(calls)))
    assert all(c.outcome == "succeeded" for c in calls)
    assert all(c.benchmark_run_id == run.id and c.inference_job_id is None for c in calls)
    assert all(c.search_id == run.search_id and c.variant == "frozen-variant" for c in calls)
    for call in calls:
        envelope = invocation_from_record(call)
        assert envelope.prompt.template_id == prompt.id
        assert envelope.prompt.version == "v1"
        assert envelope.prompt.field_schema == fields
        assert envelope.quotation_producer == "not_produced"
        assert call.reconciliation["actor_ref"] == f"worker:benchmark:{run.id}"
    selected = calls[-1] if details else calls[1]
    assert prediction.invocation_id == selected.id
    assert prediction.invocation_group_id == selected.group_id
    assert prediction.source_snapshot_id == selected.source_snapshot_id
    assert prediction.provenance_project_id == project.id
    assert prediction.search_id == run.search_id and prediction.variant == "frozen-variant"
    assert prediction.confidence_score == 0.9 and prediction.decision_json["evidence_quotes"] == []
    if details:
        assert prediction.decision_json["field"] is True
        assert invocation_from_record(selected).execution.confidence_call_id == calls[1].id
    snapshot = source_from_record(await session.get(SourceSnapshot, selected.source_snapshot_id))
    assert snapshot.article_id == article.id
    view = next(
        v for v in snapshot.views if v.id == invocation_from_record(selected).source_view_ids[0]
    )
    assert "frozen methods" in view.text


async def test_benchmark_reuses_frozen_source_after_replacement(engine, session, monkeypatch):
    _, article, _, run, _ = await _world(session, models=2)
    seen = []

    async def handler(request):
        body = json.loads(request.read())
        seen.append(body)
        if len(seen) == 1:
            # Replacement commits during the first provider call, after capture released its lock.
            await session.execute(
                update(Article)
                .where(Article.id == article.id)
                .values(abstract="replacement abstract", methods_text="replacement methods")
            )
            await session.commit()
        return httpx.Response(200, json={"answers": {"relevance": {"noul": 0.8}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def traced_decide(*args, **kwargs):
            return await decide(*args, **kwargs, http=http)

        monkeypatch.setattr(scoring, "decide", traced_decide)
        await _run(engine, run)
    predictions = (await session.execute(select(BenchmarkPrediction))).scalars().all()
    assert len(predictions) == 2 and len(seen) == 6
    assert [r["state"] for r in seen[:3]] == [r["state"] for r in seen[3:]]
    assert all("replacement" not in r["state"] for r in seen)
    assert len({p.source_snapshot_id for p in predictions}) == 1
    assert len({p.invocation_group_id for p in predictions}) == 2
    assert (await session.get(Article, article.id)).abstract == "replacement abstract"


async def test_benchmark_onnx_retains_actual_feeds_from_title_abstract(
    engine, session, monkeypatch
):
    np = pytest.importorskip("numpy")
    _, article, _, run, models = await _world(session, backend=BackendType.ONNX_LOCAL)
    seen_text = []

    class Tokenizer:
        def encode(self, text):
            seen_text.append(text)
            return type("Encoded", (), {"ids": [7, 8], "attention_mask": [1, 1]})()

    class Session:
        def run(self, names, feeds):
            assert names is None
            assert set(feeds) == {"input_ids", "attention_mask"}
            return [np.array([[0.0]])]

    monkeypatch.setitem(
        onnx_driver._SESSIONS,
        models[0].model_identifier,
        (Session(), Tokenizer(), ["input_ids", "attention_mask"]),
    )
    await _run(engine, run)
    call = (await session.execute(select(Invocation))).scalar_one()
    envelope = invocation_from_record(call)
    prediction = (await session.execute(select(BenchmarkPrediction))).scalar_one()
    assert seen_text == ["T\n" + article.abstract]
    assert envelope.call_kind == "onnx" and envelope.quotation_producer == "not_produced"
    assert envelope.prompt.field_schema == []
    assert envelope.request == {
        "feeds": {
            "input_ids": {"shape": [1, 2], "dtype": "int64", "values": [7, 8]},
            "attention_mask": {"shape": [1, 2], "dtype": "int64", "values": [1, 1]},
        },
        "output_names": None,
    }
    assert prediction.invocation_id == call.id and call.benchmark_run_id == run.id
    assert call.inference_job_id is None and prediction.confidence_score == 0.5
    assert models[0].model_identifier not in onnx_driver._SESSIONS


@pytest.mark.parametrize(
    "failure", ["uncaptured", "transport", "http", "invalid_response", "invalid_json"]
)
async def test_benchmark_scorer_failure_never_binds_invented_call(
    engine, session, monkeypatch, failure
):
    _, _, _, run, _ = await _world(session)

    def handler(request):
        if failure == "transport":
            raise httpx.ConnectError("connection lost")
        if failure == "invalid_response":
            return httpx.Response(200, json={"answers": {}})
        if failure == "invalid_json":
            return httpx.Response(200, text="not json")
        return httpx.Response(503, text="provider body excluded")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def traced_decide(*args, **kwargs):
            if failure == "uncaptured":
                return {"confidence_score": 0.9, "decision": "INCLUDED", "evidence_quotes": []}
            return await decide(*args, **kwargs, http=http)

        monkeypatch.setattr(scoring, "decide", traced_decide)
        expected = {
            "uncaptured": "scorer_call_not_captured",
            "transport": "connection_error",
            "http": "HTTP 503",
            "invalid_response": "invalid_response",
            "invalid_json": "Expecting value",
        }[failure]
        with pytest.raises(
            ValueError if failure == "invalid_json" else InferenceError, match=expected
        ):
            await _run(engine, run)
    assert (await session.execute(select(BenchmarkPrediction))).scalars().all() == []
    calls = (await session.execute(select(Invocation))).scalars().all()
    if failure == "uncaptured":
        assert calls == []
    else:
        assert len(calls) == 1
        assert calls[0].outcome == (
            "outcome_unknown"
            if failure == "transport"
            else "succeeded"
            if failure in ("invalid_response", "invalid_json")
            else "failed"
        )
        assert "provider body excluded" not in json.dumps(calls[0].response_capture)
    await session.refresh(run)
    assert run.status == BenchmarkStatus.FAILED
