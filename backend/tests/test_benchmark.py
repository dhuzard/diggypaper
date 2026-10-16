"""Tests de la task benchmark (app/tasks/benchmark.py) — 100 % offline."""

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    BenchmarkPrediction,
    BenchmarkRun,
    BenchmarkStatus,
    GoldArticle,
    Invocation,
    ModelRegistry,
    Project,
    PromptTemplate,
)
from app.inference import InferenceError, OpenAICompatibleService
from app.provenance import invocation_from_record
from app.tasks.benchmark import _run_benchmark


class FakeInference:
    """Inference mockée : renvoie une prédiction fixe."""

    def __init__(self, prediction: dict):
        self._prediction = prediction
        self.calls = 0

    async def infer(self, model, system_prompt, user_prompt) -> dict:
        self.calls += 1
        return dict(self._prediction)


def _gt(use_ai=True, sex="BOTH_SEXES", conf=0.9, quotes=("mice were randomized",)):
    return {
        "use_ai_behavior": use_ai,
        "animal_sex": sex,
        "confidence_score": conf,
        "evidence_quotes": list(quotes),
        "reasoning_summary": "ok",
    }


async def _seed(session: AsyncSession, project, prompt, model, gold_gt):
    article = Article(project_id=project.id, pmid="111", title="A", methods_text="Methods text.")
    session.add(article)
    await session.commit()
    session.add(
        GoldArticle(project_id=project.id, article_id=article.id, ground_truth_json=gold_gt)
    )
    await session.commit()


async def test_run_benchmark_completes(engine, session):
    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="SYS",
        user_prompt_schema="Article Text:\n{methods_text}",
    )
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b")
    session.add(model)
    await session.commit()
    gt = _gt()
    await _seed(session, project, prompt, model, gt)
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={"use_gold": True},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeInference(gt)
    result = await _run_benchmark(str(run.id), session_factory=factory, inference=fake)

    assert result["models"] == 1
    assert result["articles"] == 1
    assert result["results"][0]["model_name"] == "Kev-4B"
    assert result["results"][0]["f1_score"] == 1.0
    assert fake.calls == 1
    # run passé COMPLETED
    await session.refresh(run)
    assert run.status == BenchmarkStatus.COMPLETED


async def test_run_benchmark_no_gold_raises(engine, session):
    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={"use_gold": True},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with pytest.raises(LookupError):
        await _run_benchmark(str(run.id), session_factory=factory, inference=FakeInference(_gt()))


async def test_run_benchmark_inference_failure_marks_failed(engine, session, monkeypatch):
    """make_inference_service échoue → run FAILED (pas bloqué en RUNNING)."""
    import app.tasks.benchmark as bench

    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    await _seed(session, project, prompt, model, _gt())
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={"use_gold": True},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _fail_inference(db, http=None):
        raise RuntimeError("LLM config missing")

    monkeypatch.setattr(bench, "make_inference_service", _fail_inference)
    with pytest.raises(RuntimeError):
        await _run_benchmark(str(run.id), session_factory=factory)
    await session.refresh(run)
    assert run.status == BenchmarkStatus.FAILED


class RecordingInference(FakeInference):
    """Mémorise les prompts système reçus (variantes, exemples du gold)."""

    def __init__(self, prediction: dict):
        super().__init__(prediction)
        self.systems: list[str] = []

    async def infer(self, model, system_prompt, user_prompt) -> dict:
        self.systems.append(system_prompt)
        return await super().infer(model, system_prompt, user_prompt)


async def _project_with_articles(session, n=4):
    project = Project(title="P", created_by=None)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id, version_number="v1.0", system_prompt="AUTO", user_prompt_schema=None
    )
    session.add_all([prompt, ModelRegistry(name="Kev-4B", model_identifier="x")])
    arts = [
        Article(project_id=project.id, pmid=str(i), title=f"A{i}", abstract=f"abstract {i}")
        for i in range(n)
    ]
    session.add_all(arts)
    await session.commit()
    return project, prompt, arts


