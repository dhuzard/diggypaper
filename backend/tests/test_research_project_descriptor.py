"""Tests for research-project-descriptor/0.1.0: contract validator and pure builder (#53)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db.models import ProjectMemberRole
from app.project_descriptor import build_project_descriptor, packet_artifact
from tests import contract_fixtures

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "contracts" / "research-project-descriptor" / "v0.1.0"
VALIDATOR_PATH = CONTRACT / "validator" / "rpd_validate.py"
VALID = CONTRACT / "fixtures" / "valid"
# Negative fixtures are not committed: build_fixtures.py regenerates them (contract_fixtures.py).
INVALID_DIR = contract_fixtures.invalid_dir("research-project-descriptor")
INVALID_CASES = contract_fixtures.invalid_cases("research-project-descriptor")
SEP_VALID = ROOT / "docs/contracts/source-evidence-packet/v0.1.0/fixtures/valid"
SEP_CANONICAL = SEP_VALID / "canonical"
PROJECT_ID = uuid.UUID("00000000-0000-4000-8000-000000000053")
ISSUED = datetime(2026, 10, 22, tzinfo=UTC)
EXPIRES = ISSUED + timedelta(days=7)
GRANT = "grant:" + "5" * 32
DECISION = "decision:" + "3" * 32
TITLE = "Synthetic review of example findings"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rpd = _load("rpd_validate", VALIDATOR_PATH)


def _codes(result):
    return {e["code"] for e in result["errors"]}


def _packet_files(directory=SEP_CANONICAL):
    return {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}


def _build(**overrides):
    kwargs = {
        "project_id": PROJECT_ID,
        "holder_role": ProjectMemberRole.ANNOTATOR,
        "issued_at": ISSUED,
        "expires_at": EXPIRES,
        "recipient_ref": "recipient:oratlas",
        "producer_commit": "synthetic-fixture",
        "artifacts": [packet_artifact(PROJECT_ID, _packet_files())],
        "creation_method": "synthetic-fixture",
    }
    kwargs.update(overrides)
    return build_project_descriptor(**kwargs)


# --- contract fixtures -----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["withheld", "disclosed"])
def test_valid_descriptors(name):
    path = VALID / name / "descriptor.json"
    result = rpd.validate_descriptor(path, [SEP_CANONICAL], as_of="2026-10-23T00:00:00Z")
    assert result["errors"] == []
    assert result["valid"] is True
    assert result["counts"] == {"artifacts": 1, "artifacts_verified": 1}
    assert result["descriptor_digest"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("case", INVALID_CASES)
def test_invalid_fixture_codes(case):
    expected = json.loads((INVALID_DIR / case / "expected.json").read_text(encoding="utf-8"))
    packets = [SEP_VALID / expected["packet"]] if expected["packet"] else []
    result = rpd.validate_descriptor(INVALID_DIR / case / "descriptor.json", packets)
    assert result["valid"] is False
    assert _codes(result) == set(expected["errors"])
    assert "descriptor_digest" not in result


def test_every_readme_code_has_a_fixture_or_test():
    readme = (CONTRACT / "README.md").read_text(encoding="utf-8")
    section = readme.split("Error codes:", 1)[1].split("Every code", 1)[0]
    codes = set(re.findall(r"`([a-z-]+)`", section))
    fixture_codes = set()
    for case in INVALID_CASES:
        expected = json.loads((INVALID_DIR / case / "expected.json").read_text(encoding="utf-8"))
        fixture_codes |= set(expected["errors"])
    assert codes - fixture_codes == {"artifact-missing", "packet-invalid"}
    assert fixture_codes <= codes


def test_expired_as_of_and_missing_file(tmp_path):
    path = VALID / "withheld" / "descriptor.json"
    assert _codes(rpd.validate_descriptor(path, as_of="2026-10-29T00:00:00Z")) == {
        "descriptor-expired"
    }
    assert _codes(rpd.validate_descriptor(path, as_of="2026-10-21T00:00:00Z")) == {
        "descriptor-expired"
    }
    assert _codes(rpd.validate_descriptor(tmp_path / "absent.json")) == {"artifact-missing"}


def test_invalid_held_packet_is_refused(tmp_path):
    broken = tmp_path / "packet"
    shutil.copytree(SEP_CANONICAL, broken)
    (broken / "findings.jsonl").write_bytes(b"")
    result = rpd.validate_descriptor(VALID / "withheld" / "descriptor.json", [broken])
    assert _codes(result) == {"packet-invalid"}


def test_unverified_artifacts_are_counted_not_trusted():
    result = rpd.validate_descriptor(VALID / "withheld" / "descriptor.json")
    assert result["valid"] is True
    assert result["counts"] == {"artifacts": 1, "artifacts_verified": 0}


def test_cli_exit_codes():
    ok = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), str(VALID / "withheld" / "descriptor.json")],
        capture_output=True,
    )
    assert ok.returncode == 0
    bad = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            str(INVALID_DIR / "ttl-exceeded" / "descriptor.json"),
        ],
        capture_output=True,
    )
    assert bad.returncode == 1
    usage = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), "x.json", "--as-of", "yesterday"],
        capture_output=True,
    )
    assert usage.returncode == 2


def test_fixture_builder_is_idempotent(tmp_path):
    # The generator reads the frozen SEP packet relative to its own location; the rebuilt tree
    # keeps the committed layout. The committed valid descriptors are byte-identical to a fresh
    # build, and two independent builds agree on every negative fixture.
    rebuilt = contract_fixtures.build(tmp_path)
    contract_fixtures.assert_rebuild_matches(
        "research-project-descriptor",
        rebuilt / "research-project-descriptor" / "v0.1.0" / "fixtures",
    )


# --- builder ---------------------------------------------------------------------------------


def test_builder_matches_withheld_fixture():
    assert _build() == (VALID / "withheld" / "descriptor.json").read_bytes()


def test_builder_matches_disclosed_fixture():
    data = _build(
        holder_role="LEAD",
        grant_ref=GRANT,
        disclosed={"title": (TITLE, DECISION)},
    )
    assert data == (VALID / "disclosed" / "descriptor.json").read_bytes()


def test_builder_output_validates(tmp_path):
    path = tmp_path / "descriptor.json"
    path.write_bytes(_build(producer_commit="a" * 40, creation_method="producer-export"))
    result = rpd.validate_descriptor(path, [SEP_CANONICAL])
    assert result["errors"] == []
    assert result["counts"]["artifacts_verified"] == 1


def test_builder_carries_no_private_metadata():
    doc = json.loads(_build())
    assert doc["project"]["title"] == {"state": "withheld"}
    assert doc["project"]["description"] == {"state": "withheld"}
    assert doc["access_effect"] == "none"
    assert doc["audience"]["grant_ref"] == "not-available"
    assert set(doc) == {
        "access_effect",
        "artifacts",
        "audience",
        "creation_method",
        "expires_at",
        "holder",
        "issued_at",
        "policy",
        "producer",
        "project",
        "schema_version",
        "scope",
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"holder_role": None}, "active project member"),
        ({"holder_role": "ADMIN"}, "ADMIN"),
        ({"expires_at": ISSUED + timedelta(days=7, seconds=1)}, "at most 7 days"),
        ({"expires_at": ISSUED}, "at most 7 days"),
        ({"issued_at": datetime(2026, 10, 22)}, "timezone-aware"),
        ({"disclosed": {"title": (TITLE, DECISION)}}, "needs a grant_ref"),
        (
            {"grant_ref": GRANT, "disclosed": {"title": (TITLE, "decision:x")}},
            "invalid decision_ref",
        ),
        ({"grant_ref": GRANT, "disclosed": {"members": ("x", DECISION)}}, "not disclosable"),
        ({"grant_ref": GRANT, "disclosed": {"title": ("  ", DECISION)}}, "empty"),
        (
            {"grant_ref": GRANT, "disclosed": {"title": ("ask lead@example.org", DECISION)}},
            "email or a bare digest",
        ),
        (
            {"grant_ref": GRANT, "disclosed": {"description": ("x " + "ab" * 32, DECISION)}},
            "email or a bare digest",
        ),
        ({"grant_ref": "grant:short"}, "invalid grant_ref"),
        ({"recipient_ref": "oratlas"}, "invalid recipient_ref"),
        ({"producer_commit": "unknown"}, "40-hex"),
    ],
)
def test_builder_fails_closed(overrides, message):
    with pytest.raises(ValueError, match=message):
        _build(**overrides)


def test_builder_rejects_artifacts_of_another_project():
    other = packet_artifact(uuid.uuid4(), _packet_files())
    with pytest.raises(ValueError, match="inside its project"):
        _build(artifacts=[other])


def test_builder_rejects_duplicate_artifacts():
    ref = packet_artifact(PROJECT_ID, _packet_files())
    with pytest.raises(ValueError, match="duplicate"):
        _build(artifacts=[ref, dict(ref)])


def test_packet_artifact_binds_exact_manifest_bytes():
    files = _packet_files()
    ref = packet_artifact(PROJECT_ID, files)
    assert ref["digest"] == "sha256:" + hashlib.sha256(files["manifest.json"]).hexdigest()
    files["manifest.json"] = files["manifest.json"].replace(b"\n", b"\r\n")
    assert packet_artifact(PROJECT_ID, files)["digest"] != ref["digest"]


def test_only_the_grant_checked_connector_issues_descriptors():
    api = ROOT / "backend" / "app" / "api"
    users = sorted(
        p.name for p in api.rglob("*.py") if "project_descriptor" in p.read_text(encoding="utf-8")
    )
    assert users == ["connector.py"]
