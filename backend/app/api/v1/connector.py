"""Read-only ORAtlas connector endpoints, authenticated by a connector grant (Diggypaper #53).

The only accepted credential is ``Authorization: Bearer <grant credential>``. A Diggypaper
session cookie is ignored here, and so is any ORCID or name. Every request goes through
app.connector_grants.authorize, which re-reads the grant, the holder's membership and account
state, and is audited. A missing, malformed, unknown, revoked or expired credential gets the
same 404 and body as an existing project it does not cover, a guessed project id or a missing
scope, so a caller learns nothing about any other project.

Responses are ``Cache-Control: no-store``: revocation acts on the next request, and nothing
here asks a client or proxy to keep a copy. The descriptor is returned as its exact canonical
bytes, so its digest is the SHA-256 of the response body.

Under the ``list_artifacts`` scope a descriptor lists the project's restricted
source-evidence packet, built by the same code as the LEAD export, so its digest names the
packet that export returns at that moment. A public packet stays an explicit LEAD export
decision and is never listed through a grant.

Under ``obtain_authorized_export`` the holder fetches the exact packet a descriptor listed, by
its digest. The packet is rebuilt at request time with the same restricted settings; if the
project changed since the listing, the digest no longer matches and nothing is returned, so a
caller can never obtain bytes other than the ones the digest names.
"""

import os
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.v1.findings import _zip_bytes, packet_input
from app.connector_grants import authorize, decision_ref, disclosed_fields, grant_ref
from app.db.models import Project, ProjectMemberRole
from app.finding_packet import build_packet
from app.project_descriptor import MAX_TTL, build_project_descriptor, packet_artifact

router = APIRouter(prefix="/connector", tags=["connector"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}
NOT_FOUND = "project not found"
_BEARER = re.compile(r"Bearer ([A-Za-z0-9_-]{1,200})")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _refuse() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND, headers=NO_STORE)


def _producer_commit() -> str:
    commit = os.environ.get("GIT_COMMIT", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        # A descriptor or packet must name the producer code that issued it.
        raise HTTPException(
            status_code=409,
            detail="connector requires the server's GIT_COMMIT (40 hex)",
            headers=NO_STORE,
        )
    return commit


@router.get("/projects/{project_id}/descriptor")
async def descriptor(project_id: uuid.UUID, request: Request, db: DbDep) -> Response:
    match = _BEARER.fullmatch(request.headers.get("authorization", ""))
    now = datetime.now(UTC).replace(microsecond=0)
    auth = None
    if match:
        auth = await authorize(
            db, credential=match.group(1), project_id=project_id, scope="list_project", now=now
        )
        await db.commit()  # the audit event is kept whatever the outcome
    if auth is None:
        raise _refuse()
    project = await db.get(Project, project_id)
    if project is None:  # deleted between the grant check and here: same refusal
        raise _refuse()
    commit = _producer_commit()
    grant = auth.grant
    artifacts = []
    if grant.scope_list_artifacts:
        # Audited as its own access; re-checked like any other scope.
        listed = await authorize(
            db, credential=match.group(1), project_id=project_id, scope="list_artifacts", now=now
        )
        await db.commit()
        if listed is None:  # revoked or membership ended since the first check
            raise _refuse()
        packet = build_packet(await packet_input(db, project_id, producer_commit=commit))
        if packet.report["exported"]:  # an empty export state is not an artifact
            artifacts.append(packet_artifact(project.id, packet.files))
    expires = grant.expires_at if grant.expires_at.tzinfo else grant.expires_at.replace(tzinfo=UTC)
    disclosed = {}
    if grant.scope_read_selected_metadata:
        for name in disclosed_fields(grant):
            value = getattr(project, name)
            if value and value.strip():  # an empty description stays withheld
                disclosed[name] = (value, decision_ref(grant))
    try:
        body = build_project_descriptor(
            project_id=project.id,
            holder_role=auth.holder_role,
            issued_at=now,
            # A descriptor never outlives the grant it was issued under.
            expires_at=min(now + MAX_TTL, expires),
            recipient_ref=grant.recipient_ref,
            producer_commit=commit,
            grant_ref=grant_ref(grant),
            disclosed=disclosed,
            artifacts=artifacts,
        )
    except ValueError as exc:
        # e.g. a disclosed title carrying an email: nothing is issued, the lead must fix it.
        raise HTTPException(status_code=409, detail=str(exc), headers=NO_STORE) from None
    return Response(content=body, media_type="application/json", headers=NO_STORE)


@router.get("/projects/{project_id}/packets/{digest}")
async def packet(project_id: uuid.UUID, digest: str, request: Request, db: DbDep) -> Response:
    match = _BEARER.fullmatch(request.headers.get("authorization", ""))
    now = datetime.now(UTC).replace(microsecond=0)
    auth = None
    if match:
        auth = await authorize(
            db,
            credential=match.group(1),
            project_id=project_id,
            scope="obtain_authorized_export",
            now=now,
        )
        await db.commit()  # the audit event is kept whatever the outcome
    # Holders are leads, and membership only ends by removal, which revokes the grant; the
    # role check guards the export should a role ever change in place.
    if auth is None or auth.holder_role != ProjectMemberRole.LEAD:
        raise _refuse()
    if not _DIGEST.fullmatch(digest) or await db.get(Project, project_id) is None:
        raise _refuse()
    commit = _producer_commit()
    built = build_packet(await packet_input(db, project_id, producer_commit=commit))
    current = packet_artifact(project_id, built.files)["digest"]
    if not built.report["exported"] or current != digest:
        # Not (or no longer) the packet a descriptor listed: fetch a new descriptor.
        raise HTTPException(status_code=409, detail="stale-digest", headers=NO_STORE)
    return Response(
        content=_zip_bytes(dict(built.files)),
        media_type="application/zip",
        headers={
            **NO_STORE,
            "Content-Disposition": 'attachment; filename="source-evidence-packet.zip"',
        },
    )
