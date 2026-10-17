"""Tests de la task inférence massive (app/tasks/inference.py) — 100 % offline."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    ArticlePrediction,
    InferenceJob,
    InferenceJobStatus,
    Invocation,
    ModelRegistry,
    PredictionStatus,
    Project,
    ProjectStatus,
    PromptTemplate,
    SourceSnapshot,
)
from app.provenance import invocation_from_record, source_from_record
from app.tasks.inference import _run_inference, _triage


class FakeInference:
    """Inference mockée : renvoie une prédiction fixe (confidence configurable)."""

    def __init__(self, confidence: float = 0.9):
        self._confidence = confidence
        self.calls = 0

    async def infer(self, model, system_prompt, user_prompt, *, trace=None) -> dict:
        self.calls += 1
        output = {
            "use_ai_behavior": True,
            "animal_sex": "BOTH_SEXES",
            "confidence_score": self._confidence,
            "evidence_quotes": ["mice were randomized"],
            "reasoning_summary": "ok",
        }
        if trace is not None:
            await trace.before_dispatch(
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                }
            )
            raw = json.dumps({"choices": [{"message": {"content": json.dumps(output)}}]}).encode()
            await trace.on_http_response(raw, 200)
        return output


# --- _triage ---


def test_triage_high_confidence_auto_accepted():
    assert _triage(0.9) == PredictionStatus.AUTO_ACCEPTED
    assert _triage(0.86) == PredictionStatus.AUTO_ACCEPTED


def test_triage_low_confidence_auto_rejected():
    assert _triage(0.1) == PredictionStatus.AUTO_REJECTED
    assert _triage(0.14) == PredictionStatus.AUTO_REJECTED


def test_triage_mid_confidence_needs_review():
    assert _triage(0.5) == PredictionStatus.NEEDS_HUMAN_REVIEW
    assert _triage(0.85) == PredictionStatus.NEEDS_HUMAN_REVIEW  # bornes incluses
    assert _triage(0.15) == PredictionStatus.NEEDS_HUMAN_REVIEW


def test_triage_none_needs_review():
    assert _triage(None) == PredictionStatus.NEEDS_HUMAN_REVIEW


# --- _run_inference ---


async def _seed(session: AsyncSession, project, prompt, model, n_articles: int = 2):
    for i in range(n_articles):
        article = Article(
            project_id=project.id, pmid=str(100 + i), title=f"A{i}", methods_text=f"Methods {i}."
        )
        session.add(article)
    await session.commit()


async def test_run_inference_completes(engine, session):
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
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
    await _seed(session, project, prompt, model, n_articles=2)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeInference(confidence=0.9)
    result = await _run_inference(str(job.id), session_factory=factory, inference=fake)

    assert result["processed"] == 2
    assert result["auto_accepted"] == 2
    assert result["auto_rejected"] == 0
    assert result["needs_review"] == 0
    assert fake.calls == 2
    # job COMPLETED
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED
    # predictions créées
    preds = (await session.execute(select(ArticlePrediction))).scalars().all()
    assert len(preds) == 2
    assert all(p.status == PredictionStatus.AUTO_ACCEPTED for p in preds)
    snapshots = (await session.execute(select(SourceSnapshot))).scalars().all()
    calls = (await session.execute(select(Invocation))).scalars().all()
    assert len(snapshots) == len(calls) == 2
    assert all(p.source_snapshot_id and p.invocation_id for p in preds)
    for call in calls:
        envelope = invocation_from_record(call)
        source = next(s for s in snapshots if s.id == call.source_snapshot_id)
        assert envelope.request["messages"][1]["content"] == (
            f"Article Text:\n{source_from_record(source).components.methods_text}"
        )
        assert call.outcome == "succeeded"
        assert call.response_capture["origin"] == "driver_raw"


async def test_run_inference_triage_mixed(engine, session):
    """Confiances variées → triage mixte (AUTO_ACCEPTED / AUTO_REJECTED / NEEDS_HUMAN_REVIEW)."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    await _seed(session, project, prompt, model, n_articles=3)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    # 3 articles, confiances 0.9 / 0.1 / 0.5 → 1 accepted, 1 rejected, 1 review
    confidences = [0.9, 0.1, 0.5]

    class SeqInference:
        def __init__(self):
            self.i = 0

        async def infer(self, model, system_prompt, user_prompt, *, trace=None) -> dict:
            c = confidences[self.i]
            self.i += 1
            output = {
                "use_ai_behavior": True,
                "animal_sex": "BOTH_SEXES",
                "confidence_score": c,
                "evidence_quotes": [],
                "reasoning_summary": "ok",
            }
            if trace is not None:
                await trace.before_dispatch(
                    {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                    }
                )
                await trace.on_http_response(
                    json.dumps(
                        {"choices": [{"message": {"content": json.dumps(output)}}]}
                    ).encode(),
                    200,
                )
            return output

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _run_inference(str(job.id), session_factory=factory, inference=SeqInference())
    assert result["auto_accepted"] == 1
    assert result["auto_rejected"] == 1
    assert result["needs_review"] == 1
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED


