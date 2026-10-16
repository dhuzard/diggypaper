"""Synthetic ledger fixtures verify protocol mechanics, not scientific results."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.seip_pilot_manifest import (
    PilotManifestError,
    digest,
    main,
    scorer_manifest,
    validate_pilot_manifest,
    validate_pilot_manifest_bytes,
)

EXAMPLE = Path(__file__).parents[3] / "docs/examples/seip-pilot-manifest-draft-v1.json"


def write_artifact(base, path, data):
    target = base / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def frozen_manifest(base):
    prompt_sha = write_artifact(
        base, "prompt.txt", b"synthetic prompt; examples declared separately"
    )
    papers = []
    for index in range(30):
        paper_id = f"synthetic-{index + 1:02d}"
        split = "development" if index < 6 else "held_out"
        stratum = ("jats", "pdf", "abstract")[(index - 6) // 8] if index >= 6 else "jats"
        source_path = f"source/{paper_id}.txt"
        input_path = f"input/{paper_id}.txt"
        source_hash = write_artifact(base, source_path, f"synthetic source {paper_id}".encode())
        input_hash = write_artifact(base, input_path, f"synthetic input {paper_id}".encode())
        papers.append(
            {
                "id": paper_id,
                "study_key": paper_id,
                "aliases": [{"kind": "doi", "value": f"10.9999/{paper_id}"}],
                "status": "eligible",
                "split": split,
                "stratum": stratum,
                "selection_reason": "synthetic engineering fixture",
                "exclusion_reason": None,
                "duplicate_of": None,
                "source": {
                    "url": f"https://example.invalid/{paper_id}",
                    "retrieved_at": "2026-01-01T00:00:00Z",
                    "type": stratum,
                    "license": "synthetic fixture",
                    "access": "permitted_local",
                    "artifact_path": source_path,
                    "sha256": source_hash,
                },
                "effective_input": {"artifact_path": input_path, "sha256": input_hash},
            }
        )
    config = {
        "model_identifier": "synthetic-model",
        "model_revision": None,
        "prompt_id": "synthetic-prompt",
        "prompt_version": "v1",
        "prompt_sha256": prompt_sha,
        "output_schema_version": "synthetic-fields-v1",
        "software_commit": "a" * 40,
        "decoding": {"temperature": 0.0},
        "input_policy": "synthetic effective input file",
    }
    manifest = {
        "schema_version": "seip-pilot-manifest-v1",
        "state": "frozen",
        "protocol_version": "seip-qualification-v0.1",
        "selection_rule": "synthetic index order; never a real selection rule",
        "seed": 17,
        "papers": papers,
        "freeze": {
            "frozen_at": "2026-02-01T00:00:00Z",
            "cohort_sha256": "",
            "configuration": config,
            "configuration_sha256": digest(config),
            "prompt_artifact_path": "prompt.txt",
            "prompt_example_paper_ids": ["synthetic-01"],
            "fields": [{"key": "species", "kind": "categorical"}],
            "codebook_version": "synthetic-codebook-v1",
            "reference_set_id": "synthetic-locked-references",
            "retry_rule": "first_attempt",
            "attempt_manifest_id": "synthetic-attempts-v1",
        },
        "attempts": [],
    }
    refreeze(manifest)
    states = ["declared_valid", "invalid_json", "invalid_schema", "error", "missing_call"]
    for index, paper in enumerate(papers[6:]):
        state = states[index % len(states)]
        output_path = f"outputs/{paper['id']}.json" if state in states[:3] else None
        output_hash = write_artifact(base, output_path, b"{}") if output_path else None
        manifest["attempts"].append(
            {
                "paper_id": paper["id"],
                "index": 1,
                "recorded_at": "2026-03-01T00:00:00Z",
                "state": state,
                "invocation_id": None if state == "missing_call" else f"invocation-{index}",
                "input_sha256": paper["effective_input"]["sha256"],
                "configuration_sha256": manifest["freeze"]["configuration_sha256"],
                "output_path": output_path,
                "output_sha256": output_hash,
                "error_code": "synthetic_error" if state == "error" else None,
                "selected": True,
            }
        )
    return manifest


def refreeze(manifest):
    manifest["freeze"]["cohort_sha256"] = digest(
        {
            "protocol_version": manifest["protocol_version"],
            "selection_rule": manifest["selection_rule"],
            "seed": manifest["seed"],
            "papers": sorted(manifest["papers"], key=lambda item: item["id"]),
        }
    )


def test_draft_example_contains_no_papers_or_results(capsys):
    raw = EXAMPLE.read_bytes()
    assert validate_pilot_manifest_bytes(raw, EXAMPLE.parent) == {
        "state": "draft",
        "candidates": 0,
        "eligible": 0,
        "source_claim_only": [],
    }
    assert main([str(EXAMPLE)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "draft"


def test_frozen_30_paper_ledger_and_scorer_adapter(tmp_path):
    manifest = frozen_manifest(tmp_path)
    report = validate_pilot_manifest(manifest, tmp_path)
    assert (report["development"], report["held_out"], report["attempts"]) == (6, 24, 24)
    assert report["held_out_strata"] == {"abstract": 8, "jats": 8, "pdf": 8}
    assert report["qualification"] == "not_assessed"
    adapted = scorer_manifest(manifest, tmp_path)
    assert len(adapted.papers) == 30
    assert adapted.attempt_manifest_sha256 == report["attempt_manifest_sha256"]
    assert sum(p.split == "held_out" for p in adapted.papers) == 24


def test_retained_retry_follows_predeclared_choice_rule(tmp_path):
    manifest = frozen_manifest(tmp_path)
    first = manifest["attempts"][0]
    first["state"] = "invalid_json"
    retry = copy.deepcopy(first)
    retry.update(index=2, state="declared_valid", invocation_id="invocation-retry", selected=False)
    manifest["attempts"].append(retry)
    assert validate_pilot_manifest(manifest, tmp_path)["attempts"] == 25
    manifest["freeze"]["retry_rule"] = "first_valid_else_last"
    with pytest.raises(PilotManifestError, match="unverified validity"):
        validate_pilot_manifest(manifest, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        "wrong_split_counts",
        "duplicate_alias",
        "duplicate_study",
        "heldout_prompt_example",
        "input_hash_drift",
        "configuration_drift",
        "missing_attempt",
        "wrong_retry_selection",
        "unresolved_candidate",
        "attempt_before_freeze",
    ],
)
def test_rejects_leakage_counts_drift_and_incomplete_attempts(tmp_path, change):
    manifest = frozen_manifest(tmp_path)
    if change == "wrong_split_counts":
        manifest["papers"][6]["split"] = "development"
        manifest["attempts"].pop(0)
        refreeze(manifest)
    elif change == "duplicate_alias":
        manifest["papers"][7]["aliases"] = copy.deepcopy(manifest["papers"][6]["aliases"])
        refreeze(manifest)
    elif change == "duplicate_study":
        manifest["papers"][7]["study_key"] = manifest["papers"][6]["study_key"]
        refreeze(manifest)
    elif change == "heldout_prompt_example":
        manifest["freeze"]["prompt_example_paper_ids"].append("synthetic-07")
    elif change == "input_hash_drift":
        manifest["attempts"][0]["input_sha256"] = "0" * 64
    elif change == "configuration_drift":
        manifest["attempts"][0]["configuration_sha256"] = "0" * 64
    elif change == "missing_attempt":
        manifest["attempts"].pop()
    elif change == "wrong_retry_selection":
        extra = copy.deepcopy(manifest["attempts"][0])
        extra["index"] = 2
        extra["invocation_id"] = "invocation-retry"
        extra["selected"] = True
        manifest["attempts"][0]["selected"] = False
        manifest["attempts"].append(extra)
    elif change == "unresolved_candidate":
        candidate = copy.deepcopy(manifest["papers"][0])
        candidate.update(
            id="synthetic-unresolved",
            study_key="synthetic-unresolved",
            aliases=[],
            status="candidate",
            split=None,
            stratum=None,
            source=None,
            effective_input=None,
        )
        manifest["papers"].append(candidate)
        refreeze(manifest)
    elif change == "attempt_before_freeze":
        manifest["attempts"][0]["recorded_at"] = "2026-01-01T00:00:00Z"
    with pytest.raises(PilotManifestError):
        validate_pilot_manifest(manifest, tmp_path)


@pytest.mark.parametrize("kind", ["source", "input", "prompt", "output", "path_escape"])
def test_rejects_changed_or_escaped_artifacts(tmp_path, kind):
    manifest = frozen_manifest(tmp_path)
    if kind == "source":
        (tmp_path / manifest["papers"][0]["source"]["artifact_path"]).write_text("changed")
    elif kind == "input":
        (tmp_path / manifest["papers"][0]["effective_input"]["artifact_path"]).write_text("changed")
    elif kind == "prompt":
        (tmp_path / "prompt.txt").write_text("changed")
    elif kind == "output":
        (tmp_path / manifest["attempts"][0]["output_path"]).write_text("changed")
    else:
        manifest["papers"][0]["effective_input"]["artifact_path"] = "../outside.txt"
        refreeze(manifest)
    with pytest.raises(PilotManifestError):
        validate_pilot_manifest(manifest, tmp_path)


def test_excluded_duplicate_candidate_remains_visible(tmp_path):
    manifest = frozen_manifest(tmp_path)
    excluded = copy.deepcopy(manifest["papers"][0])
    excluded.update(
        id="synthetic-excluded-version",
        status="excluded",
        split=None,
        stratum=None,
        source=None,
        effective_input=None,
        exclusion_reason="duplicate version",
        duplicate_of="synthetic-01",
    )
    manifest["papers"].append(excluded)
    refreeze(manifest)
    assert validate_pilot_manifest(manifest, tmp_path)["excluded"] == 1


def test_excluded_duplicate_cannot_bridge_development_and_held_out(tmp_path):
    manifest = frozen_manifest(tmp_path)
    excluded = copy.deepcopy(manifest["papers"][0])
    excluded.update(
        id="synthetic-bridge",
        status="excluded",
        split=None,
        stratum=None,
        source=None,
        effective_input=None,
        exclusion_reason="candidate bridge",
        duplicate_of="synthetic-01",
        aliases=copy.deepcopy(manifest["papers"][6]["aliases"]),
    )
    manifest["papers"].append(excluded)
    refreeze(manifest)
    with pytest.raises(PilotManifestError, match="bridges eligible"):
        validate_pilot_manifest(manifest, tmp_path)


@pytest.mark.parametrize(
    "raw", [b"not JSON", b"[]", b'{"x":NaN}', b'{"x":1e999}', b'{"x":1,"x":2}']
)
def test_declared_valid_requires_strict_json_object_but_not_schema(tmp_path, raw):
    manifest = frozen_manifest(tmp_path)
    row = manifest["attempts"][0]
    row["output_sha256"] = write_artifact(tmp_path, row["output_path"], raw)
    with pytest.raises(PilotManifestError):
        validate_pilot_manifest(manifest, tmp_path)
    row["output_sha256"] = write_artifact(tmp_path, row["output_path"], b'{"wrong_field":true}')
    assert validate_pilot_manifest(manifest, tmp_path)["output_schema_validation"] == "unverified"


def test_fifo_artifact_is_rejected_before_read(tmp_path):
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO requires POSIX")
    manifest = frozen_manifest(tmp_path)
    path = tmp_path / "fifo.txt"
    os.mkfifo(path)
    manifest["papers"][0]["effective_input"]["artifact_path"] = "fifo.txt"
    refreeze(manifest)
    with pytest.raises(PilotManifestError, match="regular file"):
        validate_pilot_manifest(manifest, tmp_path)


def test_source_without_retained_artifact_is_reported_as_claim_only(tmp_path):
    manifest = frozen_manifest(tmp_path)
    manifest["papers"][0]["source"]["artifact_path"] = None
    refreeze(manifest)
    assert validate_pilot_manifest(manifest, tmp_path)["source_claim_only"] == ["synthetic-01"]


def test_stratum_deviation_requires_explicit_reason(tmp_path):
    manifest = frozen_manifest(tmp_path)
    manifest["papers"][6]["stratum"] = "pdf"
    manifest["papers"][6]["source"]["type"] = "pdf"
    refreeze(manifest)
    with pytest.raises(PilotManifestError, match="deviation reason"):
        validate_pilot_manifest(manifest, tmp_path)
    manifest["freeze"]["stratum_deviation_reason"] = "synthetic recruitment shortfall"
    assert validate_pilot_manifest(manifest, tmp_path)["held_out_strata"]["pdf"] == 9


@pytest.mark.parametrize(
    "raw",
    [
        b'{"state":"draft","state":"frozen"}',
        b'{"seed":NaN}',
        b'{"seed":1e999}',
        b"[]",
        '{"state":"draft"}'.encode("utf-16"),
    ],
)
def test_strict_json_rejects_ambiguous_or_non_object_manifest(raw, tmp_path):
    with pytest.raises(PilotManifestError):
        validate_pilot_manifest_bytes(raw, tmp_path)


def test_adapter_requires_reference_set_identity(tmp_path):
    manifest = frozen_manifest(tmp_path)
    manifest["freeze"]["reference_set_id"] = None
    assert validate_pilot_manifest(manifest, tmp_path)["state"] == "frozen"
    with pytest.raises(PilotManifestError, match="reference set"):
        scorer_manifest(manifest, tmp_path)