async def test_benchmark_variants_store_predictions_and_agreement(engine, session):
    project, prompt, arts = await _project_with_articles(session, 3)
    run = BenchmarkRun(
        project_id=project.id,
        models_tested=["Kev-4B"],
        variants=[
            {"name": "strict", "system_prompt": "Be strict."},
            {"name": "libre", "system_prompt": "Be free."},
        ],
        options={},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = RecordingInference(_gt())
    res = await _run_benchmark(str(run.id), session_factory=factory, inference=fake)
    assert res["variants"] == 2 and fake.calls == 6  # 3 articles × 2 variantes
    assert sorted(set(fake.systems)) == ["Be free.", "Be strict."]
    rows = (await session.execute(select(BenchmarkPrediction))).scalars().all()
    assert len(rows) == 6 and {r.variant for r in rows} == {"strict", "libre"}
    out = {(r["variant"]): r for r in res["results"]}
    assert (
        set(out) == {"strict", "libre"} and out["strict"]["f1_score"] is None
    )  # pas de gold demandé
    # accord entre variantes mesuré sans référence
    from app.db.models import BenchmarkResult

    results = (await session.execute(select(BenchmarkResult))).scalars().all()
    assert all(r.details["agreement"] == 1.0 for r in results)


async def test_benchmark_gold_examples_in_prompt_and_excluded_from_scoring(engine, session):
    project, prompt, arts = await _project_with_articles(session, 4)
    for a in arts:  # les 4 articles sont au gold ; 2 serviront d'exemples (hors validation)
        session.add(GoldArticle(project_id=project.id, article_id=a.id, ground_truth_json=_gt()))
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={"use_gold": True, "gold_examples": 2},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = RecordingInference(_gt())
    res = await _run_benchmark(str(run.id), session_factory=factory, inference=fake)
    assert all("verified answers" in s for s in fake.systems)  # exemples dans le prompt
    from app.db.models import BenchmarkResult

    (r,) = (await session.execute(select(BenchmarkResult))).scalars().all()
    assert r.n_scored == 2 and res["results"][0]["f1_score"] == 1.0  # 4 gold − 2 exemples


async def test_real_benchmark_chat_retains_actual_call_and_source(engine, session, monkeypatch):
    project, prompt, arts = await _project_with_articles(session, 1)
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"use_ai_behavior": true}'}}]},
        )

    async def skip_unload(*_args):
        pass

    monkeypatch.setattr("app.tasks.benchmark.unload_ollama", skip_unload)
    transport = httpx.MockTransport(respond)
    async with httpx.AsyncClient(transport=transport) as http:
        inference = OpenAICompatibleService("http://test/v1", http=http)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        await _run_benchmark(str(run.id), session_factory=factory, inference=inference)

    prediction = (await session.execute(select(BenchmarkPrediction))).scalar_one()
    invocation = (await session.execute(select(Invocation))).scalar_one()
    captured = invocation_from_record(invocation)
    assert len(requests) == 1
    assert prediction.invocation_id == invocation.id
    assert prediction.source_snapshot_id == captured.source_snapshot_id
    assert captured.benchmark_run_id == run.id
    assert captured.prompt.template_id == prompt.id
    assert captured.model.identifier == "x"
    assert captured.request["messages"][1]["content"] == "Article text:\nabstract 0"
    assert invocation.outcome == "succeeded"
    assert any(span.origin == "target" for span in captured.input_spans)


async def test_bound_benchmark_result_rejects_variant_and_result_rewrite(
    engine, session, monkeypatch
):
    project, prompt, arts = await _project_with_articles(session, 1)
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()

    async def skip_unload(*_args):
        pass

    monkeypatch.setattr("app.tasks.benchmark.unload_ollama", skip_unload)
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, json={"choices": [{"message": {"content": '{"use_ai_behavior": true}'}}]}
        )
    )
    async with httpx.AsyncClient(transport=transport) as http:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        await _run_benchmark(
            str(run.id),
            session_factory=factory,
            inference=OpenAICompatibleService("http://test/v1", http=http),
        )
    bound = (await session.execute(select(BenchmarkPrediction))).scalar_one()
    assert bound.invocation_id is not None

    for values in (
        {"variant": "another prompt"},
        {"decision_json": {"use_ai_behavior": False}},
        {"model_name": "another model"},
        {"error": "fabricated"},
        {"confidence_score": 0.1},
        {"seconds": 999.0},
    ):
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    update(BenchmarkPrediction)
                    .where(BenchmarkPrediction.id == bound.id)
                    .values(**values)
                )
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            session.add(
                BenchmarkPrediction(
                    benchmark_run_id=run.id,
                    article_id=arts[0].id,
                    model_name=bound.model_name,
                    variant="wrong variant",
                    decision_json=bound.decision_json,
                    search_id=bound.search_id,
                    provenance_project_id=bound.provenance_project_id,
                    source_snapshot_id=bound.source_snapshot_id,
                    invocation_id=bound.invocation_id,
                    invocation_group_id=bound.invocation_group_id,
                )
            )
            await session.flush()
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            session.add(
                BenchmarkPrediction(
                    benchmark_run_id=run.id,
                    article_id=arts[0].id,
                    model_name=bound.model_name,
                    variant=bound.variant,
                    decision_json={"use_ai_behavior": False},
                    search_id=bound.search_id,
                    provenance_project_id=bound.provenance_project_id,
                    source_snapshot_id=bound.source_snapshot_id,
                    invocation_id=bound.invocation_id,
                    invocation_group_id=bound.invocation_group_id,
                )
            )
            await session.flush()

    legacy = BenchmarkPrediction(
        benchmark_run_id=run.id,
        article_id=arts[0].id,
        model_name="legacy",
        variant="old",
        decision_json={"old": True},
    )
    session.add(legacy)
    await session.commit()
    await session.execute(
        update(BenchmarkPrediction)
        .where(BenchmarkPrediction.id == legacy.id)
        .values(variant="revised", decision_json={"old": False})
    )
    await session.commit()
    await session.refresh(legacy)
    assert legacy.variant == "revised" and legacy.decision_json == {"old": False}


async def test_failed_benchmark_http_retains_failure_without_prediction(engine, session):
    project, prompt, _arts = await _project_with_articles(session, 1)
    run = BenchmarkRun(
        project_id=project.id,
        prompt_template_id=prompt.id,
        models_tested=["Kev-4B"],
        options={},
        status=BenchmarkStatus.PENDING,
    )
    session.add(run)
    await session.commit()
    transport = httpx.MockTransport(lambda _request: httpx.Response(503, text="provider secret"))
    async with httpx.AsyncClient(transport=transport) as http:
        inference = OpenAICompatibleService("http://test/v1", http=http)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        with pytest.raises(InferenceError, match="HTTP 503"):
            await _run_benchmark(str(run.id), session_factory=factory, inference=inference)

    await session.refresh(run)
    assert run.status == BenchmarkStatus.FAILED
    assert (await session.execute(select(BenchmarkPrediction))).scalars().all() == []
    invocation = (await session.execute(select(Invocation))).scalar_one()
    assert invocation.outcome == "failed"
    assert "provider secret" not in str(invocation.response_capture)