async def test_chat_prefilter_records_only_actual_calls_and_binds_producer(engine, session):
    project = Project(title="P", status=ProjectStatus.FROZEN)
    session.add(project)
    await session.flush()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1",
        system_prompt="Screen the article",
        user_prompt_schema="{article_text}",
    )
    models = [
        ModelRegistry(name="pre", model_identifier="pre"),
        ModelRegistry(name="main", model_identifier="main"),
    ]
    articles = [
        Article(project_id=project.id, title="Reject", abstract="reject", methods_text="r"),
        Article(project_id=project.id, title="Pass", abstract="pass", methods_text="p"),
    ]
    session.add_all([prompt, *models, *articles])
    await session.flush()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="main",
        prefilter_model="pre",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    class PrefilterInference:
        def __init__(self):
            self.requests = []

        async def infer(self, model, system_prompt, user_prompt, *, trace=None):
            assert trace is not None
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
            }
            await trace.before_dispatch(body)
            self.requests.append((model, user_prompt))
            confidence = 0.1 if model == "pre" and "reject" in user_prompt else 0.9
            output = {"confidence_score": confidence, "evidence_quotes": []}
            await trace.on_http_response(
                json.dumps({"choices": [{"message": {"content": json.dumps(output)}}]}).encode(),
                200,
            )
            return output

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = PrefilterInference()
    result = await _run_inference(str(job.id), session_factory=factory, inference=fake)
    assert result["processed"] == 2 and result["prefiltered"] == 1
    assert len(fake.requests) == 3
    calls = (await session.execute(select(Invocation))).scalars().all()
    preds = (await session.execute(select(ArticlePrediction))).scalars().all()
    assert len(calls) == 3 and len(preds) == 2
    by_article = {article.id: article for article in articles}
    for pred in preds:
        article = by_article[pred.article_id]
        own_calls = sorted(
            (call for call in calls if call.article_id == pred.article_id),
            key=lambda call: call.sequence,
        )
        if article.title == "Reject":
            assert len(own_calls) == 1
            assert own_calls[0].stage == "prefilter"
            assert pred.invocation_id == own_calls[0].id
        else:
            assert [call.stage for call in own_calls] == ["prefilter", "main"]
            assert len({call.group_id for call in own_calls}) == 1
            assert len({call.source_snapshot_id for call in own_calls}) == 1
            assert pred.invocation_id == own_calls[1].id


async def test_run_inference_empty_corpus(engine, session):
    """Corpus vide → job COMPLETED, 0 processed."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _run_inference(str(job.id), session_factory=factory, inference=FakeInference())
    assert result["processed"] == 0
    await session.refresh(job)
    assert job.status == InferenceJobStatus.COMPLETED


async def test_run_inference_failure_marks_failed(engine, session):
    """Échec inférence → job FAILED (pas bloqué en RUNNING)."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    model = ModelRegistry(name="Kev-4B", model_identifier="x")
    session.add(model)
    await session.commit()
    await _seed(session, project, prompt, model, n_articles=1)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class FailInference:
        async def infer(self, model, system_prompt, user_prompt, *, trace=None) -> dict:
            if trace is not None:
                await trace.before_dispatch(
                    {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                    }
                )
            raise RuntimeError("LLM down")

    with pytest.raises(RuntimeError):
        await _run_inference(str(job.id), session_factory=factory, inference=FailInference())
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED
    calls = (await session.execute(select(Invocation))).scalars().all()
    assert len(calls) == 1 and calls[0].outcome == "outcome_unknown"
    assert await session.scalar(select(ArticlePrediction.id)) is None


async def test_run_inference_stops_when_cancelled(engine, session):
    """Arrêt demandé en cours de route : on s'arrête avant l'article suivant, sans e-mail."""
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    session.add(ModelRegistry(name="Kev-4B", model_identifier="x"))
    await session.commit()
    await _seed(session, project, prompt, None, n_articles=4)
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    class StopAfterFirst(FakeInference):
        async def infer(self, model, system_prompt, user_prompt, *, trace=None) -> dict:
            out = await super().infer(model, system_prompt, user_prompt, trace=trace)
            row = await session.get(InferenceJob, job.id)
            row.status, row.error = InferenceJobStatus.FAILED, "cancelled"  # clic sur « Arrêter »
            await session.commit()
            return out

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = StopAfterFirst()
    result = await _run_inference(str(job.id), session_factory=factory, inference=fake)
    assert result["cancelled"] is True and result["processed"] == 1 and fake.calls == 1
    await session.refresh(job)
    assert job.status == InferenceJobStatus.FAILED and job.error == "cancelled"
    assert len((await session.execute(select(ArticlePrediction))).scalars().all()) == 1


async def test_run_inference_skips_job_cancelled_before_start(engine, session):
    project = Project(title="P", created_by=None, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(project_id=project.id, version_number="v1.0", system_prompt="SYS")
    session.add(prompt)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Kev-4B",
        status=InferenceJobStatus.FAILED,
        error="cancelled",
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeInference()
    assert await _run_inference(str(job.id), session_factory=factory, inference=fake) == {}
    assert fake.calls == 0
