"""ORAtlas connector grants (#53): LEAD control, revocation, audit and access negatives."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.connector_grants import authorize, create_grant, credential_digest, revoke_grant
from app.core.security import hash_password
from app.db.models import (
    ConnectorAuditEvent,
    ConnectorGrant,
    Project,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)
from app.pseudonym import anonymized_actor_ref

PASSWORD = "member-pass-123"
LEAD = ("researcher@lab.fr", "researcher-pass-123")
ANNOTATOR = ("annot@lab.fr", PASSWORD)
OUTSIDER = ("outsider@lab.fr", PASSWORD)
LEAD_B = ("leadb@lab.fr", PASSWORD)
BOTH = ("both@lab.fr", PASSWORD)
ADMIN = ("admin@lab.fr", "admin-pass-123")
BODY = {
    "recipient_ref": "recipient:oratlas",
    "scopes": ["list_project", "list_artifacts"],
    "purpose": "Link this project to my private ORAtlas workspace",
}
NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


async def _user(session, email, role=UserRole.RESEARCHER):
    user = User(
        email=email,
        hashed_password=hash_password(PASSWORD),
        role=role,
        data_consent_given=True,
        can_create_projects=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _world(session, researcher_user, admin_user):
    """Project A (lead, annotator, both) and B (lead_b, both); outsider and admin in neither."""
    researcher_user.data_consent_given = True
    users = {"lead": researcher_user, "admin": admin_user}
    for key, (email, _) in (
        ("annotator", ANNOTATOR),
        ("outsider", OUTSIDER),
        ("lead_b", LEAD_B),
        ("both", BOTH),
    ):
        users[key] = await _user(session, email)
    a = Project(title="Confidential topic A", created_by=researcher_user.id)
    b = Project(title="Confidential topic B", created_by=users["lead_b"].id)
    session.add_all([a, b])
    await session.flush()
    for project, key, role in (
        (a, "lead", ProjectMemberRole.LEAD),
        (a, "annotator", ProjectMemberRole.ANNOTATOR),
        (a, "both", ProjectMemberRole.LEAD),
        (b, "lead_b", ProjectMemberRole.LEAD),
        (b, "both", ProjectMemberRole.LEAD),
    ):
        session.add(
            ProjectMember(project_id=project.id, user_id=users[key].id, role_in_project=role)
        )
    await session.commit()
    return a, b, users


def _url(project_id, *rest):
    return "/".join([f"/api/v1/projects/{project_id}/connector-grants", *rest])


async def _create(client, do_login, project_id, who=LEAD, **body):
    assert (await do_login(*who)).status_code == 200
    return await client.post(_url(project_id), json={**BODY, **body})


async def _events(session, grant_id):
    rows = await session.execute(
        select(ConnectorAuditEvent)
        .where(ConnectorAuditEvent.grant_id == grant_id)
        .order_by(ConnectorAuditEvent.id)
    )
    return [(e.event, e.reason) for e in rows.scalars()]


def _stage(session, project, holder, **kw):
    args = {
        "project_id": project.id,
        "holder": holder,
        "actor_ref": anonymized_actor_ref(holder.email),
        "recipient_ref": "recipient:oratlas",
        "scopes": ["list_project"],
        "purpose": "test",
        "ttl": timedelta(days=30),
        "now": NOW,
    }
    args.update(kw)
    return create_grant(session, **args)


# --- management API --------------------------------------------------------------------------


async def test_lead_creates_grant_credential_shown_once(
    client, do_login, session, researcher_user, admin_user
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    res = await _create(client, do_login, a.id, disclose=[], ttl_days=7)
    assert res.status_code == 201, res.text
    body = res.json()
    credential = body["credential"]
    assert credential.startswith("dpg_") and len(credential) > 40
    grant = body["grant"]
    assert grant["scopes"] == ["list_project", "list_artifacts"]
    assert grant["state"] == "active" and grant["held_by_you"] is True
    assert grant["disclose"] == [] and grant["decision_ref"] is None
    row = await session.get(ConnectorGrant, uuid.UUID(grant["id"]))
    assert row.credential_sha256 == hashlib.sha256(credential.encode()).hexdigest()
    listing = await client.get(_url(a.id))
    assert listing.status_code == 200
    assert credential not in listing.text and row.credential_sha256 not in listing.text
    assert [g["id"] for g in listing.json()["items"]] == [grant["id"]]


async def test_disclosure_requires_metadata_scope(
    client, do_login, session, researcher_user, admin_user
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    assert (await _create(client, do_login, a.id, disclose=["title"])).status_code == 422
    res = await _create(
        client,
        do_login,
        a.id,
        scopes=["list_project", "read_selected_metadata"],
        disclose=["title"],
    )
    assert res.status_code == 201
    grant = res.json()["grant"]
    assert grant["disclose"] == ["title"]
    assert grant["decision_ref"] == "decision:" + uuid.UUID(grant["id"]).hex


@pytest.mark.parametrize(
    "body",
    [
        {"ttl_days": 91},
        {"ttl_days": 0},
        {"scopes": []},
        {"scopes": ["list_project", "list_project"]},
        {"scopes": ["read_everything"]},
        {"recipient_ref": "https://oratlas.example"},
        {"purpose": "   "},
        {"disclose": ["members"]},
        {"credential": "dpg_chosen"},
    ],
)
async def test_invalid_grant_requests(client, do_login, session, researcher_user, admin_user, body):
    a, _, _ = await _world(session, researcher_user, admin_user)
    res = await _create(client, do_login, a.id, **body)
    assert res.status_code == 422
    assert (await session.execute(select(ConnectorGrant))).scalars().all() == []


async def test_only_a_real_lead_member_manages_grants(
    client, do_login, session, researcher_user, admin_user
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    assert (await _create(client, do_login, a.id, who=ANNOTATOR)).status_code == 403
    # Outsider, platform admin (no membership), a lead of another project and a guessed id
    # all get the same 404: nothing reveals whether the project exists.
    responses = [
        await _create(client, do_login, a.id, who=OUTSIDER),
        await _create(client, do_login, a.id, who=ADMIN),
        await _create(client, do_login, a.id, who=LEAD_B),
        await _create(client, do_login, uuid.uuid4(), who=LEAD),
    ]
    assert {r.status_code for r in responses} == {404}
    assert {r.text for r in responses} == {responses[0].text}
    for who in (OUTSIDER, ADMIN, LEAD_B):
        await do_login(*who)
        assert (await client.get(_url(a.id))).status_code == 404
    assert (await session.execute(select(ConnectorGrant))).scalars().all() == []


async def test_no_cross_project_grant_access(
    client, do_login, session, researcher_user, admin_user
):
    a, b, _ = await _world(session, researcher_user, admin_user)
    grant_a = (await _create(client, do_login, a.id)).json()["grant"]["id"]
    grant_b = (await _create(client, do_login, b.id, who=LEAD_B)).json()["grant"]["id"]
    # lead_b leads B only: A's grant is not reachable through B's path, nor A's own path.
    for path in (
        _url(b.id, grant_a, "audit"),
        _url(a.id, grant_a, "audit"),
    ):
        assert (await client.get(path)).status_code == 404
    assert (await client.post(_url(b.id, grant_a, "revoke"))).status_code == 404
    assert [g["id"] for g in (await client.get(_url(b.id))).json()["items"]] == [grant_b]
    # A member of both projects sees each project's own grants only.
    await do_login(*BOTH)
    assert [g["id"] for g in (await client.get(_url(a.id))).json()["items"]] == [grant_a]
    assert [g["id"] for g in (await client.get(_url(b.id))).json()["items"]] == [grant_b]
    assert (await client.post(_url(a.id, grant_b, "revoke"))).status_code == 404
    assert (await session.get(ConnectorGrant, uuid.UUID(grant_b))).revoked_at is None


async def test_revocation_by_lead_and_holder(
    client, do_login, session, researcher_user, admin_user
):
    a, _, users = await _world(session, researcher_user, admin_user)
    first = (await _create(client, do_login, a.id)).json()["grant"]["id"]
    second = (await _create(client, do_login, a.id, who=BOTH)).json()["grant"]["id"]
    await do_login(*ANNOTATOR)
    assert (await client.post(_url(a.id, first, "revoke"))).status_code == 404
    await do_login(*LEAD)
    res = await client.post(_url(a.id, first, "revoke"))
    assert res.json()["state"] == "revoked"
    assert res.json()["revocation_reason"] == "holder-revoked"
    res = await client.post(_url(a.id, second, "revoke"))
    assert res.json()["revocation_reason"] == "lead-revoked"
    again = await client.post(_url(a.id, second, "revoke"))
    assert again.status_code == 200 and again.json()["revocation_reason"] == "lead-revoked"
    assert await _events(session, uuid.UUID(second)) == [
        ("grant-created", None),
        ("grant-revoked", "lead-revoked"),
    ]


async def test_audit_endpoint_hides_actor_digests(
    client, do_login, session, researcher_user, admin_user
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    grant_id = (await _create(client, do_login, a.id)).json()["grant"]["id"]
    await client.post(_url(a.id, grant_id, "revoke"))
    res = await client.get(_url(a.id, grant_id, "audit"))
    assert [(e["event"], e["by_person"]) for e in res.json()["items"]] == [
        ("grant-created", True),
        ("grant-revoked", True),
    ]
    assert anonymized_actor_ref(LEAD[0]) not in res.text
    await do_login(*ANNOTATOR)
    assert (await client.get(_url(a.id, grant_id, "audit"))).status_code == 403


async def test_member_removal_revokes_grants(
    client, do_login, session, researcher_user, admin_user
):
    a, b, users = await _world(session, researcher_user, admin_user)
    a_id, b_id, both_id = a.id, b.id, users["both"].id
    res_a = await _create(client, do_login, a_id, who=BOTH)
    res_b = await _create(client, do_login, b_id, who=BOTH)
    await do_login(*LEAD)
    assert (await client.delete(f"/api/v1/projects/{a_id}/members/{both_id}")).status_code == 204
    session.expire_all()
    grant_a = await session.get(ConnectorGrant, uuid.UUID(res_a.json()["grant"]["id"]))
    grant_b = await session.get(ConnectorGrant, uuid.UUID(res_b.json()["grant"]["id"]))
    assert grant_a.revocation_reason == "membership-removed"
    assert grant_b.revoked_at is None  # membership of B is untouched
    old = res_a.json()["credential"]
    assert (
        await authorize(session, credential=old, project_id=a_id, scope="list_project", now=NOW)
        is None
    )
    assert await authorize(
        session,
        credential=res_b.json()["credential"],
        project_id=b_id,
        scope="list_project",
        now=NOW,
    )


async def test_project_deletion_revokes_and_keeps_audit(
    client, do_login, session, researcher_user, admin_user
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    res = await _create(client, do_login, a.id)
    grant_id = uuid.UUID(res.json()["grant"]["id"])
    assert (await client.delete(f"/api/v1/projects/{a.id}")).status_code == 204
    session.expire_all()
    assert await session.get(ConnectorGrant, grant_id) is None
    assert await _events(session, grant_id) == [
        ("grant-created", None),
        ("grant-revoked", "project-deleted"),
    ]
    assert (
        await authorize(
            session,
            credential=res.json()["credential"],
            project_id=a.id,
            scope="list_project",
            now=NOW,
        )
        is None
    )


async def test_account_anonymization_revokes_grants(
    client, do_login, session, researcher_user, admin_user
):
    a, _, users = await _world(session, researcher_user, admin_user)
    res = await _create(client, do_login, a.id, who=BOTH)
    assert (await client.delete("/api/v1/users/me/anonymize")).status_code == 204
    session.expire_all()
    grant = await session.get(ConnectorGrant, uuid.UUID(res.json()["grant"]["id"]))
    assert grant.revocation_reason == "account-anonymized"


# --- authorize --------------------------------------------------------------------------------


async def test_authorize_live_grant(session, researcher_user, admin_user):
    a, _, users = await _world(session, researcher_user, admin_user)
    grant, credential = _stage(session, a, users["lead"], scopes=["list_project"])
    await session.commit()
    ok = await authorize(
        session, credential=credential, project_id=a.id, scope="list_project", now=NOW
    )
    assert ok is not None and ok.grant.id == grant.id
    assert ok.holder_role == ProjectMemberRole.LEAD
    # A bearer credential is replayable only while its grant is live.
    again = await authorize(
        session, credential=credential, project_id=a.id, scope="list_project", now=NOW
    )
    assert again is not None
    revoke_grant(session, grant, reason="lead-revoked", actor_ref=None, now=NOW)
    await session.commit()
    assert (
        await authorize(
            session, credential=credential, project_id=a.id, scope="list_project", now=NOW
        )
        is None
    )
    assert await _events(session, grant.id) == [
        ("grant-created", None),
        ("access-granted", None),
        ("access-granted", None),
        ("grant-revoked", "lead-revoked"),
        ("access-refused", "revoked"),
    ]


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("expired", "expired"),
        ("other-project", "project-mismatch"),
        ("guessed-project", "project-mismatch"),
        ("scope", "scope-not-granted"),
        ("membership-row-gone", "holder-not-member"),
        ("disabled", "holder-inactive"),
        ("anonymized", "holder-inactive"),
    ],
)
async def test_authorize_refusals(session, researcher_user, admin_user, case, reason):
    a, b, users = await _world(session, researcher_user, admin_user)
    holder = users["both"]
    grant, credential = _stage(session, a, holder, scopes=["list_project"])
    await session.commit()
    project_id, scope, now = a.id, "list_project", NOW
    if case == "expired":
        now = NOW + timedelta(days=30)
    elif case == "other-project":
        project_id = b.id  # the holder is a lead of B too: the grant still names A only
    elif case == "guessed-project":
        project_id = uuid.uuid4()
    elif case == "scope":
        scope = "obtain_authorized_export"
    elif case == "membership-row-gone":
        await session.execute(
            delete(ProjectMember).where(
                ProjectMember.project_id == a.id, ProjectMember.user_id == holder.id
            )
        )
    elif case == "disabled":
        holder.is_active = False
    elif case == "anonymized":
        holder.anonymized_hash = "f" * 64
    await session.commit()
    assert (
        await authorize(session, credential=credential, project_id=project_id, scope=scope, now=now)
        is None
    )
    assert (await _events(session, grant.id))[-1] == ("access-refused", reason)


async def test_authorize_unknown_or_forged_credential(session, researcher_user, admin_user):
    a, _, users = await _world(session, researcher_user, admin_user)
    grant, credential = _stage(session, a, users["lead"])
    await session.commit()
    for forged in ("", "dpg_" + "A" * 43, credential + "x", grant.credential_sha256):
        assert (
            await authorize(
                session, credential=forged, project_id=a.id, scope="list_project", now=NOW
            )
            is None
        )
    refused = (
        (
            await session.execute(
                select(ConnectorAuditEvent).where(
                    ConnectorAuditEvent.reason == "unknown-credential"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(refused) == 4 and {e.grant_id for e in refused} == {None}
    assert credential_digest(credential) == grant.credential_sha256


# --- database guards --------------------------------------------------------------------------


async def test_grant_changes_only_by_revocation(session, researcher_user, admin_user):
    a, _, users = await _world(session, researcher_user, admin_user)
    grant, _ = _stage(session, a, users["lead"])
    await session.commit()
    grant_id = grant.id
    for values in (
        {"purpose": "widened"},
        {"scope_obtain_authorized_export": True},
        {"expires_at": NOW + timedelta(days=365)},
        {"credential_sha256": "0" * 64},
        {"project_id": uuid.uuid4()},
    ):
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.execute(
                update(ConnectorGrant).where(ConnectorGrant.id == grant_id).values(**values)
            )
        await session.rollback()
    await session.execute(
        update(ConnectorGrant)
        .where(ConnectorGrant.id == grant_id)
        .values(revoked_at=NOW, revocation_reason="lead-revoked")
    )
    await session.commit()
    with pytest.raises((IntegrityError, DBAPIError)):  # no un-revoking
        await session.execute(
            update(ConnectorGrant)
            .where(ConnectorGrant.id == grant_id)
            .values(revoked_at=None, revocation_reason=None)
        )
    await session.rollback()
    await session.execute(
        update(ConnectorGrant).where(ConnectorGrant.id == grant_id).values(holder_id=None)
    )
    await session.commit()


async def test_grant_checks_reject_inconsistent_rows(session, researcher_user, admin_user):
    a, _, users = await _world(session, researcher_user, admin_user)
    # Detached stand-ins: a rollback expires the ORM objects.
    a = SimpleNamespace(id=a.id)
    lead = SimpleNamespace(id=users["lead"].id, email=users["lead"].email)
    for change in (
        {"disclose_title": True},  # disclosure without read_selected_metadata
        {"scope_list_project": False},  # no scope at all
        {"expires_at": NOW},  # not after created_at
        {"recipient_ref": "oratlas"},
        {"revoked_at": NOW},  # revoked without a reason
    ):
        grant, _ = _stage(session, a, lead)
        for key, value in change.items():
            setattr(grant, key, value)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_audit_events_are_append_only(session, researcher_user, admin_user):
    a, _, users = await _world(session, researcher_user, admin_user)
    _stage(session, a, users["lead"])
    await session.commit()
    for stmt in (
        text("UPDATE connector_audit_events SET reason = 'revoked'"),
        text("DELETE FROM connector_audit_events"),
    ):
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.execute(stmt)
        await session.rollback()
    assert len((await session.execute(select(ConnectorAuditEvent))).scalars().all()) == 1


async def test_service_rejects_bad_grants(session, researcher_user, admin_user):
    a, _, users = await _world(session, researcher_user, admin_user)
    for kw in (
        {"scopes": []},
        {"scopes": ["admin"]},
        {"disclose": ["title"]},
        {"scopes": ["read_selected_metadata"], "disclose": ["members"]},
        {"ttl": timedelta(days=91)},
        {"ttl": timedelta(0)},
        {"recipient_ref": "oratlas"},
        {"purpose": " "},
    ):
        with pytest.raises(ValueError):
            _stage(session, a, users["lead"], **kw)
    with pytest.raises(ValueError):
        revoke_grant(session, ConnectorGrant(), reason="because", actor_ref=None, now=NOW)
