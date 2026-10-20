"""Pure tests of the packet -> finding-credit-companion builder (no database, no route)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.finding_credit import build_credit_companion
from app.finding_packet import build_packet

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "docs/contracts/finding-credit-companion/v0.1.0/validator/fcc_validate.py"
_spec = importlib.util.spec_from_file_location("fcc_validate_fc", VALIDATOR)
assert _spec is not None and _spec.loader is not None
fcc = importlib.util.module_from_spec(_spec)
sys.modules["fcc_validate_fc"] = fcc
_spec.loader.exec_module(fcc)

COMMIT = "c" * 40
SHA_A = "a" * 64
PROPOSER_1 = "reviewer-" + "1" * 64
PROPOSER_2 = "reviewer-" + "2" * 64
RV_A = "reviewer-" + "a" * 64
RV_B = "reviewer-" + "b" * 64
EXPIRES = "2027-01-01T00:00:00Z"


def _content(outcome):
    return {
        "statement": f"X changed {outcome}.",
        "origin": "source-explicit",
        "context": {
            "population": "mice",
            "exposure": "X",
            "comparator": "vehicle",
            "outcome": outcome,
            "time": "day 14",
            "experimental_unit": "animal",
        },
        "numeric_results": [],
    }


def _finding(fid, proposer, reviews, ev_ref):
    return {
        "finding_id": fid,
        "article_id": "art-a",
        "revision": 1,
        "is_latest": True,
        "content": _content(fid),
        "proposer_ref": proposer,
        "created_at": "2026-03-01T09:00:00",
        "evidence": [
            {
                "prediction_id": "pred-1",
                "answer_ref": "ans-1",
                "evidence_ref": ev_ref,
                "quote": f"quote {ev_ref}",
                "match": {
                    "ref": f"qm-{ev_ref}",
                    "status": "exact",
                    "view_ref": "view:text-v1",
                    "start": 0,
                    "end": 5,
                },
                "snapshot_id": "snap-A",
            }
        ],
        "reviews": [
            {
                "id": rid,
                "reviewer_ref": who,
                "decision": "approve",
                "judgments": [{"evidence_ref": ev_ref, "status": "supports", "rationale": "ok"}],
                "created_at": "2026-03-02T10:00:00+00:00",
            }
            for rid, who in reviews
        ],
    }


@pytest.fixture
def packet_files():
    paper = {
        "article_id": "art-a",
        "doi": "10.5555/credit.builder",
        "pmid": None,
        "pmcid": None,
        "title": "Synthetic builder paper",
        "authors": ["Synthetic, Author"],
        "pub_types": ["Journal Article"],
        "snapshot": {"id": "snap-A", "sha256": SHA_A, "raw_availability": "available"},
    }
    findings = [
        _finding("f-1", PROPOSER_1, [("rev-1", RV_A)], "ev-1"),
        _finding("f-2", PROPOSER_2, [("rev-2", RV_A), ("rev-3", RV_B)], "ev-2"),
    ]
    return build_packet({"producer_commit": COMMIT, "papers": [paper], "findings": findings}).files


def _data(**over):
    data = {
        "producer_commit": COMMIT,
        "audience": {
            "level": "project-members",
            "purpose": "Synthetic: project credit trail",
            "expires_at": EXPIRES,
        },
        "findings": [
            {"finding_id": "f-1", "revision": 1, "proposer_ref": PROPOSER_1},
            {
                "finding_id": "f-2",
                "revision": 1,
                "proposer_ref": PROPOSER_2,
                "review_blinding": {"rev-2": True, "rev-3": False},
                "production": [
                    {
                        "agent_type": "ml-model",
                        "name": "synthetic-model",
                        "version": "0",
                        "activity": "extraction",
                        "run_ref": "pred-1",
                    }
                ],
            },
        ],
        "declarations": [],
    }
    data.update(over)
    return data


def _validate(tmp_path, packet_files, result):
    packet = tmp_path / "packet"
    packet.mkdir()
    for name, blob in packet_files.items():
        (packet / name).write_bytes(blob)
    (tmp_path / "credit.json").write_bytes(result.data)
    return fcc.validate_companion(tmp_path / "credit.json", packet)


def test_builder_output_validates_and_is_deterministic(tmp_path, packet_files):
    result = build_credit_companion(packet_files, _data())
    assert build_credit_companion(packet_files, _data()).data == result.data
    check = _validate(tmp_path, packet_files, result)
    assert check["errors"] == [] and check["valid"] is True
    assert result.report["packet_digest"] == check["packet_digest"]


def test_builder_never_discloses_and_never_infers_roles(packet_files):
    doc = json.loads(build_credit_companion(packet_files, _data()).data)
    contributions = [c for e in doc["entries"] for c in e["contributions"]]
    assert {c["contributor"]["kind"] for c in contributions} <= {"pseudonym", "withheld"}
    assert all(c["credit_roles"] == [] for c in contributions)
    assert {c["capacity"] for c in contributions} == {"finding-proposer", "support-assessor"}
    assert "Synthetic, Author" not in json.dumps(doc, ensure_ascii=False)
    # Distinct proposers for two findings of one article; assessors from the packet only.
    proposers = [
        c["contributor"]["ref"] for c in contributions if c["capacity"] == "finding-proposer"
    ]
    assert proposers == ["rv-" + "1" * 64, "rv-" + "2" * 64]
    f2 = doc["entries"][1]["contributions"]
    assert [c["review_blinding"] for c in f2 if c["capacity"] == "support-assessor"] == [
        "blinded",
        "unblinded",
    ]
    assert doc["entries"][1]["production"][0]["agent_type"] == "ml-model"


def test_explicit_declarations_carry_roles_and_inactive_ones_do_not(tmp_path, packet_files):
    declarations = [
        {
            "finding_id": "f-1",
            "revision": 1,
            "capacity": "finding-curator",
            "person_ref": "reviewer-" + "3" * 64,
            "status": "confirmed",
            "roles": [
                {"role": "Data curation", "degree": "lead", "declaration_ref": "declaration:d1"}
            ],
        },
        {
            "finding_id": "f-1",
            "revision": 1,
            "capacity": "support-assessor",
            "person_ref": RV_A,
            "status": "disputed",
            "roles": [
                {"role": "Validation", "degree": "lead", "declaration_ref": "declaration:d2"}
            ],
        },
    ]
    result = build_credit_companion(packet_files, _data(declarations=declarations))
    assert _validate(tmp_path, packet_files, result)["valid"] is True
    entry = json.loads(result.data)["entries"][0]
    by_cap = {c["capacity"]: c for c in entry["contributions"]}
    assert by_cap["finding-curator"]["credit_roles"][0]["role"] == "Data curation"
    assert by_cap["support-assessor"]["status"] == "disputed"
    assert by_cap["support-assessor"]["credit_roles"] == []


def test_builder_rejects_non_credit_roles_and_ai_people(packet_files):
    bad_role = [
        {
            "finding_id": "f-1",
            "revision": 1,
            "capacity": "finding-curator",
            "person_ref": PROPOSER_1,
            "status": "confirmed",
            "roles": [{"role": "Peer review", "declaration_ref": "declaration:x"}],
        }
    ]
    with pytest.raises(ValueError, match="CRediT"):
        build_credit_companion(packet_files, _data(declarations=bad_role))
    person_model = _data()
    person_model["findings"][1]["production"][0]["agent_type"] = "person"
    with pytest.raises(ValueError, match="never a person"):
        build_credit_companion(packet_files, person_model)


@pytest.mark.parametrize(
    ("audience", "release", "match"),
    [
        ({"level": "public", "purpose": "p"}, None, "controller-approved"),
        (
            {"level": "public", "purpose": "p"},
            {"approval_ref": "approval:a", "approved_at": "2026-04-01T00:00:00Z"},
            "exceeds",  # build_packet defaults to a restricted packet
        ),
        ({"level": "named-recipient", "purpose": "p", "expires_at": EXPIRES}, None, "recipient"),
        ({"level": "project-members", "purpose": "p"}, None, "expires_at"),
        ({"level": "project-members", "purpose": " ", "expires_at": EXPIRES}, None, "purpose"),
        (
            {"level": "project-members", "purpose": "p", "expires_at": "2026-03-01T00:00:00Z"},
            None,
            "expires before",
        ),
    ],
)
def test_builder_fails_closed_on_handoff_terms(packet_files, audience, release, match):
    with pytest.raises(ValueError, match=match):
        build_credit_companion(packet_files, _data(audience=audience, release=release))


def test_named_recipient_with_controller_release_validates(tmp_path, packet_files):
    data = _data(
        audience={
            "level": "named-recipient",
            "recipient_ref": "recipient:oratlas-test",
            "purpose": "Synthetic: interoperability test",
            "expires_at": EXPIRES,
        },
        release={"approval_ref": "approval:a1", "approved_at": "2026-04-01T00:00:00Z"},
    )
    result = build_credit_companion(packet_files, data)
    check = _validate(tmp_path, packet_files, result)
    assert check["errors"] == []
    assert json.loads(result.data)["created_at"] == "2026-04-01T00:00:00Z"


def test_missing_proposer_is_withheld_not_guessed(tmp_path, packet_files):
    data = _data()
    data["findings"][0].pop("proposer_ref")
    result = build_credit_companion(packet_files, data)
    assert _validate(tmp_path, packet_files, result)["valid"] is True
    proposer = json.loads(result.data)["entries"][0]["contributions"][0]
    assert proposer["status"] == "withheld"
    assert proposer["contributor"] == {"kind": "withheld", "ref": "attribution-withheld"}


def test_credit_binds_only_the_latest_revision_of_a_history_packet(tmp_path):
    first = {**_finding("f-1", PROPOSER_1, [("rev-1", RV_A)], "ev-1"), "is_latest": False}
    second = {**_finding("f-1", PROPOSER_1, [("rev-2", RV_B)], "ev-2"), "revision": 2}
    paper = {
        "article_id": "art-a",
        "doi": "10.5555/credit.history",
        "pmid": None,
        "pmcid": None,
        "title": "Synthetic history paper",
        "authors": ["Synthetic, Author"],
        "pub_types": ["Journal Article"],
        "snapshot": {"id": "snap-A", "sha256": SHA_A, "raw_availability": "available"},
    }
    files = build_packet(
        {"producer_commit": COMMIT, "papers": [paper], "findings": [first, second]}
    ).files
    assert [json.loads(x)["revision"] for x in files["findings.jsonl"].decode().splitlines()] == [
        1,
        2,
    ]
    data = _data()
    data["findings"] = [{"finding_id": "f-1", "revision": 2, "proposer_ref": PROPOSER_1}]
    result = build_credit_companion(files, data)
    check = _validate(tmp_path, files, result)
    assert check["errors"] == [] and check["valid"] is True
    entries = json.loads(result.data)["entries"]
    assert [e["binding"]["finding_revision"] for e in entries] == [2]
    assert entries[0]["binding"]["assessment_ids"] == ["assessment:rev-2-1"]
