"""Capture a coherent current article revision for a future inference call.

The caller must commit the returned snapshot before any external model request.
Source writers must participate in the Article row lock; the source-coherence
slice makes full-text/PDF replacement do so. No historical source is inferred.

An unchanged article yields the snapshot already stored for it rather than a new
row: two captures are the same revision when every captured value is equal
(components, views, source attribution, raw-artifact digest, identifiers), not
when a text digest alone coincides. A snapshot is only reused while a review token
issued for it now would still expire before the retention sweep may delete it.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Article,
    ArticleFile,
    ArticlePrediction,
    BenchmarkPrediction,
    GoldRevision,
    HumanAnnotation,
    Invocation,
    SourceSnapshot,
)
from app.provenance import (
    Components,
    RawArtifact,
    SourceDescription,
    SourceSnapshotV1,
    canonical_json,
    source_from_record,
    source_record,
)
from app.provenance_render import composed_view, legacy_methods_view, title_abstract_view
from app.review_source import TOKEN_LIFETIME

# Unreferenced snapshots older than this are deleted by the maintenance sweep, so a
# capture reuses a snapshot only while a token issued now expires before that.
UNREFERENCED_RETENTION = timedelta(hours=24)
REUSE_WINDOW = UNREFERENCED_RETENTION - TOKEN_LIFETIME

# Every column that may name a snapshot. Finding evidence and support assessments
# reference predictions, which reference their snapshot here.
_REFERENCES = (
    Invocation.source_snapshot_id,
    ArticlePrediction.source_snapshot_id,
    BenchmarkPrediction.source_snapshot_id,
    HumanAnnotation.source_snapshot_id,
    GoldRevision.source_snapshot_ref,
)


def _revision_key(payload: dict) -> bytes:
    """Everything captured except the per-row id, capture time and random view ids."""
    views = [{k: v for k, v in view.items() if k != "id"} for view in payload["views"]]
    return canonical_json({**payload, "id": None, "captured_at": None, "views": views})


async def capture_article_source(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    text_sections: list[str] | None,
) -> tuple[SourceSnapshotV1, uuid.UUID]:
    """Lock, refresh, and store exact components/views in the current transaction.

    Returns the immutable snapshot and selected composed-view ID. The caller
    commits this short transaction before inference and renders only from the
    returned snapshot, never from its possibly stale ORM Article instance.
    """
    if session.get_bind().dialect.name == "sqlite":
        connection = await session.connection()
        driver = (await connection.get_raw_connection()).driver_connection
        if not driver.in_transaction:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
    article = await session.scalar(
        select(Article)
        .where(Article.id == article_id, Article.project_id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if article is None:
        raise LookupError("article not found in project")

    # The PDF and article fields are read inside the same short source lock.
    raw = await session.scalar(select(ArticleFile.data).where(ArticleFile.article_id == article_id))
    artifact = (
        RawArtifact(
            availability="digest_only",
            sha256=hashlib.sha256(raw).hexdigest(),
            media_type="application/pdf",
        )
        if raw is not None
        else RawArtifact(availability="not_captured")
    )
    url = article.fulltext_url
    if url is None:
        source = SourceDescription(type=article.fulltext_source or "metadata_only")
    else:
        try:
            source = SourceDescription(
                type=article.fulltext_source or "metadata_only",
                public_url=url,
                url_state="retained",
            )
        except ValueError:
            # A signed/private URL cannot be made public by stripping parameters.
            source = SourceDescription(
                type=article.fulltext_source or "metadata_only",
                url_state="unavailable",
                url_unavailable_reason="source URL is not a safe public permalink",
            )
    components = Components(
        title=article.title,
        abstract=article.abstract,
        methods_text=article.methods_text,
        sections=tuple(article.sections or ()),
    )
    selected = composed_view(components, text_sections)
    abstract_only = composed_view(components, ["abstract"])
    snapshot = SourceSnapshotV1(
        project_id=project_id,
        article_id=article_id,
        pmid=article.pmid,
        pmcid=article.pmcid,
        doi=article.doi,
        source=source,
        components=components,
        raw_artifact=artifact,
        views=(
            selected,
            abstract_only,
            legacy_methods_view(components),
            title_abstract_view(components),
        ),
    )
    key = _revision_key(snapshot.model_dump(mode="json"))
    candidates = await session.scalars(
        select(SourceSnapshot)
        .where(
            SourceSnapshot.project_id == project_id,
            SourceSnapshot.article_id == article_id,
            SourceSnapshot.payload["views"][0]["sha256"].as_string() == selected.sha256,
            SourceSnapshot.captured_at >= snapshot.captured_at - REUSE_WINDOW,
        )
        .order_by(SourceSnapshot.captured_at.desc())
    )
    for row in candidates:
        if _revision_key(row.payload) == key:
            existing = source_from_record(row)
            return existing, existing.views[0].id
    session.add(source_record(snapshot))
    await session.flush()
    return snapshot, selected.id


async def unreferenced_snapshot_sweep(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete snapshots nothing references once older than ``UNREFERENCED_RETENTION``.

    A snapshot named by any invocation, prediction, review or gold revision is kept
    forever: results keep their exact source. Returns the number deleted; the
    caller commits.
    """
    now = now or datetime.now(UTC)
    referenced = or_(*(exists().where(column == SourceSnapshot.id) for column in _REFERENCES))
    result = await session.execute(
        delete(SourceSnapshot).where(
            SourceSnapshot.captured_at < now - UNREFERENCED_RETENTION, ~referenced
        )
    )
    return result.rowcount or 0
