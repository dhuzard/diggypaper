"""Suppression de projets (lignes en cascade FK + zips d'export) et de comptes.

Règle : un compte invité (ni ADMIN ni créateur de projets) sans aucun projet n'a plus de
raison d'exister (il consommerait des ressources sans rien faire) ; il est supprimé dès qu'il
perd sa dernière appartenance. Les créateurs et les admins ne sont jamais supprimés ainsi.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connector_grants import revoke_where
from app.db.models import (
    EvidenceSupportAssessment,
    ExportJob,
    HumanAnnotation,
    Invitation,
    Project,
    ProjectMember,
    User,
    UserRole,
)
from app.pseudonym import anonymized_actor_ref


async def delete_user(db: AsyncSession, user: User) -> None:
    """Supprime le compte ; ses annotations restent, anonymisées (hash). Commit à l'appelant."""
    anon = anonymized_actor_ref(user.email)
    await revoke_where(db, reason="account-deleted", now=datetime.now(UTC), holder_id=user.id)
    await db.execute(
        update(HumanAnnotation)
        .where(HumanAnnotation.user_id == user.id)
        .values(user_id=None, annotator_hash=anon)
    )
    await db.execute(
        update(EvidenceSupportAssessment)
        .where(EvidenceSupportAssessment.actor_id == user.id)
        .values(actor_id=None)
    )
    await db.execute(delete(Invitation).where(func.lower(Invitation.email) == user.email.lower()))
    await db.delete(user)


async def purge_orphans(
    db: AsyncSession, user_ids: list[uuid.UUID] | None = None, keep: tuple = ()
) -> int:
    """Supprime les comptes invités sans projet (parmi ``user_ids``, ou tous). Commit appelant."""
    q = select(User).where(
        User.role != UserRole.ADMIN,
        User.can_create_projects.is_not(True),
        User.id.not_in(select(ProjectMember.user_id)),
    )
    if user_ids is not None:
        q = q.where(User.id.in_(user_ids))
    if keep:
        q = q.where(User.id.not_in(keep))
    users = (await db.execute(q)).scalars().all()
    for u in users:
        await delete_user(db, u)
    return len(users)


async def delete_projects(db: AsyncSession, project_ids: list, keep: tuple = ()) -> int:
    """Supprime ces projets : les FK ON DELETE CASCADE vident articles, PDF, jobs, partages…

    Les zips d'export (hors base) sont effacés ici, et les membres restés sans projet sont
    supprimés (sauf ``keep``). Commit à la charge de l'appelant.
    """
    if not project_ids:
        return 0
    members = (
        (
            await db.execute(
                select(ProjectMember.user_id).where(ProjectMember.project_id.in_(project_ids))
            )
        )
        .scalars()
        .all()
    )
    names = (
        (
            await db.execute(
                select(ExportJob.filename).where(
                    ExportJob.project_id.in_(project_ids), ExportJob.filename.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    directory = Path(os.environ.get("EXPORT_DIR", "/app/exports"))
    for name in names:
        (directory / name).unlink(missing_ok=True)
    # The grants go with the project (FK cascade); their audit trail records why and stays.
    await revoke_where(
        db, reason="project-deleted", now=datetime.now(UTC), project_ids=list(project_ids)
    )
    await db.flush()
    res = await db.execute(delete(Project).where(Project.id.in_(project_ids)))
    await purge_orphans(db, list(members), keep)
    return res.rowcount or 0
