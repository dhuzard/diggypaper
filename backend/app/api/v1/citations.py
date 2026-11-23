"""Enrichir le corpus par chaînage de citations : références et citants des articles de départ.

Les graines sont des articles du projet (une sélection, comme pour créer une recherche), sans
analyse préalable. Les candidats les plus liés aux graines entrent dans le corpus avec leur
origine ; une recherche est créée sur eux, à analyser ensuite avec le modèle pour garder ou écarter.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import citation_preview, citations
from app.api.deps import get_current_user, get_db
from app.api.v1.auth import _rate_limit_dependency
from app.api.v1.projects import (
    IMPORT_RATE_LIMIT,
    _make_http_client,
    _require_lead_or_admin,
    _require_no_active_job,
    _require_project_member,
)
from app.api.v1.searches import Selection, select_articles
from app.celery_app import celery_app
from app.citation_graph import (
    MAX_SEEDS,
    CitationBudgetExceeded,
    CitationProviderError,
    discover_citation_paths,
)
from app.citation_selection import select_ranked
from app.db.models import Article, Project, ProjectStatus, User
from app.provenance import digest
from app.pubmed import _esearch_max
from app.tasks import import_pubmed_task

router = APIRouter(prefix="/projects", tags=["citations"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]


class CitationRequest(BaseModel):
    selection: Selection = Selection()  # articles de départ (déf. : tout le corpus)
    direction: Literal["backward", "forward", "both"] = "both"  # références / citants / les deux
    depth: Literal[1, 2] = 1  # 1 = classement historique ; 2 = chemins de citation
    min_overlap: int = Field(default=1, ge=1, le=50)  # depth 1: graines ; depth 2: chemins
    max_candidates: int = Field(default=300, ge=1, le=10000)
    preview_token: uuid.UUID | None = None
    dry_run: bool = False  # estimation seulement


async def _selected_seeds(
    db: AsyncSession, project: Project, selection: Selection
) -> tuple[list[str], list[uuid.UUID]]:
    ids = await select_articles(db, project, selection)
    rows = await db.scalars(
        select(Article.pmid)
        .where(Article.id.in_(ids), Article.project_id == project.id, Article.pmid.is_not(None))
        .order_by(Article.pmid)
    )
    return [p for p in rows if p], ids


def _validate_seeds(seeds: list[str]) -> None:
    if not seeds:
        raise HTTPException(status_code=422, detail="no seed with a PMID")
    if len(seeds) > MAX_SEEDS:
        raise HTTPException(status_code=422, detail=f"too many seeds ({len(seeds)} > {MAX_SEEDS})")


def _stale() -> HTTPException:
    return HTTPException(status_code=409, detail="citation_preview_stale")


async def _depth_two(
    db: AsyncSession, project: Project, user: User, payload: CitationRequest, limit: int
) -> tuple[list[str], dict]:
    snapshot = None
    request_sha = digest(payload.model_dump(mode="json", exclude={"dry_run", "preview_token"}))
    if payload.dry_run:
        token = uuid.uuid4()
    else:
        token = payload.preview_token
        if token is None:
            raise _stale()
        snapshot = await citation_preview.take_preview(project.id, user.id, token)
        if snapshot is None or snapshot["request_sha256"] != request_sha:
            raise _stale()
    selection = payload.selection
    if selection.sample_n and selection.seed is None:
        # A random sample belongs to the preview. Resolve it identically at confirmation.
        selection = selection.model_copy(update={"seed": token.int})
    seeds, ids = await _selected_seeds(db, project, selection)
    known, corpus_sha = await citation_preview.corpus_state(db, project.id)
    selection_sha = digest({"article_ids": [str(i) for i in ids], "seed_pmids": seeds})
    if snapshot is not None:
        run = snapshot["run"]
        if (
            run["selection_sha256"] != selection_sha
            or run["corpus_sha256"] != corpus_sha
            or run["max_candidates"] != limit
        ):
            raise _stale()
        return seeds, run

    _validate_seeds(seeds)
    async with _make_http_client() as http:
        try:
            graph = await discover_citation_paths(seeds, http, depth=2, direction=payload.direction)
            selection_result = await select_ranked(
                graph, known, db, project.id, http, min_score=payload.min_overlap, limit=limit
            )
        except CitationBudgetExceeded as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except CitationProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    scores = {p: n for p, n in graph.path_counts.items() if p not in known}
    direct = {p: n for p, n in graph.direct_seed_overlap.items() if p not in known}
    run = citation_preview.make_run(
        graph,
        selection_result.ranked,
        project_id=project.id,
        corpus_sha256=corpus_sha,
        selection_sha256=selection_sha,
        min_overlap=payload.min_overlap,
        limit=limit,
        candidates=len(scores),
    )
    run["requested_max_candidates"] = payload.max_candidates
    run["doi_exclusion"] = selection_result.doi_metadata
    run["source_search_id"] = str(selection.from_search_id) if selection.from_search_id else None
    await citation_preview.save_preview(
        project.id, user.id, token, {"request_sha256": request_sha, "run": run}
    )
    return seeds, {
        "selected": len(ids),
        "seeds": len(seeds),
        "candidates": len(scores),
        "by_overlap": {str(k): sum(n >= k for n in direct.values()) for k in (1, 2, 3, 5)},
        "by_score": {str(k): sum(n >= k for n in scores.values()) for k in (1, 2, 3, 5)},
        "depth": 2,
        "ranking": "path_count",
        "scoring_version": graph.scoring_version,
        "queried_pmids": len(graph.queried_pmids),
        "edges": len(graph.edges),
        "max": _esearch_max(),
        "ranked_count": len(selection_result.ranked),
        "preview_token": str(token),
    }


@router.post(
    "/{project_id}/citations",
    status_code=202,
    dependencies=[Depends(_rate_limit_dependency("import", IMPORT_RATE_LIMIT))],
)
async def expand_by_citations(
    project_id: uuid.UUID, payload: CitationRequest, db: DbDep, user: UserDep
) -> dict:
    """``dry_run`` : combien de candidats (par seuil de recouvrement). Sinon tâche de fond :
    import des meilleurs candidats + recherche créée sur eux (id dans le résultat du job)."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    if payload.depth == 1:
        if payload.preview_token is not None:
            raise _stale()
        seeds, ids = await _selected_seeds(db, project, payload.selection)
        _validate_seeds(seeds)
        if payload.dry_run:
            known = set(
                await db.scalars(select(Article.pmid).where(Article.project_id == project.id))
            )
            async with _make_http_client() as http:
                links = await citations.fetch_links(seeds, http, payload.direction)
            return {
                "selected": len(ids),
                "seeds": len(seeds),
                "candidates": sum(1 for p in links if p not in known),
                "by_overlap": citations.overlap_histogram(links, known),
                "max": _esearch_max(),
            }
    if not payload.dry_run:
        if project.status == ProjectStatus.PROCESSING:
            raise HTTPException(status_code=409, detail="import already in progress")
        await _require_no_active_job(db, project.id)
    limit = min(payload.max_candidates, _esearch_max())
    if payload.depth == 2:
        try:
            seeds, run = await _depth_two(db, project, user, payload, limit)
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="citation_preview_unavailable") from exc
        if payload.dry_run:
            return run
    origin = payload.selection.from_search_id
    options = (
        {"depth": 2, "citation_run": run}
        if payload.depth == 2
        else {
            "seeds": seeds,
            "direction": payload.direction,
            "depth": 1,
            "min_overlap": payload.min_overlap,
            "max_candidates": limit,
            "search_id": str(origin) if origin else None,
        }
    )
    job = import_pubmed_task.delay(
        str(project.id),
        f"citations ({payload.direction}, {len(seeds)})",
        None,
        {"kind": "citations", "citations": options},
    )
    status = job.state if celery_app.conf.task_always_eager else "PENDING"
    return {"job_id": job.id, "status": status}
