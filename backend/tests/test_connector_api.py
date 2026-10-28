"""Connector descriptor endpoint (#53): bearer grant only, uniform refusal, live revocation."""

import hashlib
import importlib.util
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from test_connector_grants import ANNOTATOR, BOTH, LEAD, LEAD_B, _create, _world
from test_findings_api import _content, _prediction, _rev_url, _review, _unzip

from app.connector_grants import create_grant
from app.db.models import Article, ConnectorAuditEvent, ConnectorGrant
from app.pseudonym import anonymized_actor_ref

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "docs/contracts/research-project-descriptor/v0.1.0/validator/rpd_validate.py"
COMMIT = "fedcba9876543210fedcba9876543210fedcba98"


@pytest.fixture(autouse=True)
def _producer_commit(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", COMMIT)


def _validator():
    spec = importlib.util.spec_from_file_location("rpd_validate_connector", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["rpd_validate_connector"] = module
    spec.loader.exec_module(module)
    return module


def _path(project_id):
    return f"/api/v1/connector/projects/{project_id}/descriptor"


async def _get(client, project_id, credential=None, header=None):
    client.cookies.clear()  # the connector never relies on a browser session
    headers = {}
    if header is not None:
        headers["Authorization"] = header
    elif credential is not None:
        headers["Authorization"] = f"Bearer {credential}"
    return await client.get(_path(project_id), headers=headers)


async def _grant(client, do_login, project_id, who=LEAD, **body):
    res = await _create(client, do_login, project_id, who=who, **body)
    assert res.status_code == 201, res.text
    return res.json()


async def _reasons(session):
    rows = await session.execute(
        select(ConnectorAuditEvent.reason)
        .where(ConnectorAuditEvent.event == "access-refused")
        .order_by(ConnectorAuditEvent.id)
    )
    return list(rows.scalars())


def _assert_refused(res, reference):
    assert res.status_code == 404
    assert res.text == reference.text
    assert res.headers["cache-control"] == "no-store, private"


async def test_descriptor_is_issued_under_a_live_grant(
    client, do_login, session, researcher_user, admin_user, tmp_path
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    created = await _grant(client, do_login, a.id, ttl_days=3)
    res = await _get(client, a.id, created["credential"])
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "no-store, private"
    doc = json.loads(res.content)
    assert doc["access_effect"] == "none"
    assert doc["holder"] == {"membership": "active", "role": "lead"}
    assert doc["audience"] == {
        "grant_ref": created["grant"]["grant_ref"],
        "recipient_ref": "recipient:oratlas",
    }
    assert doc["project"]["title"] == {"state": "withheld"}
    assert doc["producer"] == {"commit": COMMIT, "name": "diggypaper"}
    assert doc["creation_method"] == "producer-export"
    assert doc["artifacts"] == []
    # A 3-day grant caps the descriptor below its 7-day maximum.
    assert doc["expires_at"] == created["grant"]["expires_at"]
    path = tmp_path / "descriptor.json"
    path.write_bytes(res.content)
    result = _validator().validate_descriptor(path, as_of=doc["issued_at"])
    assert result["errors"] == []
    assert result["descriptor_digest"] == "sha256:" + hashlib.sha256(res.content).hexdigest()
    assert "Confidential topic A" not in res.text
    assert researcher_user.email not in res.text


async def test_disclosed_title_only_when_granted(
    client, do_login, session, researcher_user, admin_user
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    created = await _grant(
        client,
        do_login,
        a.id,
        scopes=["list_project", "read_selected_metadata"],
        disclose=["title", "description"],
    )
    doc = (await _get(client, a.id, created["credential"])).json()
    assert doc["project"]["title"] == {
        "decision_ref": created["grant"]["decision_ref"],
        "state": "disclosed",
        "value": "Confidential topic A",
    }
    assert doc["project"]["description"] == {"state": "withheld"}  # the project has none


async def test_uniform_refusal(client, do_login, session, researcher_user, admin_user):
    a, b, _ = await _world(session, researcher_user, admin_user)
    good = (await _grant(client, do_login, a.id, who=BOTH))["credential"]
    no_list = (await _grant(client, do_login, a.id, scopes=["list_artifacts"]))["credential"]
    reference = await _get(client, uuid.uuid4())
    for res in (
        await _get(client, a.id),  # no credential
        await _get(client, a.id, header=good),  # not a Bearer header
        await _get(client, a.id, header=f"Basic {good}"),
        await _get(client, a.id, header=f"Bearer {good} extra"),
        await _get(client, a.id, "dpg_" + "A" * 43),  # unknown
        await _get(client, uuid.uuid4(), good),  # guessed project
        await _get(client, b.id, good),  # holder leads B too; the grant names A only
        await _get(client, a.id, no_list),  # scope not granted
    ):
        _assert_refused(res, reference)
    assert await _reasons(session) == [
        "unknown-credential",
        "project-mismatch",
        "project-mismatch",
        "scope-not-granted",
    ]


async def test_session_cookie_is_not_a_credential(
    client, do_login, session, researcher_user, admin_user
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    assert (await do_login(*LEAD)).status_code == 200
    res = await client.get(_path(a.id))
    assert res.status_code == 404


async def test_replay_after_revocation_is_refused(
    client, do_login, session, researcher_user, admin_user
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    created = await _grant(client, do_login, a.id)
    credential, grant_id = created["credential"], created["grant"]["id"]
    first = await _get(client, a.id, credential)
    assert first.status_code == 200
    assert (await _get(client, a.id, credential)).status_code == 200  # live: reusable
    await do_login(*LEAD)
    revoked = await client.post(f"/api/v1/projects/{a.id}/connector-grants/{grant_id}/revoke")
    assert revoked.status_code == 200
    reference = await _get(client, uuid.uuid4())
    _assert_refused(await _get(client, a.id, credential), reference)
    assert (await _reasons(session))[-1] == "revoked"


@pytest.mark.parametrize("how", ["member-removed", "project-deleted", "account-disabled"])
async def test_membership_or_project_end_stops_reads(
    client, do_login, session, researcher_user, admin_user, how
):
    a, _, users = await _world(session, researcher_user, admin_user)
    a_id, both_id = a.id, users["both"].id
    credential = (await _grant(client, do_login, a_id, who=BOTH))["credential"]
    assert (await _get(client, a_id, credential)).status_code == 200
    await do_login(*LEAD)
    if how == "member-removed":
        res = await client.delete(f"/api/v1/projects/{a_id}/members/{both_id}")
    elif how == "project-deleted":
        res = await client.delete(f"/api/v1/projects/{a_id}")
    else:
        users["both"].is_active = False
        await session.commit()
        res = None
    assert res is None or res.status_code == 204
    reference = await _get(client, uuid.uuid4())
    _assert_refused(await _get(client, a_id, credential), reference)


async def test_expired_grant_is_refused(client, session, researcher_user, admin_user):
    a, _, users = await _world(session, researcher_user, admin_user)
    past = datetime.now(UTC) - timedelta(days=31)
    _, credential = create_grant(
        session,
        project_id=a.id,
        holder=users["lead"],
        actor_ref=anonymized_actor_ref(users["lead"].email),
        recipient_ref="recipient:oratlas",
        scopes=["list_project"],
        purpose="expired",
        ttl=timedelta(days=30),
        now=past,
    )
    await session.commit()
    assert (await _get(client, a.id, credential)).status_code == 404
    assert (await _reasons(session))[-1] == "expired"


async def test_leaking_title_is_not_issued(client, do_login, session, researcher_user, admin_user):
    a, _, _ = await _world(session, researcher_user, admin_user)
    a.title = "Ask lead@lab.fr for access"
    await session.commit()
    created = await _grant(
        client,
        do_login,
        a.id,
        scopes=["list_project", "read_selected_metadata"],
        disclose=["title"],
    )
    res = await _get(client, a.id, created["credential"])
    assert res.status_code == 409
    assert "lead@lab.fr" not in res.text


async def test_missing_producer_commit_after_authorization(
    client, do_login, session, researcher_user, admin_user, monkeypatch
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    credential = (await _grant(client, do_login, a.id))["credential"]
    monkeypatch.delenv("GIT_COMMIT")
    assert (await _get(client, a.id, credential)).status_code == 409
    # Refusals still come first: an unknown credential never learns about configuration.
    assert (await _get(client, a.id, "dpg_unknown")).status_code == 404


async def test_other_lead_cannot_use_a_grant_on_their_project(
    client, do_login, session, researcher_user, admin_user
):
    a, b, _ = await _world(session, researcher_user, admin_user)
    credential_b = (await _grant(client, do_login, b.id, who=LEAD_B))["credential"]
    reference = await _get(client, uuid.uuid4())
    _assert_refused(await _get(client, a.id, credential_b), reference)
    assert (await _get(client, b.id, credential_b)).status_code == 200
    grants = (await session.execute(select(ConnectorGrant))).scalars().all()
    assert len(grants) == 1


# --- artifact references (list_artifacts) -------------------------------------------------


async def _approved_finding(client, do_login, session, project):
    """One reviewed, approved finding in ``project``: the restricted packet exports it."""
    article = Article(project_id=project.id, title="Mice study", doi="10.1000/mice")
    session.add(article)
    await session.commit()
    _, links = await _prediction(session, project, article, snapshot=True)
    base = f"/api/v1/projects/{project.id}/articles/{article.id}/findings"
    assert (await do_login(*LEAD, consent=True)).status_code == 200
    res = await client.post(base, json={"content": _content(), "evidence": links[:1]})
    assert res.status_code == 201, res.text
    finding_id = res.json()["finding_id"]
    assert (await do_login(*ANNOTATOR, consent=True)).status_code == 200
    res = await client.post(_rev_url(project, article, finding_id, 1), json=_review(links[:1]))
    assert res.status_code == 201, res.text
    return finding_id


async def test_listed_digest_is_the_lead_export_manifest(
    client, do_login, session, researcher_user, admin_user, tmp_path
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    await _approved_finding(client, do_login, session, a)
    credential = (await _grant(client, do_login, a.id))["credential"]
    res = await _get(client, a.id, credential)
    assert res.status_code == 200, res.text
    doc = json.loads(res.content)
    [artifact] = doc["artifacts"]
    assert artifact["access_level"] == "restricted"
    assert artifact["retrieval"] == "authorized-export-required"
    assert artifact["deep_link"] == f"/projects/{a.id}/findings"
    # Same bytes as the LEAD export of that moment: the digest binds its manifest.
    assert (await do_login(*LEAD)).status_code == 200
    export = await client.get(f"/api/v1/projects/{a.id}/findings/export")
    files = _unzip(export.content)
    manifest = files["manifest.json"]
    assert artifact["digest"] == "sha256:" + hashlib.sha256(manifest).hexdigest()
    assert artifact["packet_id"] == json.loads(manifest)["packet_id"]
    packet = tmp_path / "packet"
    packet.mkdir()
    for name, data in files.items():
        (packet / name).write_bytes(data)
    path = tmp_path / "descriptor.json"
    path.write_bytes(res.content)
    result = _validator().validate_descriptor(path, [packet], as_of=doc["issued_at"])
    assert result["errors"] == []
    assert result["counts"]["artifacts_verified"] == 1
    granted = await session.execute(
        select(ConnectorAuditEvent.scope).where(ConnectorAuditEvent.event == "access-granted")
    )
    assert sorted(granted.scalars()) == ["list_artifacts", "list_project"]


async def test_no_artifacts_without_the_scope(
    client, do_login, session, researcher_user, admin_user
):
    a, _, _ = await _world(session, researcher_user, admin_user)
    await _approved_finding(client, do_login, session, a)
    credential = (await _grant(client, do_login, a.id, scopes=["list_project"]))["credential"]
    res = await _get(client, a.id, credential)
    assert res.status_code == 200, res.text
    assert json.loads(res.content)["artifacts"] == []
    # Not holding a scope is not a refused access: nothing is logged as one.
    assert await _reasons(session) == []


async def test_artifacts_stay_in_their_project(
    client, do_login, session, researcher_user, admin_user
):
    a, b, _ = await _world(session, researcher_user, admin_user)
    await _approved_finding(client, do_login, session, a)
    # BOTH leads A and B: a grant on B never lists A's packet, and B has none of its own.
    credential = (await _grant(client, do_login, b.id, who=BOTH))["credential"]
    res = await _get(client, b.id, credential)
    assert res.status_code == 200, res.text
    assert json.loads(res.content)["artifacts"] == []
    assert str(a.id) not in res.text
