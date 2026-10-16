"""Synthetic reference records test ordering and scope, never human findings."""

import copy

import pytest
from test_seip_pilot_manifest import frozen_manifest

from app.seip_pilot_manifest import PilotManifestError, digest
from app.seip_reference_ledger import _strict_json, validate_reference_ledger

QUALIFIED = "2026-02-01T01:00:00Z"
CLEARED = "2026-02-02T00:00:00Z"
INITIAL = "2026-02-03T00:00:00Z"
ADJUDICATED = "2026-02-04T00:00:00Z"
LOCKED_A = "2026-02-05T00:00:00Z"
REVIEWED_B = "2026-03-02T00:00:00Z"
LOCKED_B = "2026-03-03T00:00:00Z"


def _locked(record, when):
    record["lock"] = {"at": when, "sha256": digest(record)}
    return record


def _field(status="not_reported", reason="synthetic searched input"):
    return {"field_key": "species", "status": status, "observations": [], "reason": reason}


def ledger_for(manifest):
    papers = [p for p in manifest["papers"] if p["split"] == "held_out"]
    actors = []
    for actor_id, role in (
        ("reviewer-a", "initial"),
        ("reviewer-b", "initial"),
        ("adjudicator-c", "adjudicator"),
    ):
        actors.append(
            {
                "id": actor_id,
                "role": role,
                "qualification": {
                    "expertise": "synthetic training declaration",
                    "protocol_version": manifest["protocol_version"],
                    "codebook_version": manifest["freeze"]["codebook_version"],
                    "signed_at": QUALIFIED,
                },
                "case_clearance": [
                    {
                        "paper_id": p["id"],
                        "conflict": "none",
                        "model_visible": False,
                        "peer_visible": False,
                        "checked_at": CLEARED,
                    }
                    for p in papers
                ],
            }
        )
    stage_a = []
    stage_b = []
    attempts = {a["paper_id"]: a for a in manifest["attempts"]}
    for paper in papers:
        a = _locked(
            {
                "paper_id": paper["id"],
                "source_sha256": paper["source"]["sha256"],
                "input_sha256": paper["effective_input"]["sha256"],
                "initial": [
                    {"actor_id": actor, "locked_at": INITIAL, "fields": [_field()]}
                    for actor in ("reviewer-a", "reviewer-b")
                ],
                "adjudications": [],
            },
            LOCKED_A,
        )
        stage_a.append(a)
        stage_b.append(
            _locked(
                {
                    "paper_id": paper["id"],
                    "stage_a_lock_sha256": a["lock"]["sha256"],
                    "input_sha256": paper["effective_input"]["sha256"],
                    "model_output_sha256": (
                        attempts[paper["id"]]["output_sha256"]
                        if attempts[paper["id"]]["state"] == "declared_valid"
                        else None
                    ),
                    "links": [],
                    "no_links_reason": "synthetic output has no link inventory",
                },
                LOCKED_B,
            )
        )
    return {
        "schema_version": "seip-independent-reference-v1",
        "state": "locked",
        "reference_set_id": manifest["freeze"]["reference_set_id"],
        "pilot_manifest_sha256": digest(manifest),
        "protocol_version": manifest["protocol_version"],
        "codebook_version": manifest["freeze"]["codebook_version"],
        "actors": actors,
        "stage_a": stage_a,
        "stage_b": stage_b,
    }


def test_complete_unsigned_synthetic_ledger(tmp_path):
    manifest = frozen_manifest(tmp_path)
    ledger = ledger_for(manifest)
    result = validate_reference_ledger(ledger, manifest, tmp_path)
    assert result["state"] == "structurally_valid_unsigned"
    assert result["held_out"] == result["stage_a"] == result["stage_b"] == 24
    assert result["qualification"] == "externally_unverified"
    assert validate_reference_ledger(ledger, manifest, tmp_path) == result


def test_draft_template_is_explicitly_pending(tmp_path):
    manifest = frozen_manifest(tmp_path)
    manifest["state"] = "draft"
    manifest["freeze"] = None
    manifest["attempts"] = []
    draft = {
        "schema_version": "seip-independent-reference-v1",
        "state": "draft",
        "reference_set_id": None,
        "pilot_manifest_sha256": None,
        "protocol_version": manifest["protocol_version"],
        "codebook_version": "pending",
        "actors": [],
        "stage_a": [],
        "stage_b": [],
    }
    assert validate_reference_ledger(draft, manifest, tmp_path) == {
        "state": "draft",
        "qualification": "pending",
    }


