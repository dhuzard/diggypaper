"""Tests for the source-evidence-packet/0.1.0 contract validator (docs/contracts)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "contracts" / "source-evidence-packet" / "v0.1.0"
VALIDATOR_PATH = CONTRACT / "validator" / "sep_validate.py"
CANONICAL = CONTRACT / "fixtures" / "valid" / "canonical"
INVALID_DIR = CONTRACT / "fixtures" / "invalid"
INVALID_CASES = sorted(p.name for p in INVALID_DIR.iterdir() if p.is_dir())
JSONL_FILES = ["papers.jsonl", "findings.jsonl", "passages.jsonl", "assessments.jsonl"]


def _load_validator():
    spec = importlib.util.spec_from_file_location("sep_validate", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sep_validate"] = module
    spec.loader.exec_module(module)
    return module


sep = _load_validator()


def _canonical_line(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_invalid_fixture_cases_exist():
    assert INVALID_CASES, "no invalid fixtures found"


def test_canonical_fixture_is_valid():
    result = sep.validate_packet(CANONICAL, "for-import")
    assert result["errors"] == []
    assert result["valid"] is True
    counts = result["counts"]
    assert counts["papers"] >= 2
    assert counts["findings"] >= 3
    assert counts["passages"] >= 4
    assert counts["assessments"] >= 3


@pytest.mark.parametrize("case", INVALID_CASES)
def test_invalid_fixture_reports_expected_codes(case):
    case_dir = INVALID_DIR / case
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))["errors"]
    assert expected, f"{case}: expected.json lists no error codes"
    result = sep.validate_packet(case_dir, "for-import")
    assert result["valid"] is False
    got = {e["code"] for e in result["errors"]}
    missing = [code for code in expected if code not in got]
    assert not missing, f"{case}: missing {missing}; got {sorted(got)}"


def test_every_invalid_fixture_is_unique_and_has_expected_json():
    for case in INVALID_CASES:
        assert (INVALID_DIR / case / "expected.json").is_file(), case


def test_determinism_two_runs_byte_identical():
    for packet in [CANONICAL, *(INVALID_DIR / c for c in INVALID_CASES)]:
        a = json.dumps(sep.validate_packet(packet, "for-import"), sort_keys=True)
        b = json.dumps(sep.validate_packet(packet, "for-import"), sort_keys=True)
        assert a == b, packet.name


def test_cli_output_byte_identical_and_exit_codes():
    def run(*args):
        return subprocess.run(
            [sys.executable, str(VALIDATOR_PATH), *args], capture_output=True, check=False
        )

    first, second = run(str(CANONICAL)), run(str(CANONICAL))
    assert first.returncode == 0
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["valid"] is True
    bad = run(str(INVALID_DIR / "revision-gap"))
    assert bad.returncode == 1
    assert json.loads(bad.stdout)["valid"] is False
    assert run().returncode == 2
    assert run(str(CANONICAL / "does-not-exist")).returncode == 2
    assert run(str(CANONICAL), "--for-import", "--allow-restricted").returncode == 2


def test_errors_sorted_by_code_file_id():
    result = sep.validate_packet(INVALID_DIR / "schema-invalid", "for-import")
    keys = [(e["code"], e["file"], e["id"]) for e in result["errors"]]
    assert keys == sorted(keys)


@pytest.mark.parametrize("case", ["private-packet", "embargoed-packet"])
def test_restricted_packet_modes(case):
    strict = sep.validate_packet(INVALID_DIR / case, "for-import")
    assert "private-packet-not-exportable" in {e["code"] for e in strict["errors"]}
    relaxed = sep.validate_packet(INVALID_DIR / case, "allow-restricted")
    assert "private-packet-not-exportable" not in {e["code"] for e in relaxed["errors"]}
    assert "private-packet-not-exportable" in {w["code"] for w in relaxed["warnings"]}


def test_mapping_report_flags_incompatible_and_restricted():
    result = sep.validate_packet(CANONICAL, "for-import", with_mapping_report=True)
    report = result["mapping_report"]
    rows = {(r["finding_id"], r["revision"]): r for r in report["findings"]}
    assert len(rows) == result["counts"]["findings"]
    assert rows[("finding:synth-a-f1", 1)]["outcome"] == "importable"
    f2 = rows[("finding:synth-a-f2", 1)]
    assert f2["outcome"] == "incompatible"
    assert {"unreviewed", "proposed"} <= set(f2["incompatible_reasons"])
    assert any(r["restricted_reasons"] for r in report["findings"])
    assert report["withheld_quote_passages"]
    assert "mapping_report" not in sep.validate_packet(CANONICAL, "for-import")


def test_round_trip_loses_nothing(tmp_path):
    out = tmp_path / "packet"
    out.mkdir()
    manifest = json.loads((CANONICAL / "manifest.json").read_text(encoding="utf-8"))
    for name in JSONL_FILES:
        raw = (CANONICAL / name).read_text(encoding="utf-8")
        records = [json.loads(line) for line in raw.split("\n")[:-1]]
        assert len(records) == manifest["artifacts"][name.removesuffix(".jsonl")]["record_count"]
        rewritten = "".join(_canonical_line(r) + "\n" for r in records)
        assert rewritten == raw, f"{name}: re-serialization differs"
        # reparse equals original: no field dropped or altered
        assert [json.loads(line) for line in rewritten.split("\n")[:-1]] == records
        (out / name).write_bytes(rewritten.encode("utf-8"))
    shutil.copyfile(CANONICAL / "manifest.json", out / "manifest.json")
    assert sep.validate_packet(out, "for-import") == sep.validate_packet(CANONICAL, "for-import")
    assert sep.validate_packet(out, "for-import")["valid"] is True
    # manifest re-serialization is stable too
    raw_manifest = (CANONICAL / "manifest.json").read_text(encoding="utf-8")
    assert json.loads(raw_manifest) == manifest


# --- packet digest (README section 2.1) -------------------------------------------------------


def _copy_canonical(tmp_path):
    out = tmp_path / "packet"
    shutil.copytree(CANONICAL, out)
    return out


def test_packet_digest_is_sha256_of_exact_manifest_bytes():
    import hashlib

    result = sep.validate_packet(CANONICAL, "for-import")
    expected = "sha256:" + hashlib.sha256((CANONICAL / "manifest.json").read_bytes()).hexdigest()
    assert result["packet_digest"] == expected


@pytest.mark.parametrize("case", INVALID_CASES)
def test_invalid_packets_have_no_packet_digest(case):
    assert "packet_digest" not in sep.validate_packet(INVALID_DIR / case, "for-import")


def test_same_manifest_content_with_different_bytes_is_a_different_packet(tmp_path):
    out = _copy_canonical(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    # Indent 4 is also canonical, so this is a valid packet with identical JSON content.
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=4, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    original = sep.validate_packet(CANONICAL, "for-import")
    rewritten = sep.validate_packet(out, "for-import")
    assert rewritten["valid"] is True
    assert rewritten["packet_digest"] != original["packet_digest"]


def test_artifact_tampering_removes_the_digest(tmp_path):
    out = _copy_canonical(tmp_path)
    passages = out / "passages.jsonl"
    lines = passages.read_text(encoding="utf-8").split("\n")
    first = json.loads(lines[0])
    first["producer_ids"]["evidence_ref"] += "-tampered"  # canonical, but not the recorded bytes
    lines[0] = _canonical_line(first)
    passages.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    result = sep.validate_packet(out, "for-import")
    assert result["valid"] is False
    assert "artifact-hash-mismatch" in {e["code"] for e in result["errors"]}
    assert "packet_digest" not in result


def test_cli_reports_packet_digest(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-I", str(VALIDATOR_PATH), str(CANONICAL)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["packet_digest"].startswith("sha256:")


@pytest.mark.parametrize("commit", ["unknown", "", "C" * 40])
def test_producer_commit_must_be_40_hex_or_synthetic_fixture(tmp_path, commit):
    # Frozen in #47: consumers pin this schema, so "unknown" is never a valid commit.
    out = _copy_canonical(tmp_path)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["producer"]["commit"] = commit
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result = sep.validate_packet(out, "for-import")
    assert result["valid"] is False
    assert "schema-invalid:manifest.json" in {e["code"] for e in result["errors"]}
