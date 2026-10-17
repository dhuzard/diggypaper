"""Opt-in PostgreSQL API race regression for first blind review submission."""

import asyncio
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_provenance_migration import alembic

from app.api.deps import get_db
from app.core.security import create_access_token
from app.db.models import (
    Article,
    HumanAnnotation,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Search,
    UserRole,
)
from app.main import create_app
from tests.conftest import _make_user

pytestmark = pytest.mark.skipif(
    not os.environ.get("SEIP_TEST_POSTGRES_URL"), reason="requires disposable PostgreSQL"
)


@pytest.fixture
async def postgres_database():
    admin_url = make_url(os.environ["SEIP_TEST_POSTGRES_URL"])
    name = "seip_review_test_" + uuid.uuid4().hex
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    test_url = admin_url.set(database=name).render_as_string(hide_password=False)
    engine = create_async_engine(test_url)
    try:
        yield test_url, engine
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            await connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        await admin.dispose()


async def test_concurrent_blind_posts_commit_only_first(postgres_database):
    url, engine = postgres_database
    await alembic(url, "upgrade", "head")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        user = await _make_user(session, "race@lab.fr", UserRole.RESEARCHER, "test-pass-123", True)
        project = Project(title="Race", created_by=user.id)
        session.add(project)
        await session.flush()
        session.add(
            ProjectMember(
                project_id=project.id, user_id=user.id,
                role_in_project=ProjectMemberRole.LEAD,
            )
        )
        article = Article(project_id=project.id, title="Race article", methods_text="Mice")
        search = Search(
            project_id=project.id, name="Blind", kind="blind", question="Q", lot_all=True,
        )
        session.add_all([article, search])
        await session.commit()

    app = create_app()

    async def independent_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = independent_session
    cookie = {"access_token": create_access_token(user)}
    article_url = f"/api/v1/projects/{project.id}/articles/{article.id}"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://app.test", cookies=cookie
    ) as client:
        shown = await client.get(article_url, params={"search": str(search.id)})
        assert shown.status_code == 200, shown.text
        body = {
            "use_ai_behavior": True,
            "animal_sex": "BOTH_SEXES",
            "confidence_score": 0.9,
            "source_view_token": shown.json()["source_view_token"],
        }
        # Hold the article row while both requests reach SELECT FOR UPDATE.
        # Releasing it gives both contenders a concurrent race; the second must
        # observe the committed first review after acquiring the row lock.
        async with factory() as holder:
            await holder.execute(
                select(Article.id).where(Article.id == article.id).with_for_update()
            )
            tasks = [
                asyncio.create_task(
                    client.post(
                        article_url + "/annotation", params={"search": str(search.id)}, json=body
                    )
                )
                for _ in range(2)
            ]
            for _ in range(100):
                async with engine.connect() as observer:
                    waiting = await observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                            "AND query ILIKE '%FOR UPDATE%'"
                        )
                    )
                if waiting >= 2:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("both blind submissions did not reach the article lock")
            await holder.commit()
        first, second = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
        assert sorted((first.status_code, second.status_code)) == [201, 409]
    async with factory() as session:
        rows = (
            await session.execute(
                select(HumanAnnotation).where(
                    HumanAnnotation.article_id == article.id,
                    HumanAnnotation.user_id == user.id,
                    HumanAnnotation.search_id == search.id,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].blind is True
        assert rows[0].source_view_id is not None
