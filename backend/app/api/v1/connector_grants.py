"""Project-lead management of ORAtlas connector grants (Diggypaper #53).

A project lead registers their own project with one recipient. They name the scopes, the
purpose, a TTL of at most 90 days and any metadata to disclose; nothing is granted by default.
The credential is returned once, at creation, and only its SHA-256 is stored. The lead or the
holder can revoke a grant at any time; removing the holder from the project, deleting the
project or the account revokes it too.

The access check is stricter than the rest of the API. It requires real LEAD membership, and a
global ADMIN gets no bypass, because a platform role is not project authority (ORAtlas #202).
A non-member, a guessed project and a grant of another project all get the same 404.
These routes manage grants; the connector endpoints that consume them come later in #53.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, rate_limit_user
from app.connector_grants import (
    create_grant,
    decision_ref,
    disclosed_fields,
    grant_ref,
    is_active,
    revoke_grant,
    scopes_of,
)
from app.db.models import (
    ConnectorAuditEvent,
    ConnectorGrant,
    ProjectMember,
    ProjectMemberRole,
    User,
)
from app.pseudonym import anonymized_actor_ref

router = APIRouter(prefix="/projects", tags=["connector-grants"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]
Scope = Literal[
    "list_project", "list_artifacts", "read_selected_metadata", "obtain_authorized_export"
]
NOT_FOUND = "project not found"


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient_ref: str = Field(pattern=r"^recipient:[a-z0-9][a-z0-9-]{0,62}$")
    scopes: list[Scope] = Field(min_length=1, max_length=4)
    purpose: str = Field(min_length=1, max_length=500)
    ttl_days: int = Field(default=30, ge=1, le=90)
    disclose: list[Literal["title", "description"]] = Field(default_factory=list, max_length=2)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    value = value if value.tzinfo else value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _member(db: AsyncSession, project_id: uuid.UUID, user: User) -> ProjectMember:
    member = await db.get(ProjectMember, (project_id, user.id))
    if member is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return member


async def _require_lead_member(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    if (await _member(db, project_id, user)).role_in_project != ProjectMemberRole.LEAD:
        raise HTTPException(status_code=403, detail="lead required")


async def _grant(db: AsyncSession, project_id: uuid.UUID, grant_id: uuid.UUID) -> ConnectorGrant:
    grant = await db.get(ConnectorGrant, grant_id)
    if grant is None or grant.project_id != project_id:
        raise HTTPException(status_code=404, detail="grant not found")
    return grant


def _payload(grant: ConnectorGrant, user: User, now: datetime) -> dict:
    return {
        "id": str(grant.id),
        "grant_ref": grant_ref(grant),
        "recipient_ref": grant.recipient_ref,
        "purpose": grant.purpose,
        "scopes": scopes_of(grant),
        "disclose": disclosed_fields(grant),
        "decision_ref": decision_ref(grant) if disclosed_fields(grant) else None,
        "held_by_you": grant.holder_id == user.id,
        "created_at": _ts(grant.created_at),
        "expires_at": _ts(grant.expires_at),
        "revoked_at": _ts(grant.revoked_at),
        "revocation_reason": grant.revocation_reason,
        "state": "revoked"
        if grant.revoked_at is not None
        else ("active" if is_active(grant, now) else "expired"),
    }


@router.post(
    "/{project_id}/connector-grants",
    status_code=201,
    dependencies=[Depends(rate_limit_user("connector_grant_create", 20))],
)
async def create(project_id: uuid.UUID, payload: GrantCreate, db: DbDep, user: UserDep) -> dict:
    await _require_lead_member(db, project_id, user)
    if len(set(payload.scopes)) != len(payload.scopes) or len(set(payload.disclose)) != len(
        payload.disclose
    ):
        raise HTTPException(status_code=422, detail="duplicate scope or field")
    now = _now()
    try:
        grant, credential = create_grant(
            db,
            project_id=project_id,
            holder=user,
            actor_ref=anonymized_actor_ref(user.email),
            recipient_ref=payload.recipient_ref,
            scopes=payload.scopes,
            purpose=payload.purpose,
            ttl=timedelta(days=payload.ttl_days),
            disclose=payload.disclose,
            now=now,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await db.commit()
    return {"grant": _payload(grant, user, now), "credential": credential}


@router.get("/{project_id}/connector-grants")
async def list_grants(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_lead_member(db, project_id, user)
    now = _now()
    grants = (
        (
            await db.execute(
                select(ConnectorGrant)
                .where(ConnectorGrant.project_id == project_id)
                .order_by(ConnectorGrant.created_at, ConnectorGrant.id)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_payload(g, user, now) for g in grants]}


@router.post("/{project_id}/connector-grants/{grant_id}/revoke")
async def revoke(project_id: uuid.UUID, grant_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    member = await _member(db, project_id, user)
    grant = await _grant(db, project_id, grant_id)
    if grant.holder_id == user.id:
        reason = "holder-revoked"
    elif member.role_in_project == ProjectMemberRole.LEAD:
        reason = "lead-revoked"
    else:
        raise HTTPException(status_code=404, detail="grant not found")
    now = _now()
    revoke_grant(db, grant, reason=reason, actor_ref=anonymized_actor_ref(user.email), now=now)
    await db.commit()
    return _payload(grant, user, now)


@router.get("/{project_id}/connector-grants/{grant_id}/audit")
async def audit(project_id: uuid.UUID, grant_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_lead_member(db, project_id, user)
    grant = await _grant(db, project_id, grant_id)
    events = (
        (
            await db.execute(
                select(ConnectorAuditEvent)
                .where(ConnectorAuditEvent.grant_id == grant.id)
                .order_by(ConnectorAuditEvent.id)
            )
        )
        .scalars()
        .all()
    )
    # Actor digests are global (salted) correlators: never returned, only whether one exists.
    return {
        "items": [
            {
                "event": e.event,
                "scope": e.scope,
                "reason": e.reason,
                "by_person": e.actor_ref is not None,
                "at": _ts(e.created_at),
            }
            for e in events
        ]
    }
