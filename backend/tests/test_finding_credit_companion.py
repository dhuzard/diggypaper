"""Tests for the finding-credit-companion/0.1.0 contract validator (docs/contracts)."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import contract_fixtures

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "contracts" / "finding-credit-companion" / "v0.1.0"
VALIDATOR_PATH = CONTRACT / "validator" / "fcc_validate.py"
PACKETS = CONTRACT / "fixtures" / "packets"
VALID = CONTRACT / "fixtures" / "valid"
# Negative fixtures are not committed: build_fixtures.py regenerates them (contract_fixtures.py).
INVALID_DIR = contract_fixtures.invalid_dir("finding-credit-companion")
INVALID_CASES = contract_fixtures.invalid_cases("finding-credit-companion")
SEP_CANONICAL = ROOT / "docs/contracts/source-evidence-packet/v0.1.0/fixtures/valid/canonical"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fcc = _load("fcc_validate", VALIDATOR_PATH)
sep = fcc.sep
VALID_PAIRS = [("public", PACKETS / "public"), ("project-members", PACKETS / "private")]


def _doc(name="public"):
    return json.loads((VALID / name / "credit.json").read_text(encoding="utf-8"))


def _write(tmp_path, doc, name="credit.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _codes(result):
    return {e["code"] for e in result["errors"]}


def _contrib(doc, cid):
    return next(
        c for e in doc["entries"] for c in e["contributions"] if c["contribution_id"] == cid
    )


# --- fixtures --------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "packet"), VALID_PAIRS)
def test_valid_companions(name, packet):
    result = fcc.validate_companion(VALID / name / "credit.json", packet)
    assert result["errors"] == []
    assert result["valid"] is True
    raw = (VALID / name / "credit.json").read_bytes()
    assert result["companion_digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    manifest = (packet / "manifest.json").read_bytes()
    assert result["packet_digest"] == "sha256:" + hashlib.sha256(manifest).hexdigest()


def test_fixture_packets_are_valid_source_evidence_packets():
    assert sep.validate_packet(PACKETS / "public", "for-import")["valid"] is True
    # The private packet is valid for trusted internal use only, never for import.
    assert sep.validate_packet(PACKETS / "private", "allow-restricted")["valid"] is True
    strict = sep.validate_packet(PACKETS / "private", "for-import")
    assert "private-packet-not-exportable" in {e["code"] for e in strict["errors"]}


def test_invalid_fixture_cases_exist():
    assert len(INVALID_CASES) >= 30


@pytest.mark.parametrize("case", INVALID_CASES)
def test_invalid_fixture_reports_expected_codes(case):
    expected = json.loads((INVALID_DIR / case / "expected.json").read_text(encoding="utf-8"))
    assert expected["errors"], case
    result = fcc.validate_companion(
        INVALID_DIR / case / "credit.json", PACKETS / expected["packet"]
    )
    assert result["valid"] is False
    missing = set(expected["errors"]) - _codes(result)
    assert not missing, f"{case}: missing {missing}; got {sorted(_codes(result))}"
    assert "companion_digest" not in result


def test_fixtures_are_reproducible(tmp_path):
    # The committed packets and valid companions are byte-identical to a fresh build, and two
    # independent builds (this one and the session's) agree on every negative fixture.
    rebuilt = contract_fixtures.build(tmp_path)
    contract_fixtures.assert_rebuild_matches(
        "finding-credit-companion", rebuilt / "finding-credit-companion" / "v0.1.0" / "fixtures"
    )


def test_determinism_and_sorted_errors():
    for case in INVALID_CASES:
        packet = PACKETS / json.loads((INVALID_DIR / case / "expected.json").read_text())["packet"]
        a = fcc.validate_companion(INVALID_DIR / case / "credit.json", packet)
        b = fcc.validate_companion(INVALID_DIR / case / "credit.json", packet)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
        keys = [(e["code"], e["file"], e["id"], e["message"]) for e in a["errors"]]
        assert keys == sorted(keys)


def test_cli_exit_codes_and_byte_identical_output():
    def run(*args):
        return subprocess.run(
            [sys.executable, "-I", str(VALIDATOR_PATH), *args], capture_output=True, check=False
        )

    good = [str(VALID / "public" / "credit.json"), "--packet", str(PACKETS / "public")]
    first, second = run(*good), run(*good)
    assert first.returncode == 0 and first.stdout == second.stdout
    assert json.loads(first.stdout)["valid"] is True
    bad = run(
        str(INVALID_DIR / "consent-missing" / "credit.json"), "--packet", str(PACKETS / "public")
    )
    assert bad.returncode == 1
    assert run().returncode == 2
    assert run(good[0]).returncode == 2  # --packet is required: a companion never stands alone
    assert run(good[0], "--packet", str(PACKETS / "missing")).returncode == 2
    assert run(*good, "--as-of", "tomorrow").returncode == 2


# --- binding to the exact packet ---------------------------------------------------------------


def test_companion_is_refused_against_another_valid_packet():
    result = fcc.validate_companion(VALID / "public" / "credit.json", SEP_CANONICAL)
    assert "packet-digest-mismatch" in _codes(result)


def test_companion_is_refused_against_an_invalid_packet(tmp_path):
    packet = tmp_path / "packet"
    shutil.copytree(PACKETS / "public", packet)
    findings = packet / "findings.jsonl"
    findings.write_bytes(findings.read_bytes().replace(b"week 2", b"week 3"))
    result = fcc.validate_companion(VALID / "public" / "credit.json", packet)
    assert "packet-invalid" in _codes(result)
    assert result["valid"] is False


def test_reformatted_manifest_is_a_different_packet(tmp_path):
    packet = tmp_path / "packet"
    shutil.copytree(PACKETS / "public", packet)
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    (packet / "manifest.json").write_text(
        json.dumps(manifest, indent=4, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    assert sep.validate_packet(packet, "for-import")["valid"] is True
    result = fcc.validate_companion(VALID / "public" / "credit.json", packet)
    assert "packet-digest-mismatch" in _codes(result)


def test_handoff_expiry_is_checked_against_the_consumer_clock():
    doc = VALID / "project-members" / "credit.json"
    before = fcc.validate_companion(doc, PACKETS / "private", as_of="2027-02-28T00:00:00Z")
    assert before["valid"] is True
    after = fcc.validate_companion(doc, PACKETS / "private", as_of="2027-03-01T00:00:00Z")
    assert "handoff-expired" in _codes(after)


# --- separation of the omittable companion ------------------------------------------------------


def test_companion_is_omittable_and_never_inside_the_packet(tmp_path):
    # The packet validates on its own; the companion is a sibling file, never a packet member.
    assert sep.validate_packet(PACKETS / "public", "for-import")["valid"] is True
    packet = tmp_path / "packet"
    shutil.copytree(PACKETS / "public", packet)
    shutil.copy(VALID / "public" / "credit.json", packet / "credit.json")
    result = sep.validate_packet(packet, "for-import")
    assert "unexpected-file" in {e["code"] for e in result["errors"]}


def test_frozen_packet_contract_is_untouched():
    readme = (ROOT / "docs/contracts/source-evidence-packet/v0.1.0/README.md").read_text("utf-8")
    assert "finding-credit-companion" not in readme
    assert sep.SCHEMA_VERSION == "source-evidence-packet/0.1.0"
    assert sep.validate_packet(SEP_CANONICAL, "for-import")["valid"] is True


# --- credit semantics ---------------------------------------------------------------------------


def test_two_findings_of_one_article_keep_distinct_proposers_and_assessments():
    doc = _doc()
    a = [e for e in doc["entries"] if e["binding"]["paper_id"] == "paper:credit-a"]
    assert [e["binding"]["finding_id"] for e in a] == ["finding:credit-a-f1", "finding:credit-a-f2"]
    proposers = [
        c["contributor"]["ref"]
        for e in a
        for c in e["contributions"]
        if c["capacity"] == "finding-proposer"
    ]
    assert len(set(proposers)) == 2
    assert not set(a[0]["binding"]["assessment_ids"]) & set(a[1]["binding"]["assessment_ids"])
    assert a[0]["binding"]["passage_ids"] != a[1]["binding"]["passage_ids"]


def test_claimed_and_verified_orcid_are_never_conflated():
    doc = _doc()
    one = _contrib(doc, "contribution:a-f1-curator")["contributor"]["orcid"]
    two = _contrib(doc, "contribution:a-f2-curator")["contributor"]["orcid"]
    assert one["id"] == two["id"]  # same string on purpose
    assert one["verification"] == "oauth-verified" and one["verification_ref"] != "not-available"
    assert two["verification"] == "claimed-unverified" and two["verified_at"] == "not-available"
    counts = fcc.validate_companion(VALID / "public" / "credit.json", PACKETS / "public")["counts"]
    assert counts["orcid_by_verification"] == {"claimed-unverified": 1, "oauth-verified": 1}


def test_orcid_checksum():
    assert fcc.orcid_checksum_ok("0000-0002-1825-0097")
    assert not fcc.orcid_checksum_ok("0000-0002-1825-0098")


def test_public_companion_shares_only_approved_findings_and_counts_the_rest():
    public = _doc()
    members = _doc("project-members")
    shared = {e["binding"]["finding_id"] for e in public["entries"]}
    trail = {e["binding"]["finding_id"] for e in members["entries"]}
    assert "finding:credit-c-f1" in trail - shared
    assert public["export_status"]["withheld_counts"]["entries"] == 1
    assert public["release"]["state"] == "controller-approved"
    assert members["release"]["state"] == "not-released"


def test_blinded_reviewers_stay_pseudonymous_or_withheld():
    for name in ("public", "project-members"):
        for e in _doc(name)["entries"]:
            for c in e["contributions"]:
                if c["review_blinding"] in ("blinded", "unknown"):
                    assert c["contributor"]["kind"] in ("pseudonym", "withheld")


def test_no_private_fields_or_cited_authors_in_companions():
    authors = set()
    for line in (PACKETS / "public" / "papers.jsonl").read_text("utf-8").splitlines():
        authors |= set(json.loads(line)["authors"])
    for name in ("public", "project-members"):
        text = (VALID / name / "credit.json").read_text("utf-8")
        for forbidden in ("email", "login", "rationale", "project_id", "user_id", "@"):
            assert forbidden not in text
        for author in authors:
            assert author not in text  # bibliographic credit stays in the packet


def test_credit_roles_never_on_inactive_assertions(tmp_path):
    doc = _doc()
    c = _contrib(doc, "contribution:a-f2-curator")
    c["status"] = "disputed"
    c["contributor"] = {"kind": "withheld", "ref": "attribution-withheld"}
    result = fcc.validate_companion(_write(tmp_path, doc), PACKETS / "public")
    assert _codes(result) == {"credit-role-not-asserted"}
    c["credit_roles"] = []
    assert fcc.validate_companion(_write(tmp_path, doc), PACKETS / "public")["valid"] is True


def test_revocation_after_release_prevents_further_disclosure(tmp_path):
    doc = _doc()
    for field in ("person_consent", "controller_authorization", "membership"):
        mutated = copy.deepcopy(doc)
        _contrib(mutated, "contribution:a-f1-curator")["disclosure"][field] = "revoked"
        result = fcc.validate_companion(_write(tmp_path, mutated), PACKETS / "public")
        assert _codes(result) == {"consent-revoked"}, field
    # The compliant re-export after revocation withholds the identity and its roles' owner.
    c = _contrib(doc, "contribution:a-f1-curator")
    c["disclosure"]["person_consent"] = "revoked"
    c["contributor"] = {"kind": "withheld", "ref": "attribution-withheld"}
    assert fcc.validate_companion(_write(tmp_path, doc), PACKETS / "public")["valid"] is True


def test_cited_author_name_is_only_a_warning(tmp_path):
    doc = _doc()
    _contrib(doc, "contribution:a-f1-curator")["contributor"]["display_name"] = "Synthetic, Dana"
    result = fcc.validate_companion(_write(tmp_path, doc), PACKETS / "public")
    assert result["valid"] is True
    assert {w["code"] for w in result["warnings"]} == {"cited-author-name-match"}


def test_one_entry_binds_exactly_one_paper(tmp_path):
    doc = _doc()
    doc["entries"][0]["binding"]["paper_id"] = ["paper:credit-a", "paper:credit-b"]
    result = fcc.validate_companion(_write(tmp_path, doc), PACKETS / "public")
    assert "schema-invalid" in _codes(result)


def test_missing_companion_file_is_reported(tmp_path):
    result = fcc.validate_companion(tmp_path / "absent.json", PACKETS / "public")
    assert _codes(result) == {"artifact-missing"}
