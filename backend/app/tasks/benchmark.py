"""Task benchmark (mise au point) : comparer des variantes (modèle × prompt) sur un lot d'articles.

Même pattern d'exécution que ``import_pubmed_task`` : wrapper sync Celery → thread dédié →
``asyncio.run`` du core async ; engine par run (ou ``session_factory`` injecté en tests).

Le banc d'essai est le lot d'une recherche. Chaque variante répond à chaque article ; les réponses
sont conservées (``benchmark_predictions``) pour comparer côte à côte. Si le gold est demandé, les
variantes sont notées contre lui (articles du lot présents au gold, hors exemples du prompt) ;
sinon on mesure leur accord. Les variantes de prompt ne concernent que les modèles génératifs.
"""

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass

try:
    import resource  # Unix seulement : absent sous Windows
except ImportError:  # pragma: no cover - dépend de la plateforme
    resource = None

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import metrics
from app.article_text import fill_prompt
from app.celery_app import celery_app
from app.db.base import DATABASE_URL as _IMPORT_TIME_DATABASE_URL
from app.db.base import make_engine
from app.db.models import (
    Article,
    BackendType,
    BenchmarkPrediction,
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkStatus,
    GoldArticle,
    ModelRegistry,
    Project,
    PromptTemplate,
)
from app.gold_examples import examples_block, pick_examples
from app.inference import (
    InferenceError,
    OpenAICompatibleService,
    infer_with,
    make_inference_service,
    unload_ollama,
)
from app.inference.onnx_driver import unload as onnx_unload
from app.inference.scoring import is_scorer, score_prediction
from app.provenance_capture import capture_article_source
from app.provenance_chat import PersistedChatTrace
from app.provenance_render import render_chat
from app.provenance_scorer import PersistedScorerTrace
from app.schema import project_fields
from app.searches import ScopedProject, get_search, gold_condition, lot_condition

logger = logging.getLogger(__name__)

#: Hook de test : session_factory injecté pour le vrai chemin thread+asyncio.run.
_session_factory_override: async_sessionmaker[AsyncSession] | None = None

DEFAULT_USER_PROMPT = "Article text:\n{article_text}"


def _peak_ram_mb() -> float | None:
    """RAM max du processus (Mo) via ``resource`` ; ``None`` sans ce module (Windows)."""
    if resource is None:
        return None
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


@dataclass(frozen=True)
class _PromptVariant:
    name: str
    system: str
    user: str
    template_id: uuid.UUID | None = None
    template_version: str | None = None