def test_locked_ledger_requires_reference_set_identity(tmp_path):
    manifest = frozen_manifest(tmp_path)
    ledger = ledger_for(manifest)
    manifest["freeze"]["reference_set_id"] = None
    ledger["reference_set_id"] = None
    ledger["pilot_manifest_sha256"] = digest(manifest)
    with pytest.raises(PilotManifestError, match="locked reference set"):
        validate_reference_ledger(ledger, manifest, tmp_path)


@pytest.mark.parametrize("raw", [b'{"state":1,"state":2}', b'{"x":NaN}', b'{"x":1e999}'])
def test_rejects_ambiguous_or_nonfinite_wire_json(raw):
    with pytest.raises(PilotManifestError):
        _strict_json(raw)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("missing_reviewer", "two initial"),
        ("missing_qualification", "entry has missing"),
        ("conflict", "case has conflict"),
        ("prior_model_exposure", "case has conflict"),
        ("alien_paper", "Stage A paper is unknown"),
        ("input_drift", "Stage A source/input"),
        ("missing_lock", "entry has missing"),
        ("attempt_before_a", "model attempt predates lock"),
        ("b_before_a", "Stage B lock predates lock"),
        ("b_before_attempt", "Stage B lock predates lock"),
        ("bad_a_digest", "Stage A lock hash"),
        ("bad_b_digest", "Stage B lock hash"),
    ],
)
def test_rejects_missing_roles_exposure_scope_and_locks(tmp_path, change, message):
    manifest = frozen_manifest(tmp_path)
    ledger = ledger_for(manifest)
    a = ledger["stage_a"][0]
    b = ledger["stage_b"][0]
    if change == "missing_reviewer":
        a["initial"].pop()
    elif change == "missing_qualification":
        ledger["actors"][0]["qualification"].pop("expertise")
    elif change == "conflict":
        ledger["actors"][0]["case_clearance"][0]["conflict"] = "conflicted"
    elif change == "prior_model_exposure":
        ledger["actors"][0]["case_clearance"][0]["model_visible"] = True
    elif change == "alien_paper":
        a["paper_id"] = "unknown-paper"
    elif change == "input_drift":
        a["input_sha256"] = "0" * 64
    elif change == "missing_lock":
        a.pop("lock")
    elif change == "attempt_before_a":
        a["lock"]["at"] = "2026-03-01T01:00:00Z"
        a["lock"]["sha256"] = digest({k: v for k, v in a.items() if k != "lock"})
        b["stage_a_lock_sha256"] = a["lock"]["sha256"]
        b["lock"]["sha256"] = digest({k: v for k, v in b.items() if k != "lock"})
    elif change == "b_before_a":
        b["lock"]["at"] = "2026-02-04T00:00:00Z"
    elif change == "b_before_attempt":
        b["lock"]["at"] = "2026-02-06T00:00:00Z"
    elif change == "bad_a_digest":
        a["lock"]["sha256"] = "0" * 64
    else:
        b["lock"]["sha256"] = "0" * 64
    with pytest.raises(PilotManifestError, match=message):
        validate_reference_ledger(ledger, manifest, tmp_path)


