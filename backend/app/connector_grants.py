"""LEAD-controlled, expiring, revocable ORAtlas connector grants (Diggypaper #53).

A grant lets one named recipient act for one project member (the holder) with explicit scopes.
It is the only thing that can authorize a connector. A Diggypaper session, an ORCID or a name
match never does, and neither does a descriptor (app.project_descriptor, access_effect none).

``authorize`` is the single check for every connector request. It re-reads the grant, the
holder's current membership and account state on every call, and returns ``None`` for every
failure. The caller answers all of them identically; the reason only goes to the audit log.
Revocation is one-way and takes effect on the next call: nothing caches an authorization.

Every function here only stages changes; committing is the caller's job.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connector_policy import (
    CREDENTIAL_PREFIX,
    DISCLOSABLE_FIELDS,
    GRANT_SCOPES,
    MAX_GRANT_TTL,
    REVOCATION_REASONS,
)
from app.db.models import (
    ConnectorAuditEvent,
    ConnectorGrant,
    ProjectMember,
    ProjectMemberRole,
    User,
)
from app.project_descriptor import _RECIPIENT


@dataclass(frozen=True)
class AuthorizedGrant:
    grant: ConnectorGrant
    holder_role: ProjectMemberRole


def credential_digest(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def grant_ref(grant: ConnectorGrant) -> str:
    return f"grant:{grant.id.hex}"


def decision_ref(grant: ConnectorGrant) -> str:
    """The lead's disclosure decision is made, and revoked, with the grant itself."""
    return f"decision:{grant.id.hex}"


def scopes_of(grant: ConnectorGrant) -> list[str]:
    return [s for s in GRANT_SCOPES if getattr(grant, f"scope_{s}")]


def disclosed_fields(grant: ConnectorGrant) -> list[str]:
    return [f for f in DISCLOSABLE_FIELDS if getattr(grant, f"disclose_{f}")]


def is_active(grant: ConnectorGrant, now: datetime) -> bool:
    return grant.revoked_at is None and now < _aware(grant.expires_at)


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; every stored timestamp is UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _audit(
    db: AsyncSession,
    event: str,
    now: datetime,
    *,
    grant: ConnectorGrant | None = None,
    project_id: uuid.UUID | None = None,
    scope: str | None = None,
    reason: str | None = None,
    actor_ref: str | None = None,
) -> None:
    db.add(
        ConnectorAuditEvent(
            grant_id=grant.id if grant else None,
            project_id=grant.project_id if grant else project_id,
            event=event,
            scope=scope,
            reason=reason,
            actor_ref=actor_ref,
            created_at=now,
        )
    )


def create_grant(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    holder: User,
    actor_ref: str,
    recipient_ref: str,
    scopes: Iterable[str],
    purpose: str,
    ttl: timedelta,
    disclose: Iterable[str] = (),
    now: datetime,
) -> tuple[ConnectorGrant, str]:
    """Stage a grant; returns it with the bearer credential, which is never stored or shown again.

    The caller has already checked that ``holder`` is a lead of ``project_id``.
    """
    scopes, disclose = set(scopes), set(disclose)
    if not scopes or not scopes <= set(GRANT_SCOPES):
        raise ValueError("a grant names one or more known scopes")
    if not disclose <= set(DISCLOSABLE_FIELDS):
        raise ValueError("only the title and description are disclosable")
    if disclose and "read_selected_metadata" not in scopes:
        raise ValueError("disclosing metadata needs the read_selected_metadata scope")
    if not _RECIPIENT.match(recipient_ref):
        raise ValueError("invalid recipient_ref")
    if not purpose.strip():
        raise ValueError("a grant needs a stated purpose")
    if not timedelta(0) < ttl <= MAX_GRANT_TTL:
        raise ValueError("a grant lasts more than 0 and at most 90 days")
    credential = CREDENTIAL_PREFIX + secrets.token_urlsafe(32)
    now = now.replace(microsecond=0)
    grant = ConnectorGrant(
        id=uuid.uuid4(),
        project_id=project_id,
        holder_id=holder.id,
        created_by_ref=actor_ref,
        recipient_ref=recipient_ref,
        purpose=purpose.strip(),
        credential_sha256=credential_digest(credential),
        created_at=now,
        expires_at=now + ttl,
        **{f"scope_{s}": s in scopes for s in GRANT_SCOPES},
        **{f"disclose_{f}": f in disclose for f in DISCLOSABLE_FIELDS},
    )
    db.add(grant)
    _audit(db, "grant-created", now, grant=grant, actor_ref=actor_ref)
    return grant, credential


def revoke_grant(
    db: AsyncSession, grant: ConnectorGrant, *, reason: str, actor_ref: str | None, now: datetime
) -> bool:
    """One-way revocation; False (and nothing recorded) if it was already revoked."""
    if reason not in REVOCATION_REASONS:
        raise ValueError(f"unknown revocation reason {reason!r}")
    if grant.revoked_at is not None:
        return False
    grant.revoked_at = now
    grant.revoked_by_ref = actor_ref
    grant.revocation_reason = reason
    _audit(db, "grant-revoked", now, grant=grant, reason=reason, actor_ref=actor_ref)
    return True


async def revoke_where(
    db: AsyncSession,
    *,
    reason: str,
    now: datetime,
    project_ids: Iterable[uuid.UUID] | None = None,
    holder_id: uuid.UUID | None = None,
    actor_ref: str | None = None,
) -> int:
    """Revoke every live grant of these projects and/or this holder (membership/account end)."""
    q = select(ConnectorGrant).where(ConnectorGrant.revoked_at.is_(None))
    if project_ids is not None:
        q = q.where(ConnectorGrant.project_id.in_(list(project_ids)))
    if holder_id is not None:
        q = q.where(ConnectorGrant.holder_id == holder_id)
    if project_ids is None and holder_id is None:
        raise ValueError("revoke_where needs a project or a holder")
    grants = (await db.execute(q)).scalars().all()
    for grant in grants:
        revoke_grant(db, grant, reason=reason, actor_ref=actor_ref, now=now)
    return len(grants)


async def authorize(
    db: AsyncSession, *, credential: str, project_id: uuid.UUID, scope: str, now: datetime
) -> AuthorizedGrant | None:
    """The live grant for this credential, project and scope, or None. Always audited."""
    if scope not in GRANT_SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    grant = (
        await db.execute(
            select(ConnectorGrant).where(
                ConnectorGrant.credential_sha256 == credential_digest(credential)
            )
        )
    ).scalar_one_or_none()
    reason = None
    member = holder = None
    if grant is None:
        reason = "unknown-credential"
    elif grant.revoked_at is not None:
        reason = "revoked"
    elif now >= _aware(grant.expires_at):
        reason = "expired"
    elif grant.project_id != project_id:
        reason = "project-mismatch"
    elif not getattr(grant, f"scope_{scope}"):
        reason = "scope-not-granted"
    else:
        holder = await db.get(User, grant.holder_id) if grant.holder_id else None
        member = await db.get(ProjectMember, (project_id, grant.holder_id)) if holder else None
        if holder is None or member is None:
            reason = "holder-not-member"
        elif holder.is_active is False or holder.anonymized_hash is not None:
            reason = "holder-inactive"
    if reason is not None:
        _audit(
            db,
            "access-refused",
            now,
            grant=grant,
            project_id=project_id,
            scope=scope,
            reason=reason,
        )
        return None
    assert grant is not None and member is not None
    _audit(db, "access-granted", now, grant=grant, scope=scope)
    return AuthorizedGrant(grant, member.role_in_project or ProjectMemberRole.ANNOTATOR)
