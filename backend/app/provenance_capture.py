"""Capture a coherent current article revision for a future inference call.

The caller must commit the returned snapshot before any external model request.
Source writers must participate in the Article row lock; the source-coherence
slice makes full-text/PDF replacement do so. No historical source is inferred.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Article, ArticleFile
from app.provenance import (
    Components,
    RawArtifact,
    SourceDescription,
    SourceSnapshotV1,
    source_record,
)
from app.provenance_render import composed_view, legacy_methods_view, title_abstract_view


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
    session.add(source_record(snapshot))
    await session.flush()
    return snapshot, selected.id
