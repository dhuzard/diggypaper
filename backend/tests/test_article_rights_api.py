"""Article redistribution rights: LEAD-only append-only declarations and their export effect."""

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError

from app.db.models import ArticleRightsDeclaration
from tests.test_findings_api import (
    LEAD,
    OUTSIDER,
    REV1,
    REV2,
    _article,
    _content,
    _export_url,
    _load_validator,
    _login,
    _prediction,
    _propose,
    _rev_url,
    _review,
    _unzip,
    _world,
)

ALLOWED = {
    "license_id": "CC-BY-4.0",
    "redistribution": "allowed",
    "basis": "Publisher page states CC BY 4.0 for the article and its text.",
    "evidence_url": "https://example.org/article/licence",
}
QUOTE = "Mice were randomized."
COMMIT = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(autouse=True)
def _producer_commit(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", COMMIT)


def _rights_url(project, article):
    return f"/api/v1/projects/{project.id}/articles/{article.id}/rights"


async def _declare(client, project, article, body, status=201):
    response = await client.post(_rights_url(project, article), json=body)
    assert response.status_code == status, response.text
    return response.json()


async def _approved_finding(client, session, project, article, do_login):
    """One REV1-approved finding on a captured snapshot, backed by QUOTE."""
    _, links = await _prediction(session, project, article, quotes=(QUOTE,), snapshot=True)
    await _login(do_login, LEAD)
    created = await _propose(client, project, article, links)
    await _login(do_login, REV1)
    response = await client.post(
        _rev_url(project, article, created["finding_id"], 1), json=_review(links)
    )
    assert response.status_code == 201, response.text
    await _login(do_login, LEAD)
    return created


async def _export(client, project, **params):
    response = await client.get(_export_url(project), params=params)
    assert response.status_code == 200, response.text
    return _unzip(response.content)


def _write(files, directory: Path) -> Path:
    directory.mkdir()
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return directory


def _records(files, name):
    return [json.loads(x) for x in files[name].decode().splitlines()]


# --- declarations ------------------------------------------------------------------------


async def test_lead_declares_append_only_history_with_pseudonyms(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    await _login(do_login, LEAD)
    first = await _declare(
        client,
        project,
        article,
        {"license_id": "unknown", "redistribution": "metadata-only", "basis": "Not checked yet."},
    )
    second = await _declare(client, project, article, ALLOWED)
    assert (first["revision"], second["revision"]) == (1, 2)
    history = (await client.get(_rights_url(project, article))).json()["declarations"]
    assert [d["revision"] for d in history] == [1, 2]
    assert history[1]["license_id"] == "CC-BY-4.0"
    assert history[1]["evidence_url"] == ALLOWED["evidence_url"]
    assert all(d["declared_by_ref"].startswith("reviewer-") for d in history)
    assert LEAD[0] not in json.dumps(history)


async def test_annotator_and_outsider_cannot_declare(client, session, researcher_user, do_login):
    project, article, _ = await _world(session, researcher_user)
    await _login(do_login, REV1)
    await _declare(client, project, article, ALLOWED, status=403)
    # Members may read the audit trail.
    assert (await client.get(_rights_url(project, article))).status_code == 200
    await _login(do_login, OUTSIDER)
    response = await client.post(_rights_url(project, article), json=ALLOWED)
    assert response.status_code in (403, 404)
    assert (await client.get(_rights_url(project, article))).status_code in (403, 404)


async def test_article_from_another_project_is_not_addressable(
    client, session, researcher_user, do_login
):
    project, _, _ = await _world(session, researcher_user)
    other, foreign, _ = await _world_other(session, researcher_user)
    await _login(do_login, LEAD)
    response = await client.post(
        f"/api/v1/projects/{project.id}/articles/{foreign.id}/rights", json=ALLOWED
    )
    assert response.status_code == 404
    assert other.id != project.id


async def _world_other(session, researcher_user):
    from app.db.models import Project, ProjectMember, ProjectMemberRole

    project = Project(title="Other", created_by=researcher_user.id)
    session.add(project)
    await session.flush()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    await session.commit()
    article = await _article(session, project, doi="10.1000/other")
    return project, article, None


@pytest.mark.parametrize(
    "override",
    [
        {"license_id": "CC-BY-NC-4.0"},  # non-commercial: not redistributable for any purpose
        {"license_id": "CC-BY-ND-4.0"},
        {"license_id": "proprietary"},
        {"license_id": "unknown"},
        {"evidence_url": None},
        {"evidence_url": "http://example.org/licence"},
        {"evidence_url": "javascript:alert(1)"},
        {"basis": "   "},
        {"basis": ""},
        {"license_id": "<b>CC-BY-4.0</b>"},
        {"redistribution": "public-domain"},
        {"quotes": "include anyway"},
    ],
)
async def test_invalid_declarations_rejected(client, session, researcher_user, do_login, override):
    project, article, _ = await _world(session, researcher_user)
    await _login(do_login, LEAD)
    body = {k: v for k, v in {**ALLOWED, **override}.items() if v is not None}
    await _declare(client, project, article, body, status=422)
    assert (await client.get(_rights_url(project, article))).json()["declarations"] == []


async def test_database_enforces_the_allowlist_and_append_only(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    row = {
        "project_id": project.id,
        "article_id": article.id,
        "revision": 1,
        "license_id": "CC-BY-NC-4.0",
        "redistribution": "allowed",
        "basis": "Bypassing the API.",
        "evidence_url": "https://example.org/x",
        "declared_by_ref": "a" * 64,
    }
    for bad in (
        {},
        {"license_id": "CC-BY-4.0", "evidence_url": None},
        {"license_id": "CC-BY-4.0", "evidence_url": "http://example.org/x"},
        {"license_id": "CC-BY-4.0", "redistribution": "everything"},
        {"license_id": "CC-BY-4.0", "basis": "  "},
        {"license_id": "CC-BY-4.0", "declared_by_ref": "short"},
        {"license_id": "CC-BY-4.0", "revision": 0},
    ):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    insert(ArticleRightsDeclaration.__table__).values(
                        id=uuid.uuid4(), **{**row, **bad}
                    )
                )
    await _login(do_login, LEAD)
    declared = await _declare(client, project, article, ALLOWED)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                update(ArticleRightsDeclaration)
                .where(ArticleRightsDeclaration.id == uuid.UUID(declared["id"]))
                .values(redistribution="restricted")
            )


# --- export ------------------------------------------------------------------------------


async def test_default_export_withholds_quotes_without_a_declaration(
    client, session, researcher_user, do_login, tmp_path
):
    project, article, _ = await _world(session, researcher_user)
    await _approved_finding(client, session, project, article, do_login)
    files = await _export(client, project)
    (passage,) = _records(files, "passages.jsonl")
    assert passage["quote_state"] == "withheld-rights"
    assert QUOTE.encode() not in b"".join(files.values())
    manifest = json.loads(files["manifest.json"])
    assert manifest["access_scope"]["level"] == "restricted"
    result = _load_validator().validate_packet(_write(files, tmp_path / "p"), "for-import")
    assert result["errors"] == [], result["errors"]


async def test_metadata_only_declaration_still_withholds(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    await _approved_finding(client, session, project, article, do_login)
    await _declare(
        client,
        project,
        article,
        {**ALLOWED, "redistribution": "metadata-only", "license_id": "CC-BY-NC-4.0"},
    )
    files = await _export(client, project, access_level="public", metadata_license="CC0-1.0")
    (passage,) = _records(files, "passages.jsonl")
    assert passage["quote_state"] == "withheld-rights"
    assert QUOTE.encode() not in b"".join(files.values())
    (paper,) = _records(files, "papers.jsonl")
    assert paper["rights"]["license"] == "CC-BY-NC-4.0"
    assert paper["rights"]["redistribution"] == "metadata-only"


async def test_allowed_declaration_releases_quotes_and_validates(
    client, session, researcher_user, do_login, tmp_path
):
    project, article, _ = await _world(session, researcher_user)
    await _approved_finding(client, session, project, article, do_login)
    await _declare(client, project, article, ALLOWED)
    files = await _export(client, project, access_level="public", metadata_license="CC0-1.0")
    (passage,) = _records(files, "passages.jsonl")
    assert passage["quote_state"] == "included"
    assert passage["quote"] == QUOTE
    assert passage["quote_sha256"] == "sha256:" + hashlib.sha256(QUOTE.encode()).hexdigest()
    manifest = json.loads(files["manifest.json"])
    assert manifest["access_scope"]["level"] == "public"
    assert {"license_id": "CC-BY-4.0", "applies_to": "quotes"} in manifest["licenses"]
    directory = _write(files, tmp_path / "p")
    result = _load_validator().validate_packet(directory, "for-import")
    assert result["errors"] == [], result["errors"]
    assert result["packet_digest"] == "sha256:" + hashlib.sha256(files["manifest.json"]).hexdigest()
    # Restricted is still the default even once rights allow quotes.
    default = await _export(client, project)
    assert json.loads(default["manifest.json"])["access_scope"]["level"] == "restricted"


async def test_later_restricted_declaration_supersedes_allowed(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    created = await _approved_finding(client, session, project, article, do_login)
    await _declare(client, project, article, ALLOWED)
    await _declare(
        client,
        project,
        article,
        {
            "license_id": "proprietary",
            "redistribution": "restricted",
            "basis": "Publisher withdrew the open licence; takedown request received.",
        },
    )
    files = await _export(client, project, access_level="public", metadata_license="CC0-1.0")
    assert created["finding_id"] not in files["findings.jsonl"].decode()
    assert QUOTE.encode() not in b"".join(files.values())
    assert "paper-rights-restricted" in files["manifest.json"].decode()


@pytest.mark.parametrize(
    "params",
    [
        {"access_level": "public"},
        {"access_level": "public", "metadata_license": "CC-BY-NC-4.0"},
        {"metadata_license": "proprietary"},
        {"access_level": "open"},
    ],
)
async def test_export_scope_options_rejected(client, session, researcher_user, do_login, params):
    project, _, _ = await _world(session, researcher_user)
    await _login(do_login, LEAD)
    response = await client.get(_export_url(project), params=params)
    assert response.status_code == 422, response.text


async def test_export_requires_a_producer_commit(
    client, session, researcher_user, do_login, monkeypatch
):
    project, _, _ = await _world(session, researcher_user)
    await _login(do_login, LEAD)
    files = await _export(client, project)
    assert json.loads(files["manifest.json"])["producer"]["commit"] == COMMIT
    for params in ({}, {"access_level": "public", "metadata_license": "CC0-1.0"}):
        for value in (None, "unknown", "abc123"):
            if value is None:
                monkeypatch.delenv("GIT_COMMIT", raising=False)
            else:
                monkeypatch.setenv("GIT_COMMIT", value)
            response = await client.get(_export_url(project), params=params)
            assert response.status_code == 409, response.text


async def test_annotator_cannot_export_public(client, session, researcher_user, do_login):
    project, _, _ = await _world(session, researcher_user)
    await _login(do_login, REV1)
    response = await client.get(
        _export_url(project), params={"access_level": "public", "metadata_license": "CC0-1.0"}
    )
    assert response.status_code == 403


# --- consumer: ComputationalReviewTemplate S2 importer (opt-in) ---------------------------

CRT = os.environ.get("CRT_CHECKOUT")


@pytest.mark.skipif(
    not CRT or shutil.which("node") is None,
    reason="set CRT_CHECKOUT to a ComputationalReviewTemplate checkout to run its importer",
)
async def test_crt_importer_accepts_redistributable_and_withholds_default(
    client, session, researcher_user, do_login, tmp_path
):
    project, article, _ = await _world(session, researcher_user)
    created = await _approved_finding(client, session, project, article, do_login)
    restricted = _write(await _export(client, project), tmp_path / "restricted")
    await _declare(client, project, article, ALLOWED)
    public = _write(
        await _export(client, project, access_level="public", metadata_license="CC0-1.0"),
        tmp_path / "public",
    )
    script = Path(CRT) / "scripts/import-source-evidence-packet.mjs"

    def run(packet: Path, out: Path):
        result = subprocess.run(
            ["node", str(script), "--packet", str(packet), "--out", str(out)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    accepted = run(public, tmp_path / "out-public")
    assert accepted["findings"]["accepted"] == 1, accepted
    out = tmp_path / "out-public"
    imported = "\n".join(p.read_text() for p in out.rglob("*") if p.is_file())
    assert created["finding_id"] in imported
    assert QUOTE in imported
    withheld = run(restricted, tmp_path / "out-restricted")
    assert withheld["findings"]["accepted"] == 0, withheld
    assert withheld["findings"]["withheld"] == 1, withheld
    assert "packet-scope-restricted" in json.dumps(withheld["by_reason"]), withheld


# --- rights apply to the whole revision chain --------------------------------------------

QUOTE_2 = "Weight fell by 1.5 g."


async def _two_revision_chain(client, session, project, article, do_login):
    _, links = await _prediction(session, project, article, quotes=(QUOTE, QUOTE_2), snapshot=True)
    await _login(do_login, LEAD)
    created = await _propose(client, project, article, links[:1])
    fid = created["finding_id"]
    await _login(do_login, REV1)
    ok = await client.post(_rev_url(project, article, fid, 1), json=_review(links[:1]))
    assert ok.status_code == 201, ok.text
    await _login(do_login, LEAD)
    revised = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/findings/{fid}/revisions",
        json={"content": _content(statement="Revised."), "evidence": links[1:]},
    )
    assert revised.status_code == 201, revised.text
    await _login(do_login, REV2)
    ok = await client.post(_rev_url(project, article, fid, 2), json=_review(links[1:]))
    assert ok.status_code == 201, ok.text
    await _login(do_login, LEAD)
    return fid


async def test_latest_rights_declaration_governs_every_revision_of_a_chain(
    client, session, researcher_user, do_login, tmp_path
):
    project, article, _ = await _world(session, researcher_user)
    fid = await _two_revision_chain(client, session, project, article, do_login)
    # no declaration: both historical and current quotes withheld, real hashes kept
    files = await _export(client, project)
    passages = _records(files, "passages.jsonl")
    assert [p["quote_state"] for p in passages] == ["withheld-rights"] * 2
    assert sorted(p["quote_sha256"] for p in passages) == sorted(
        "sha256:" + hashlib.sha256(q.encode()).hexdigest() for q in (QUOTE, QUOTE_2)
    )
    assert QUOTE.encode() not in b"".join(files.values())
    assert QUOTE_2.encode() not in b"".join(files.values())
    # allowed: both revisions' quotes are released
    await _declare(client, project, article, ALLOWED)
    files = await _export(client, project, access_level="public", metadata_license="CC0-1.0")
    assert sorted(p["quote"] for p in _records(files, "passages.jsonl")) == sorted([QUOTE, QUOTE_2])
    assert [f["revision"] for f in _records(files, "findings.jsonl")] == [1, 2]
    result = _load_validator().validate_packet(_write(files, tmp_path / "ok"), "for-import")
    assert result["errors"] == [], result["errors"]
    # a later restricted declaration withdraws the whole chain, historical revision included
    await _declare(
        client,
        project,
        article,
        {
            "license_id": "proprietary",
            "redistribution": "restricted",
            "basis": "Publisher withdrew the open licence; takedown request received.",
        },
    )
    files = await _export(client, project, access_level="public", metadata_license="CC0-1.0")
    assert fid not in files["findings.jsonl"].decode()
    assert QUOTE.encode() not in b"".join(files.values())
    assert (
        "paper-rights-restricted=2"
        in json.loads(files["manifest.json"])["export_status"]["reasons"]
    )
