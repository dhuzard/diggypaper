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
"""

import os
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.connector_grants import authorize, decision_ref, disclosed_fields, grant_ref
from app.db.models import Project
from app.project_descriptor import MAX_TTL, build_project_descriptor

router = APIRouter(prefix="/connector", tags=["connector"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
NO_STORE = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}
NOT_FOUND = "project not found"
_BEARER = re.compile(r"Bearer ([A-Za-z0-9_-]{1,200})")


def _refuse() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND, headers=NO_STORE)


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
    commit = os.environ.get("GIT_COMMIT", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        # A descriptor must name the producer code that issued it, as a packet does.
        raise HTTPException(
            status_code=409,
            detail="descriptor issue requires the server's GIT_COMMIT (40 hex)",
            headers=NO_STORE,
        )
    grant = auth.grant
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
            # Artifact references (packet digests, list_artifacts) are the next #53 slice.
            artifacts=[],
        )
    except ValueError as exc:
        # e.g. a disclosed title carrying an email: nothing is issued, the lead must fix it.
        raise HTTPException(status_code=409, detail=str(exc), headers=NO_STORE) from None
    return Response(content=body, media_type="application/json", headers=NO_STORE)
