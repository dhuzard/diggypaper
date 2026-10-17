"""Coherent, non-retrospective source capture before an actual call."""

import hashlib
import uuid

import pytest
from sqlalchemy import select, update

from app.db.models import Article, ArticleFile, Project, SourceSnapshot
from app.provenance import source_from_record
from app.provenance_capture import capture_article_source
from app.provenance_render import render_chat


@pytest.fixture
async def source_article(session):
    project = Project(title="Capture")
    session.add(project)
    await session.flush()
    article = Article(
        project_id=project.id,
        title="Original",
        abstract="Abstract A",
        methods_text="Methods A",
        sections=[{"title": "Methods", "kind": "methods", "text": "Section A"}],
        fulltext_source="pdf:upload",
        fulltext_url="https://example.org/paper.pdf?token=private",
    )
    session.add(article)
    await session.flush()
    session.add(ArticleFile(article_id=article.id, data=b"%PDF-1.4 source A"))
    await session.commit()
    return project, article


async def test_capture_refreshes_stale_article_and_retains_exact_revision(session, source_article):
    project, article = source_article
    assert article.title == "Original"
    # Simulate a committed metadata/source refresh that bypasses this identity
    # map, as a separate worker would. Capture must not trust the cached object.
    await session.execute(
        update(Article)
        .where(Article.id == article.id)
        .values(
            title="Updated",
            abstract="Abstract B",
            methods_text="Methods B",
            sections=[{"title": "Methods", "kind": "methods", "text": "Section B"}],
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    assert article.title == "Original"

    first, view_id = await capture_article_source(
        session, project_id=project.id, article_id=article.id, text_sections=["methods"]
    )
    await session.commit()  # Required before dispatch; no lock crosses inference.
    assert first.components.title == "Updated"
    assert first.components.methods_text == "Methods B"
    assert first.views[0].text == "Methods\nSection B"
    assert view_id == first.views[0].id
    assert first.raw_artifact.availability == "digest_only"
    assert first.raw_artifact.sha256 == hashlib.sha256(b"%PDF-1.4 source A").hexdigest()
    assert first.source.url_state == "unavailable"
    assert first.source.public_url is None
    assert first.source.url_unavailable_reason
    stored = await session.scalar(select(SourceSnapshot).where(SourceSnapshot.id == first.id))
    assert source_from_record(stored).sha256 == first.sha256
    rendered = render_chat(first, view_id, "system", "{article_text} {methods_text}")
    assert rendered.user.text == "Methods\nSection B Methods B"

    await session.execute(
        update(Article)
        .where(Article.id == article.id)
        .values(methods_text="Methods C")
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    second, _ = await capture_article_source(
        session, project_id=project.id, article_id=article.id, text_sections=["methods"]
    )
    await session.commit()
    assert second.id != first.id and second.sha256 != first.sha256
    assert source_from_record(stored).components.methods_text == "Methods B"
    assert second.components.methods_text == "Methods C"


async def test_capture_rejects_other_project_without_writing_snapshot(session, source_article):
    _, article = source_article
    with pytest.raises(LookupError):
        await capture_article_source(
            session,
            project_id=uuid.uuid4(),
            article_id=article.id,
            text_sections=["methods"],
        )
    await session.rollback()
    assert await session.scalar(select(SourceSnapshot.id)) is None


async def test_capture_retains_extra_section_metadata_without_changing_text(
    session, source_article
):
    project, article = source_article
    await session.execute(
        update(Article)
        .where(Article.id == article.id)
        .values(sections=[{"title": "Methods", "kind": "methods", "text": "A", "extra": "B"}])
    )
    await session.commit()
    snapshot, _ = await capture_article_source(
        session, project_id=project.id, article_id=article.id, text_sections=["methods"]
    )
    await session.commit()
    assert snapshot.components.sections[0].model_dump()["extra"] == "B"
    assert snapshot.views[0].text == "Methods\nA"
