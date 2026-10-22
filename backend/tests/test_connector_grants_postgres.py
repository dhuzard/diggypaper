"""Opt-in real migration test for 0034 connector grants. Needs SEIP_TEST_POSTGRES_URL.

Each run creates and drops its own UUID-named database (test_provenance_migration fixture).
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_provenance_migration import alembic, postgres_database  # noqa: F401

from app.connector_grants import authorize, create_grant, revoke_grant
from app.db.models import (
    ConnectorAuditEvent,
    ConnectorGrant,
    Project,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("SEIP_TEST_POSTGRES_URL"), reason="requires disposable PostgreSQL admin URL"
)
NOW = datetime.now(UTC).replace(microsecond=0)


async def _refused(session, stmt):
    with pytest.raises(DBAPIError):
        await session.execute(stmt)
    await session.rollback()


async def test_0034_upgrade_guards_and_downgrade(postgres_database):  # noqa: F811
    url, engine = postgres_database
    await alembic(url, "upgrade", "0033")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(email="lead@lab.fr", hashed_password="x", role=UserRole.RESEARCHER)
        project = Project(title="Existing before 0034")
        session.add_all([user, project])
        await session.flush()
        session.add(
            ProjectMember(
                project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
            )
        )
        await session.commit()
        ids = (user.id, project.id)
    await alembic(url, "upgrade", "0034")
    async with factory() as session:
        user, project = await session.get(User, ids[0]), await session.get(Project, ids[1])
        assert project.title == "Existing before 0034"  # additive: existing rows untouched
        grant, credential = create_grant(
            session,
            project_id=project.id,
            holder=user,
            actor_ref="a" * 64,
            recipient_ref="recipient:oratlas",
            scopes=["list_project"],
            purpose="migration test",
            ttl=timedelta(days=30),
            now=NOW,
        )
        await session.commit()
        gid, pid = grant.id, project.id
        assert await authorize(
            session, credential=credential, project_id=pid, scope="list_project", now=NOW
        )
        await session.commit()

        by_id = ConnectorGrant.id == gid
        await _refused(session, update(ConnectorGrant).where(by_id).values(purpose="widened"))
        await _refused(
            session,
            update(ConnectorGrant).where(by_id).values(scope_obtain_authorized_export=True),
        )
        await _refused(session, update(ConnectorGrant).where(by_id).values(revoked_at=NOW))
        await _refused(session, text("UPDATE connector_audit_events SET reason = 'revoked'"))
        await _refused(session, text("DELETE FROM connector_audit_events"))

        grant = await session.get(ConnectorGrant, gid)
        revoke_grant(session, grant, reason="lead-revoked", actor_ref=None, now=NOW)
        await session.commit()
        await _refused(
            session,
            update(ConnectorGrant).where(by_id).values(revoked_at=None, revocation_reason=None),
        )
        await _refused(
            session, update(ConnectorGrant).where(by_id).values(revocation_reason="project-deleted")
        )
        # Account deletion nulls the holder FK through the guard; the revocation stays.
        await session.execute(delete(User).where(User.id == ids[0]))
        await session.commit()
        session.expire_all()
        grant = await session.get(ConnectorGrant, gid)
        assert grant.holder_id is None and grant.revocation_reason == "lead-revoked"

        await session.execute(delete(Project).where(Project.id == pid))
        await session.commit()
        session.expire_all()
        assert await session.get(ConnectorGrant, gid) is None
        events = (
            (
                await session.execute(
                    select(ConnectorAuditEvent.event)
                    .where(ConnectorAuditEvent.grant_id == gid)
                    .order_by(ConnectorAuditEvent.id)
                )
            )
            .scalars()
            .all()
        )
        assert events == ["grant-created", "access-granted", "grant-revoked"]

    # The audit trail is never discarded silently, so downgrade refuses while it has rows.
    await alembic(url, "downgrade", "0033", success=False)
    async with engine.begin() as connection:
        # An operator's explicit, deliberate purge: the guard is disabled for it, then restored.
        trigger = "connector_audit_events_append_only"
        await connection.exec_driver_sql(
            f"ALTER TABLE connector_audit_events DISABLE TRIGGER {trigger}"
        )
        await connection.exec_driver_sql("DELETE FROM connector_audit_events")
        await connection.exec_driver_sql(
            f"ALTER TABLE connector_audit_events ENABLE TRIGGER {trigger}"
        )
    await alembic(url, "downgrade", "0033")
    async with engine.connect() as connection:
        tables = (
            await connection.execute(
                text(
                    "SELECT to_regclass('connector_grants'), to_regclass('connector_audit_events')"
                )
            )
        ).one()
    assert tables == (None, None)
    await alembic(url, "upgrade", "0034")