def test_disagreement_requires_third_actor_and_keeps_both_originals(tmp_path):
    manifest = frozen_manifest(tmp_path)
    ledger = ledger_for(manifest)
    a = ledger["stage_a"][0]
    b = ledger["stage_b"][0]
    a["initial"][1]["fields"][0] = _field("unclear", "synthetic ambiguity")
    originals = [review["fields"][0] for review in a["initial"]]
    a["adjudications"] = [
        {
            "field_key": "species",
            "actor_id": "adjudicator-c",
            "judged_at": ADJUDICATED,
            "original_sha256": digest(originals),
            "final": _field("unclear", "unresolved synthetic case"),
            "reason": "synthetic ambiguity remains",
        }
    ]
    a["lock"]["sha256"] = digest({k: v for k, v in a.items() if k != "lock"})
    b["stage_a_lock_sha256"] = a["lock"]["sha256"]
    b["lock"]["sha256"] = digest({k: v for k, v in b.items() if k != "lock"})
    assert validate_reference_ledger(ledger, manifest, tmp_path)["held_out"] == 24
    bad = copy.deepcopy(ledger)
    bad["stage_a"][0]["adjudications"][0]["actor_id"] = "reviewer-a"
    with pytest.raises(PilotManifestError, match="self-adjudication"):
        validate_reference_ledger(bad, manifest, tmp_path)
    bad = copy.deepcopy(ledger)
    bad["stage_a"][0]["adjudications"][0]["original_sha256"] = "0" * 64
    with pytest.raises(PilotManifestError, match="both original"):
        validate_reference_ledger(bad, manifest, tmp_path)


def test_exact_occurrence_cannot_infer_support(tmp_path):
    manifest = frozen_manifest(tmp_path)
    ledger = ledger_for(manifest)
    b = ledger["stage_b"][0]
    b["links"] = [
        {
            "link_id": "synthetic-link",
            "field_key": "species",
            "answer_revision": "revision-1",
            "evidence_id": "evidence-1",
            "quote": "synthetic",
            "match_state": "exact",
            "start": 0,
            "end": 9,
            "initial": [
                {
                    "actor_id": actor,
                    "judged_at": REVIEWED_B,
                    "support": status,
                    "reason": "independently assessed synthetic negation",
                }
                for actor, status in (("reviewer-a", "contradicts"), ("reviewer-b", "insufficient"))
            ],
            "adjudication": {
                "actor_id": "adjudicator-c",
                "judged_at": "2026-03-02T01:00:00Z",
                "support": "unclear",
                "reason": "synthetic conflict unresolved",
                "original_sha256": "",
            },
        }
    ]
    b["links"][0]["adjudication"]["original_sha256"] = digest(b["links"][0]["initial"])
    b["no_links_reason"] = None
    b["lock"]["sha256"] = digest({k: v for k, v in b.items() if k != "lock"})
    assert validate_reference_ledger(ledger, manifest, tmp_path)["stage_b"] == 24
    bad = copy.deepcopy(ledger)
    bad["stage_b"][0]["links"][0]["initial"] = []
    with pytest.raises(PilotManifestError, match="two independent Stage B"):
        validate_reference_ledger(bad, manifest, tmp_path)
    bad = copy.deepcopy(ledger)
    bad["stage_b"][0]["links"][0]["quote"] = "different"
    with pytest.raises(PilotManifestError, match="exact match does not replay"):
        validate_reference_ledger(bad, manifest, tmp_path)
    bad = copy.deepcopy(ledger)
    bad["stage_b"][0]["links"][0]["adjudication"]["actor_id"] = "reviewer-a"
    with pytest.raises(PilotManifestError, match="self-adjudication"):
        validate_reference_ledger(bad, manifest, tmp_path)


def test_reported_reference_requires_exact_input_span(tmp_path):
    manifest = frozen_manifest(tmp_path)
    ledger = ledger_for(manifest)
    a = ledger["stage_a"][0]
    b = ledger["stage_b"][0]
    observation = {
        "value": "mouse",
        "scope": {},
        "unit": None,
        "quote": "synthetic",
        "start": 0,
        "end": 9,
        "section": "synthetic abstract",
    }
    for review in a["initial"]:
        review["fields"] = [
            {
                "field_key": "species",
                "status": "reported",
                "observations": [observation],
                "reason": "synthetic fixture only",
            }
        ]
    a["lock"]["sha256"] = digest({key: value for key, value in a.items() if key != "lock"})
    b["stage_a_lock_sha256"] = a["lock"]["sha256"]
    b["lock"]["sha256"] = digest({key: value for key, value in b.items() if key != "lock"})
    assert validate_reference_ledger(ledger, manifest, tmp_path)["stage_a"] == 24
    bad = copy.deepcopy(ledger)
    bad["stage_a"][0]["initial"][0]["fields"][0]["observations"][0]["quote"] = "not there"
    with pytest.raises(PilotManifestError, match="quote/span does not replay"):
        validate_reference_ledger(bad, manifest, tmp_path)
