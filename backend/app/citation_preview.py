"""Bind a two-hop import to the preview ranked by the server. Redis also serves Celery."""

import json
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import REDIS_URL
from app.citation_graph import CitationPathResult
from app.db.models import Article
from app.provenance import digest

PREVIEW_TTL = 15 * 60


async def corpus_state(db: AsyncSession, project_id: uuid.UUID) -> tuple[set[str], str]:
    """Fingerprint the project rows and the inputs used to deduplicate an import."""
    rows = await db.execute(
        select(Article.id, Article.pmid, Article.doi, Article.title)
        .where(Article.project_id == project_id)
        .order_by(Article.id)
    )
    records = [[str(i), p, d, t] for i, p, d, t in rows]
    # The graph canonicalizes numerical identifiers, including legacy zero-padded PMIDs.
    known = {pmid.strip().lstrip("0") for _, pmid, _, _ in records if pmid}
    return known, digest(records)


def make_run(
    graph: CitationPathResult,
    ranked: list[tuple[str, int]],
    *,
    project_id: uuid.UUID,
    corpus_sha256: str,
    selection_sha256: str,
    min_overlap: int,
    limit: int,
    candidates: int,
) -> dict:
    return {
        "schema_version": "citation-run-v1",
        "project_id": str(project_id),
        "captured_at": datetime.now(UTC).isoformat(),
        "seed_pmids": list(graph.seeds),
        "direction": graph.direction,
        "depth": graph.depth,
        "ranking": "path_count",
        "provider": graph.provider,
        "scoring_version": graph.scoring_version,
        "min_overlap": min_overlap,
        "max_candidates": limit,
        "selection_sha256": selection_sha256,
        "corpus_sha256": corpus_sha256,
        "queried_pmids": list(graph.queried_pmids),
        "edge_count": len(graph.edges),
        "edge_sha256": digest([[e.source_pmid, e.target_pmid, e.relation] for e in graph.edges]),
        "candidates": candidates,
        "ranked": [
            {
                "pmid": p,
                "path_count": score,
                "direct_seed_overlap": graph.direct_seed_overlap.get(p, 0),
            }
            for p, score in ranked
        ],
        "completeness": "provider-responses-only",
    }


def _cache_key(project_id: uuid.UUID, user_id: uuid.UUID, token: uuid.UUID) -> str:
    return f"diggypaper:citation-preview:{project_id}:{user_id}:{token}"


async def save_preview(
    project_id: uuid.UUID, user_id: uuid.UUID, token: uuid.UUID, snapshot: dict
) -> None:
    async with Redis.from_url(REDIS_URL) as redis:
        await redis.set(
            _cache_key(project_id, user_id, token), json.dumps(snapshot), ex=PREVIEW_TTL
        )


async def take_preview(project_id: uuid.UUID, user_id: uuid.UUID, token: uuid.UUID) -> dict | None:
    async with Redis.from_url(REDIS_URL) as redis:
        data = await redis.getdel(_cache_key(project_id, user_id, token))
    return json.loads(data) if data is not None else None
