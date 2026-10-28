"""Connector end to end, as an external consumer sees it (#53), and the captured fixture.

One synthetic project goes through the whole bounded exchange over HTTP: a LEAD creates a
grant, the consumer fetches the descriptor and then the listed packet by its digest, checks
both with the contract validator, and every read is refused once the grant is revoked.

With ``CAPTURE_CONNECTOR_FIXTURE=<dir>`` (and ``CAPTURE_PRODUCER_COMMIT=<40 hex>``) the same run
writes the exact response bytes there. That is how ``fixtures/captured/connector-e2e`` was
made; it is a capture, not a rebuild: ids and timestamps differ on every run, so the committed
set is checked for its own integrity instead of byte equality with a fresh run.
"""

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest
from test_connector_api import _approved_finding, _grant, _validator
from test_findings_api import _unzip

ROOT = Path(__file__).resolve().parents[2]
CAPTURED = (
    ROOT / "docs/contracts/research-project-descriptor/v0.1.0/fixtures/captured/connector-e2e"
)
COMMIT = "fedcba9876543210fedcba9876543210fedcba98"
SCOPES = ["list_project", "list_artifacts", "obtain_authorized_export"]


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def _producer_commit(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", os.environ.get("CAPTURE_PRODUCER_COMMIT", COMMIT))


async def _bearer(client, path, credential):
    client.cookies.clear()  # a consumer has no Diggypaper session
    return await client.get(path, headers={"Authorization": f"Bearer {credential}"})


async def test_consumer_exchange_end_to_end(
    client, do_login, session, researcher_user, admin_user, tmp_path
):
    from test_connector_grants import LEAD, _world

    a, _, _ = await _world(session, researcher_user, admin_user)
    await _approved_finding(client, do_login, session, a)
    created = await _grant(client, do_login, a.id, scopes=SCOPES, ttl_days=7)
    credential, grant_id = created["credential"], created["grant"]["id"]

    descriptor_path = f"/api/v1/connector/projects/{a.id}/descriptor"
    descriptor = await _bearer(client, descriptor_path, credential)
    assert descriptor.status_code == 200, descriptor.text
    doc = json.loads(descriptor.content)
    [artifact] = doc["artifacts"]
    packet_path = f"/api/v1/connector/projects/{a.id}/packets/{artifact['digest']}"
    packet = await _bearer(client, packet_path, credential)
    assert packet.status_code == 200, packet.text
    files = _unzip(packet.content)
    assert _sha(files["manifest.json"]) == artifact["digest"]

    held = tmp_path / "packet"
    held.mkdir()
    for name, data in files.items():
        (held / name).write_bytes(data)
    (tmp_path / "descriptor.json").write_bytes(descriptor.content)
    result = _validator().validate_descriptor(
        tmp_path / "descriptor.json", [held], as_of=doc["issued_at"]
    )
    assert result["errors"] == [] and result["counts"]["artifacts_verified"] == 1
    for secret in (LEAD[0], researcher_user.email, "Confidential topic A"):
        assert secret.encode() not in descriptor.content + packet.content

    assert (await do_login(*LEAD)).status_code == 200
    revoke = await client.post(f"/api/v1/projects/{a.id}/connector-grants/{grant_id}/revoke")
    assert revoke.status_code == 200
    after = {
        "descriptor": (await _bearer(client, descriptor_path, credential)).status_code,
        "packet": (await _bearer(client, packet_path, credential)).status_code,
    }
    assert after == {"descriptor": 404, "packet": 404}

    out = os.environ.get("CAPTURE_CONNECTOR_FIXTURE")
    if out:
        _write_capture(Path(out), doc, descriptor, packet, files, a.id, after)


def _write_capture(out, doc, descriptor, packet, files, project_id, after):
    (out / "packet").mkdir(parents=True, exist_ok=True)
    (out / "descriptor.json").write_bytes(descriptor.content)
    for name, data in files.items():
        (out / "packet" / name).write_bytes(data)
    rel = {"descriptor.json": descriptor.content}
    rel.update({f"packet/{name}": data for name, data in files.items()})
    capture = {
        "kind": "captured-producer-output",
        "data": "synthetic",
        "note": (
            "Exact HTTP response bytes of the Diggypaper connector for one synthetic project "
            "(no real article, person or full text). Not a scientific result."
        ),
        "producer": doc["producer"],
        "valid_as_of": doc["issued_at"],
        "requests": [
            {
                "method": "GET",
                "path": "/api/v1/connector/projects/{project_id}/descriptor",
                "scope": "list_project, list_artifacts",
                "status": 200,
                "body": "descriptor.json",
            },
            {
                "method": "GET",
                "path": "/api/v1/connector/projects/{project_id}/packets/{digest}",
                "scope": "obtain_authorized_export",
                "status": 200,
                "body": "packet/ (zip entries)",
                "body_sha256": _sha(packet.content),
            },
        ],
        "after_revocation": after,
        "project_id": str(project_id),
        "files": {name: _sha(data) for name, data in sorted(rel.items())},
    }
    text = json.dumps(capture, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (out / "capture.json").write_text(text, encoding="utf-8", newline="\n")


def test_captured_fixture_is_intact_and_valid():
    capture = json.loads((CAPTURED / "capture.json").read_text(encoding="utf-8"))
    assert capture["kind"] == "captured-producer-output" and capture["data"] == "synthetic"
    on_disk = {
        p.relative_to(CAPTURED).as_posix(): _sha(p.read_bytes())
        for p in CAPTURED.rglob("*")
        if p.is_file() and p.name != "capture.json"
    }
    assert on_disk == capture["files"]
    doc = json.loads((CAPTURED / "descriptor.json").read_bytes())
    assert doc["producer"] == capture["producer"]
    assert len(doc["producer"]["commit"]) == 40 and doc["producer"]["commit"] != COMMIT
    assert doc["project"]["project_id"] == capture["project_id"]
    uuid.UUID(capture["project_id"])
    result = _validator().validate_descriptor(
        CAPTURED / "descriptor.json", [CAPTURED / "packet"], as_of=capture["valid_as_of"]
    )
    assert result["errors"] == [] and result["counts"]["artifacts_verified"] == 1
    assert capture["after_revocation"] == {"descriptor": 404, "packet": 404}
