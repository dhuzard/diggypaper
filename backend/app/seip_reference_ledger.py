"""Offline structural validator for independent SEIP pilot references.

This checks declarations, retained input slices and stage ordering. An unsigned
ledger cannot prove identities, qualifications, blinding, or chronology.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

from app.seip_pilot_manifest import (
    PilotManifestError,
    artifact,
    digest,
    keys,
    name,
    nonempty,
    sha,
    timestamp,
    validate_pilot_manifest,
    validate_pilot_manifest_bytes,
)
from app.seip_scoring import FieldSpec, Observation, ReferenceField, _validate_field

VERSION = "seip-independent-reference-v1"
STATUSES = {"reported", "not_reported", "not_accessible", "not_applicable", "unclear"}
SUPPORT = {"supports", "contradicts", "insufficient", "unclear"}
MAX_BYTES = 8 * 1024 * 1024


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PilotManifestError(message)


def _later(value: str, before: str, label: str) -> None:
    _require(
        timestamp(value, label) > timestamp(before, "preceding lock"), f"{label} predates lock"
    )


def _field(value: dict, spec: FieldSpec, text: str) -> None:
    keys(value, {"field_key", "status", "observations", "reason"})
    _require(value["field_key"] == spec.key, "field key mismatch")
    _require(value["status"] in STATUSES, "invalid reference status")
    nonempty(value["reason"], "reference reason")
    _require(isinstance(value["observations"], list), "observations must be a list")
    observations = []
    for row in value["observations"]:
        keys(row, {"value", "scope", "unit", "quote", "start", "end", "section"})
        _require(isinstance(row["scope"], dict), "observation scope must be an object")
        _require(isinstance(row["quote"], str) and bool(row["quote"]), "exact quote required")
        _require(isinstance(row["section"], str) and bool(row["section"]), "section required")
        _require(
            type(row["start"]) is int
            and type(row["end"]) is int
            and 0 <= row["start"] < row["end"] <= len(text)
            and text[row["start"] : row["end"]] == row["quote"],
            "reference quote/span does not replay from effective input",
        )
        observations.append(
            Observation(row["value"], tuple(sorted(row["scope"].items())), row["unit"])
        )
    try:
        _validate_field(spec, ReferenceField(value["status"], tuple(observations)))
    except (ValueError, TypeError) as exc:
        raise PilotManifestError(f"invalid typed reference field: {exc}") from exc


def _fields(rows: list, specs: dict[str, FieldSpec], text: str) -> dict:
    _require(isinstance(rows, list) and len(rows) == len(specs), "reference field coverage missing")
    found = {}
    for row in rows:
        _require(isinstance(row, dict), "reference field must be an object")
        key = row.get("field_key")
        _require(key in specs and key not in found, "unknown or duplicate reference field")
        _field(row, specs[key], text)
        found[key] = row
    return found


def _actors(ledger: dict, papers: set[str], protocol: str, codebook: str) -> tuple[dict, tuple]:
    actors = {}
    roles = []
    for row in ledger["actors"]:
        keys(row, {"id", "role", "qualification", "case_clearance"})
        ident = name(row["id"], "actor ID")
        _require(ident not in actors, "duplicate actor ID")
        _require(row["role"] in {"initial", "adjudicator"}, "invalid actor role")
        qualification = row["qualification"]
        keys(qualification, {"expertise", "protocol_version", "codebook_version", "signed_at"})
        nonempty(qualification["expertise"], "reviewer expertise")
        _require(
            qualification["protocol_version"] == protocol
            and qualification["codebook_version"] == codebook,
            "reviewer qualification does not approve frozen protocol/codebook",
        )
        timestamp(qualification["signed_at"], "qualification")
        clearances = {}
        for clearance in row["case_clearance"]:
            keys(clearance, {"paper_id", "conflict", "model_visible", "peer_visible", "checked_at"})
            paper_id = clearance["paper_id"]
            _require(paper_id in papers and paper_id not in clearances, "invalid case clearance")
            _require(
                clearance["conflict"] == "none"
                and clearance["model_visible"] is False
                and clearance["peer_visible"] is False,
                "case has conflict or prior result/peer exposure",
            )
            _later(clearance["checked_at"], qualification["signed_at"], "case clearance")
            clearances[paper_id] = clearance
        _require(set(clearances) == papers, "missing case conflict/blinding clearance")
        actors[ident] = row
        roles.append(row["role"])
    _require(
        Counter(roles) == {"initial": 2, "adjudicator": 1},
        "two initial reviewers and separate adjudicator required",
    )
    initial = tuple(sorted(ident for ident, row in actors.items() if row["role"] == "initial"))
    return actors, initial


def _stage_a(row: dict, paper: dict, specs: dict, actors: dict, initial: tuple, base: Path) -> str:
    keys(row, {"paper_id", "source_sha256", "input_sha256", "initial", "adjudications", "lock"})
    _require(
        row["source_sha256"] == paper["source"]["sha256"]
        and row["input_sha256"] == paper["effective_input"]["sha256"],
        "Stage A source/input hash mismatch",
    )
    text = artifact(
        base,
        paper["effective_input"]["artifact_path"],
        paper["effective_input"]["sha256"],
        text=True,
    ).decode("utf-8")
    _require(
        isinstance(row["initial"], list) and len(row["initial"]) == 2,
        "two initial judgments required",
    )
    originals = {}
    initial_times = []
    for review in row["initial"]:
        keys(review, {"actor_id", "locked_at", "fields"})
        actor_id = review["actor_id"]
        _require(
            actor_id in initial and actor_id not in originals,
            "missing or duplicate initial reviewer",
        )
        _later(
            review["locked_at"],
            actors[actor_id]["case_clearance_by_id"][row["paper_id"]]["checked_at"],
            "initial judgment",
        )
        originals[actor_id] = _fields(review["fields"], specs, text)
        initial_times.append(review["locked_at"])
    adjudicator = next(key for key, value in actors.items() if value["role"] == "adjudicator")
    disagreements = {
        key for key in specs if originals[initial[0]][key] != originals[initial[1]][key]
    }
    _require(isinstance(row["adjudications"], list), "adjudications must be a list")
    seen = set()
    for adjudication in row["adjudications"]:
        keys(
            adjudication,
            {"field_key", "actor_id", "judged_at", "original_sha256", "final", "reason"},
        )
        field_key = adjudication["field_key"]
        _require(
            field_key in disagreements and field_key not in seen,
            "adjudication must resolve a disagreement",
        )
        _require(adjudication["actor_id"] == adjudicator, "self-adjudication forbidden")
        _require(
            adjudication["original_sha256"]
            == digest([originals[actor][field_key] for actor in initial]),
            "adjudication does not bind both original judgments",
        )
        for before in initial_times:
            _later(adjudication["judged_at"], before, "Stage A adjudication")
        _later(
            adjudication["judged_at"],
            actors[adjudicator]["case_clearance_by_id"][row["paper_id"]]["checked_at"],
            "Stage A adjudication",
        )
        _field(adjudication["final"], specs[field_key], text)
        nonempty(adjudication["reason"], "adjudication reason")
        seen.add(field_key)
    _require(seen == disagreements, "unresolved disagreement lacks explicit adjudication")
    lock = row["lock"]
    keys(lock, {"at", "sha256"})
    _require(
        sha(lock["sha256"], "Stage A lock")
        == digest({k: v for k, v in row.items() if k != "lock"}),
        "Stage A lock hash mismatch",
    )
    for before in initial_times + [item["judged_at"] for item in row["adjudications"]]:
        _later(lock["at"], before, "Stage A lock")
    return lock["at"]


def _stage_b(
    row: dict,
    paper: dict,
    a: dict,
    attempt: dict,
    actors: dict,
    initial: tuple,
    specs: dict,
    text: str,
) -> None:
    keys(
        row,
        {
            "paper_id",
            "stage_a_lock_sha256",
            "input_sha256",
            "model_output_sha256",
            "links",
            "no_links_reason",
            "lock",
        },
    )
    _require(row["stage_a_lock_sha256"] == a["lock"]["sha256"], "Stage B has alien Stage A lock")
    _require(
        row["input_sha256"] == paper["effective_input"]["sha256"], "Stage B input hash mismatch"
    )
    expected_output = attempt["output_sha256"] if attempt["state"] == "declared_valid" else None
    _require(row["model_output_sha256"] == expected_output, "Stage B model output hash mismatch")
    _require(isinstance(row["links"], list), "Stage B links must be a list")
    _require(
        attempt["state"] == "declared_valid" or not row["links"],
        "failed model output has support links",
    )
    if not row["links"]:
        nonempty(row["no_links_reason"], "empty model-link reason")
    else:
        _require(row["no_links_reason"] is None, "links cannot have empty-link reason")
    seen = set()
    adjudicator = next(key for key, value in actors.items() if value["role"] == "adjudicator")
    for link in row["links"]:
        keys(
            link,
            {
                "link_id",
                "field_key",
                "answer_revision",
                "evidence_id",
                "quote",
                "match_state",
                "start",
                "end",
                "initial",
                "adjudication",
            },
        )
        link_id = name(link["link_id"], "link ID")
        _require(link_id not in seen, "duplicate model link")
        seen.add(link_id)
        _require(link["field_key"] in specs, "model link has unknown field")
        nonempty(link["answer_revision"], "answer revision")
        name(link["evidence_id"], "evidence ID")
        nonempty(link["quote"], "model quote")
        if link["match_state"] == "exact":
            _require(
                type(link["start"]) is int
                and type(link["end"]) is int
                and 0 <= link["start"] < link["end"] <= len(text)
                and text[link["start"] : link["end"]] == link["quote"],
                "Stage B exact match does not replay",
            )
        else:
            _require(
                link["match_state"] == "unverified"
                and link["start"] is None
                and link["end"] is None,
                "invalid separate match state",
            )
        _require(len(link["initial"]) == 2, "two independent Stage B judgments required")
        reviews = {}
        for review in link["initial"]:
            keys(review, {"actor_id", "judged_at", "support", "reason"})
            actor = review["actor_id"]
            _require(actor in initial and actor not in reviews, "invalid Stage B initial reviewer")
            _require(review["support"] in SUPPORT, "invalid independent support status")
            nonempty(review["reason"], "support reason")
            _later(review["judged_at"], a["lock"]["at"], "Stage B judgment")
            _later(review["judged_at"], attempt["recorded_at"], "Stage B judgment")
            reviews[actor] = review
        dispute = reviews[initial[0]]["support"] != reviews[initial[1]]["support"]
        resolution = link["adjudication"]
        _require(
            (resolution is not None) == dispute,
            "Stage B disagreement requires separate adjudication",
        )
        if resolution is not None:
            keys(resolution, {"actor_id", "judged_at", "support", "reason", "original_sha256"})
            _require(resolution["actor_id"] == adjudicator, "Stage B self-adjudication forbidden")
            _require(resolution["support"] in SUPPORT, "invalid adjudicated support status")
            nonempty(resolution["reason"], "Stage B adjudication reason")
            _require(
                resolution["original_sha256"] == digest([reviews[actor] for actor in initial]),
                "Stage B adjudication does not bind originals",
            )
            for review in reviews.values():
                _later(resolution["judged_at"], review["judged_at"], "Stage B adjudication")
        for before in [review["judged_at"] for review in reviews.values()] + (
            [resolution["judged_at"]] if resolution else []
        ):
            _later(row["lock"]["at"], before, "Stage B lock")
    keys(row["lock"], {"at", "sha256"})
    _require(
        sha(row["lock"]["sha256"], "Stage B lock")
        == digest({k: v for k, v in row.items() if k != "lock"}),
        "Stage B lock hash mismatch",
    )
    _later(row["lock"]["at"], a["lock"]["at"], "Stage B lock")
    _later(row["lock"]["at"], attempt["recorded_at"], "Stage B lock")


def validate_reference_ledger(ledger: dict, manifest: dict, base_dir: Path) -> dict:
    """Validate a complete claimed reference set against a frozen pilot manifest."""
    pilot = validate_pilot_manifest(manifest, base_dir)
    keys(
        ledger,
        {
            "schema_version",
            "state",
            "reference_set_id",
            "pilot_manifest_sha256",
            "protocol_version",
            "codebook_version",
            "codebook_approval",
            "actors",
            "stage_a",
            "stage_b",
        },
    )
    _require(ledger["schema_version"] == VERSION, "unsupported reference ledger version")
    _require(ledger["state"] in {"draft", "locked"}, "invalid reference ledger state")
    if ledger["state"] == "draft":
        _require(
            pilot["state"] == "draft"
            and ledger["reference_set_id"] is None
            and ledger["pilot_manifest_sha256"] is None
            and ledger["codebook_approval"] is None
            and ledger["actors"] == ledger["stage_a"] == ledger["stage_b"] == [],
            "draft ledger cannot claim independent references",
        )
        return {"state": "draft", "qualification": "pending"}
    _require(pilot["state"] == "frozen", "locked ledger requires frozen pilot manifest")
    freeze = manifest["freeze"]
    nonempty(freeze["reference_set_id"], "locked reference set ID")
    _require(
        not any(
            marker in freeze["codebook_version"].casefold()
            for marker in ("pending", "draft", "unapproved", "tbd")
        ),
        "locked ledger cannot use pending codebook version",
    )
    _require(
        ledger["pilot_manifest_sha256"] == digest(manifest)
        and ledger["reference_set_id"] == freeze["reference_set_id"]
        and ledger["protocol_version"] == manifest["protocol_version"]
        and ledger["codebook_version"] == freeze["codebook_version"],
        "reference ledger does not bind frozen pilot identity",
    )
    papers = {
        paper["id"]: paper
        for paper in manifest["papers"]
        if paper["status"] == "eligible" and paper["split"] == "held_out"
    }
    specs = {item["key"]: FieldSpec(item["key"], item["kind"]) for item in freeze["fields"]}
    _require(
        isinstance(ledger["actors"], list)
        and isinstance(ledger["stage_a"], list)
        and isinstance(ledger["stage_b"], list),
        "ledger records must be arrays",
    )
    actors, initial = _actors(
        ledger, set(papers), manifest["protocol_version"], freeze["codebook_version"]
    )
    approval = ledger["codebook_approval"]
    keys(approval, {"state", "approved_at", "approved_by", "approval_record_sha256"})
    _require(approval["state"] == "approved", "locked codebook approval missing")
    _require(
        isinstance(approval["approved_by"], list)
        and len(approval["approved_by"]) == len(actors)
        and set(approval["approved_by"]) == set(actors),
        "codebook approval must name all three qualified actors",
    )
    timestamp(approval["approved_at"], "codebook approval")
    sha(approval["approval_record_sha256"], "codebook approval record")
    for actor in actors.values():
        _later(actor["qualification"]["signed_at"], approval["approved_at"], "qualification")
    actors = {
        ident: {
            **actor,
            "case_clearance_by_id": {row["paper_id"]: row for row in actor["case_clearance"]},
        }
        for ident, actor in actors.items()
    }
    _require(
        len(ledger["stage_a"]) == len(papers) and len(ledger["stage_b"]) == len(papers),
        "every held-out paper needs both stages",
    )
    a_by_id = {}
    for row in ledger["stage_a"]:
        paper_id = row.get("paper_id")
        _require(
            paper_id in papers and paper_id not in a_by_id, "Stage A paper is unknown/duplicate"
        )
        _stage_a(row, papers[paper_id], specs, actors, initial, Path(base_dir))
        a_by_id[paper_id] = row
    _require(set(a_by_id) == set(papers), "Stage A misses held-out paper")
    global_stage_a_lock = max(
        (row["lock"]["at"] for row in a_by_id.values()),
        key=lambda value: timestamp(value, "Stage A lock"),
    )
    for attempt in manifest["attempts"]:
        _later(attempt["recorded_at"], global_stage_a_lock, "held-out model attempt")
    attempts = {row["paper_id"]: row for row in manifest["attempts"] if row["selected"]}
    b_seen = set()
    for row in ledger["stage_b"]:
        paper_id = row.get("paper_id")
        _require(
            paper_id in papers and paper_id not in b_seen, "Stage B paper is unknown/duplicate"
        )
        b_seen.add(paper_id)
        a = a_by_id[paper_id]
        _later(row["lock"]["at"], global_stage_a_lock, "Stage B lock")
        text = artifact(
            Path(base_dir),
            papers[paper_id]["effective_input"]["artifact_path"],
            papers[paper_id]["effective_input"]["sha256"],
            text=True,
        ).decode("utf-8")
        _stage_b(row, papers[paper_id], a, attempts[paper_id], actors, initial, specs, text)
    return {
        "state": "structurally_valid_unsigned",
        "held_out": len(papers),
        "stage_a": len(a_by_id),
        "stage_b": len(b_seen),
        "reference_set_id": ledger["reference_set_id"],
        "ledger_sha256": digest(ledger),
        "qualification": "externally_unverified",
    }


def _strict_json(raw: bytes):
    _require(len(raw) <= MAX_BYTES, "reference ledger exceeds byte bound")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "duplicate reference ledger JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise PilotManifestError(f"nonfinite reference ledger JSON value: {value}")

    def finite_float(value):
        number = float(value)
        _require(math.isfinite(number), "nonfinite reference ledger JSON number")
        return number

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique_pairs,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an unsigned SEIP independent-reference ledger"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("ledger", type=Path)
    args = parser.parse_args(argv)
    try:
        raw_manifest = args.manifest.read_bytes()
        validate_pilot_manifest_bytes(raw_manifest, args.manifest.parent)
        raw = args.ledger.read_bytes()
        manifest = json.loads(raw_manifest)
        ledger = _strict_json(raw)
        result = validate_reference_ledger(ledger, manifest, args.manifest.parent)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        print(f"reference ledger validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