def _make_session_factory():
    import os

    url = os.environ.get("DATABASE_URL", _IMPORT_TIME_DATABASE_URL)
    engine = make_engine(url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@celery_app.task(name="diggypaper.benchmark")
def benchmark_task(benchmark_run_id: str) -> dict:
    """Wrapper sync Celery : exécute le core async dans un thread dédié (C1/C2)."""
    result: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(_run_benchmark(benchmark_run_id))
        except BaseException as exc:  # propagée au thread principal
            error = exc

    thread = threading.Thread(target=_run, name="benchmark")
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


async def _run_benchmark(
    benchmark_run_id: str,
    session_factory=None,
    http=None,
    inference: OpenAICompatibleService | None = None,
) -> dict:
    """Core async du benchmark.

    - ``session_factory`` injectable (tests) ; sinon engine par run (C2).
    - ``inference`` injectable (tests) ; sinon ``make_inference_service``.
    - Séquentiel par modèle, puis par gold article.
    - Renvoie ``{"models", "articles", "results"}``.
    """
    if session_factory is None:
        session_factory = _session_factory_override
    if session_factory is not None:
        factory, engine = session_factory, None
    else:
        engine, factory = _make_session_factory()
    client = http if http is not None else None
    try:
        try:
            run_uuid = uuid.UUID(benchmark_run_id)
        except (TypeError, ValueError) as exc:
            raise LookupError(f"invalid benchmark run id: {benchmark_run_id}") from exc

        async with factory() as session:
            run = await session.get(BenchmarkRun, run_uuid)
            if run is None:
                raise LookupError(f"benchmark run not found: {benchmark_run_id}")
            if run.status in (BenchmarkStatus.FAILED, BenchmarkStatus.COMPLETED):
                return {}  # arrêté avant de démarrer, ou redélivré
            run.status = BenchmarkStatus.RUNNING
            await session.commit()

            try:
                if inference is None:
                    inference = await make_inference_service(session, http=client)
                return await _do_benchmark(session, run, inference)
            except Exception as exc:
                await _record_failure(session, run, exc)
                raise
    finally:
        if engine is not None:
            await engine.dispose()


async def _prompts(session: AsyncSession, run: BenchmarkRun) -> list[_PromptVariant]:
    """Freeze the resolved variant text and any real template identity."""
    out = []
    for v in run.variants or [{}]:
        pid = v.get("prompt_template_id") or (
            None if v.get("system_prompt") else run.prompt_template_id
        )
        if pid:
            pt = await session.get(PromptTemplate, uuid.UUID(str(pid)))
            if pt is None:
                raise LookupError(f"prompt template not found: {pid}")
            system, user = pt.system_prompt or "", pt.user_prompt_schema or DEFAULT_USER_PROMPT
            name = v.get("name") or pt.title or pt.version_number
            template_id, template_version = pt.id, pt.version_number
        else:
            system = v.get("system_prompt") or ""
            user = v.get("user_prompt_schema") or DEFAULT_USER_PROMPT
            name = v.get("name") or f"variante {len(out) + 1}"
            template_id, template_version = None, None
        out.append(_PromptVariant(name, system, user, template_id, template_version))
    return out


async def _do_benchmark(
    session: AsyncSession, run: BenchmarkRun, inference: OpenAICompatibleService
) -> dict:
    """Corps du benchmark : chaque (modèle, variante) répond à chaque article du lot."""
    base_project = await session.get(Project, run.project_id)
    search = await get_search(session, base_project, run.search_id)
    project = ScopedProject(base_project, search)  # question / items / parties de la recherche
    fields = project_fields(project)
    lot = (
        (
            await session.execute(
                select(Article)
                .where(lot_condition(search))
                .order_by(Article.created_at, Article.id)
            )
        )
        .scalars()
        .all()
    )
    if not lot:
        raise LookupError(f"no articles in the benchmark lot of search {search.id}")
    lot_ids = {a.id for a in lot}
    opts = run.options or {}

    # Références : exemples du prompt (hors lot de préférence) et notes de validation
    gold_all: dict = {}
    if opts.get("use_gold") or opts.get("gold_examples"):
        rows = await session.execute(
            select(GoldArticle).where(await gold_condition(session, base_project, search))
        )
        gold_all = {g.article_id: g.ground_truth_json for g in rows.scalars() if g.article_id}
    example_ids: list = []
    if opts.get("gold_examples") and gold_all:
        outside = {i: g for i, g in gold_all.items() if i not in lot_ids}
        pool = outside if len(outside) >= opts["gold_examples"] else gold_all
        example_ids = pick_examples(pool, int(opts["gold_examples"]), seed=1)
    shots = ""
    if example_ids:
        ex_articles = {
            a.id: a
            for a in (
                await session.execute(select(Article).where(Article.id.in_(example_ids)))
            ).scalars()
        }
        shots = examples_block(ex_articles, gold_all, example_ids, fields, project.text_sections)
    gold = {i: g for i, g in gold_all.items() if i in lot_ids} if opts.get("use_gold") else {}
    if opts.get("use_gold") and not (set(gold) - set(example_ids)):
        raise LookupError("no gold articles in the benchmark lot")

    variants = await _prompts(session, run)
    names = run.models_tested or []
    found = {
        m.name: m
        for m in (await session.execute(select(ModelRegistry).where(ModelRegistry.name.in_(names))))
        .scalars()
        .all()
    }
    models = [found[n] for n in names if n in found]
    if not models:
        raise LookupError(f"no registered models for {run.models_tested}")

    total_steps = sum(len(lot) * (1 if is_scorer(m) else len(variants)) for m in models)
    done_steps = 0
    results_payload = []
    ram_mb = 0.0
    frozen_sources = {}
    for model in models:
        scorer = is_scorer(model)  # un scoreur ignore le prompt : une seule « variante »
        todo = variants[:1] if scorer else variants
        by_variant: dict[str, dict] = {}
        pending: list[BenchmarkResult] = []
        for variant in todo:
            vname, system, user_schema = variant.name, variant.system, variant.user
            preds: dict = {}
            total_time = 0.0
            for article in lot:
                if await _cancelled(session, run):
                    return {"cancelled": True, "models": len(results_payload)}
                start = time.monotonic()
                error = None
                trace = None
                try:
                    if scorer:
                        if article.id not in frozen_sources:
                            frozen_sources[article.id] = await capture_article_source(
                                session,
                                project_id=run.project_id,
                                article_id=article.id,
                                text_sections=project.text_sections,
                            )
                            await session.commit()  # Release source lock before model dispatch.
                        snapshot, view_id = frozen_sources[article.id]
                        if model.backend_type == BackendType.ONNX_LOCAL:
                            view_id = next(
                                v.id for v in snapshot.views if v.kind == "title_abstract"
                            )
                        view = next(v for v in snapshot.views if v.id == view_id)
                        trace = PersistedScorerTrace(
                            session,
                            snapshot=snapshot,
                            view_id=view_id,
                            job=run,
                            prompt=None,
                            model=model,
                            field_schema=fields
                            if model.backend_type == BackendType.SYSTEMONE
                            else [],
                            group_id=uuid.uuid4(),
                            stage="main",
                            benchmark_run=run,
                            benchmark_variant=vname,
                            benchmark_system_template=system,
                            benchmark_user_template=user_schema,
                            benchmark_template_id=variant.template_id,
                            benchmark_template_version=variant.template_version,
                        )
                        prediction = await score_prediction(
                            model, project, article, light=False, trace=trace, text=view.text
                        )
                        if trace.producing_invocation is None:
                            raise InferenceError("scorer_call_not_captured")
                    else:
                        # Real chat drivers can retain an exact, committed source
                        # view and request. Injected test doubles retain legacy
                        # behavior unless they implement the actual trace path.
                        if isinstance(inference, OpenAICompatibleService):
                            if article.id not in frozen_sources:
                                frozen_sources[article.id] = await capture_article_source(
                                    session,
                                    project_id=run.project_id,
                                    article_id=article.id,
                                    text_sections=project.text_sections,
                                )
                                await session.commit()
                            snapshot, view_id = frozen_sources[article.id]
                            rendered = render_chat(
                                snapshot,
                                view_id,
                                system,
                                user_schema,
                                examples=shots,
                                example_ref=f"benchmark:{run.id}:gold_examples" if shots else None,
                            )
                            trace = PersistedChatTrace(
                                session,
                                snapshot=snapshot,
                                rendered=rendered,
                                job=run,
                                prompt=None,
                                model=model,
                                field_schema=fields,
                                group_id=uuid.uuid4(),
                                stage="main",
                                sequence=0,
                                benchmark_run=run,
                                benchmark_variant=vname,
                                benchmark_system_template=system,
                                benchmark_user_template=user_schema,
                                benchmark_template_id=variant.template_id,
                                benchmark_template_version=variant.template_version,
                            )
                        legacy_system_text = (system + "\n\n" + shots).strip() if shots else system
                        system_text = rendered.system.text if trace else legacy_system_text
                        user_text = (
                            rendered.user.text
                            if trace
                            else fill_prompt(user_schema, article, project.text_sections)
                        )
                        prediction = await infer_with(
                            model,
                            inference,
                            system_text,
                            user_text,
                            trace=trace,
                        )
                        if trace is not None and trace.producing_invocation is None:
                            raise InferenceError("benchmark call has no succeeded invocation")
                    if not isinstance(prediction, dict):
                        raise InferenceError("invalid_json")  # JSON valide mais pas un objet
                except InferenceError as exc:
                    if str(exc) != "invalid_json":
                        raise
                    prediction, error = {}, "invalid_json"  # sortie non-JSON : réponse fausse
                took = time.monotonic() - start
                total_time += took
                preds[article.id] = prediction
                # A scorer may select an earlier winning window; the last dispatched
                # call is not necessarily the one that produced this prediction.
                producing = trace.producing_invocation if trace else None
                session.add(
                    BenchmarkPrediction(
                        benchmark_run_id=run.id,
                        article_id=article.id,
                        model_name=model.name,
                        variant=vname,
                        decision_json=prediction,
                        confidence_score=prediction.get("confidence_score"),
                        error=error,
                        seconds=took,
                        search_id=run.search_id if trace else None,
                        provenance_project_id=run.project_id if trace else None,
                        source_snapshot_id=trace.snapshot.id if trace else None,
                        invocation_id=producing.id if producing else None,
                        invocation_group_id=trace.group_id if trace else None,
                    )
                )
                done_steps += 1
                run.progress = {
                    "done": done_steps,
                    "total": total_steps,
                    "model": model.name,
                    "variant": vname,
                }
                await session.commit()
                ram_mb = _peak_ram_mb()
            by_variant[vname] = preds
            res = BenchmarkResult(
                benchmark_run_id=run.id,
                model_name=model.name,
                variant=vname if len(variants) > 1 else None,
                execution_time_seconds=total_time,
            )
            details: dict = {"articles": len(lot)}
            if gold:
                scored = metrics.score(fields, preds, gold, exclude=set(example_ids))
                target, metric, value = metrics.primary_value(scored, fields)
                rel = scored["relevance"]
                res.n_scored = scored["n"]
                res.accuracy = (
                    scored["accuracy"] if scored["accuracy"] is not None else rel["accuracy"]
                )
                res.precision_score, res.recall_score, res.f1_score = (
                    rel["precision"],
                    rel["recall"],
                    rel["f1"],
                )
                details.update(
                    scored=scored, primary={"item": target, "metric": metric, "value": value}
                )
            res.details = details
            pending.append(res)
        if len(by_variant) > 1:  # accord entre variantes (sans référence)
            agree = metrics.agreement(by_variant, fields)
            for r in pending:
                r.details = {**(r.details or {}), "agreement": agree.get(r.variant)}
        for r in pending:
            session.add(r)
            results_payload.append(
                {
                    "model_name": r.model_name,
                    "variant": r.variant,
                    "execution_time_seconds": r.execution_time_seconds,
                    "accuracy": r.accuracy,
                    "precision_score": r.precision_score,
                    "recall_score": r.recall_score,
                    "f1_score": r.f1_score,
                    "ram_usage_mb": ram_mb,
                }
            )
        if scorer and model.backend_type == BackendType.ONNX_LOCAL:
            onnx_unload(model.model_identifier)
        base = getattr(inference, "base_url", None)
        if model.backend_type == BackendType.OLLAMA and isinstance(base, str):
            await unload_ollama(base, model.model_identifier)  # libère la RAM avant le suivant
        await session.commit()

    run.status = BenchmarkStatus.COMPLETED
    await session.commit()
    return {
        "models": len(models),
        "articles": len(lot),
        "variants": len(variants),
        "results": results_payload,
    }


async def _cancelled(session: AsyncSession, run: BenchmarkRun) -> bool:
    """Arrêt demandé depuis l'interface (POST …/benchmarks/{id}/cancel : statut FAILED en base)."""
    status = (
        await session.execute(select(BenchmarkRun.status).where(BenchmarkRun.id == run.id))
    ).scalar_one_or_none()
    return status == BenchmarkStatus.FAILED


async def _record_failure(session: AsyncSession, run: BenchmarkRun, exc: Exception) -> None:
    """Échec : run FAILED (best effort)."""
    logger.error("benchmark failed (run %s): %s", run.id, _sanitize(exc))
    try:
        await session.rollback()
        run.status = BenchmarkStatus.FAILED
        await session.commit()
    except Exception:
        logger.exception("failed to record benchmark failure")


def _sanitize(exc: BaseException) -> str:
    """Message court (pas de traceback brut)."""
    return str(exc)[:500] or type(exc).__name__
