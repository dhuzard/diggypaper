"""NTS pipeline on SYNTHETIC articles: links, packets, rights, review state, idempotence, CRT."""

# ruff: noqa: E501  (long synthetic sentences)

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from nts_support import (
    ANIMALS,
    COMMIT,
    NC,
    OPEN,
    UNKNOWN,
    section,
    source,
    write_csv,
    write_study,
)

from app.nts import packets
from app.nts.cli import _validate, _write_packet
from app.nts.pipeline import BLOCKED, PASSED, gate0_verdict, run_study
from app.nts.source import freeze, load_source, match_receipt

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "docs/contracts/source-evidence-packet/v0.1.0/validator/sep_validate.py"
_spec = importlib.util.spec_from_file_location("sep_validate_nts_test", VALIDATOR)
sep = importlib.util.module_from_spec(_spec)
sys.modules["sep_validate_nts_test"] = sep
_spec.loader.exec_module(sep)
CRT = os.environ.get("CRT_CHECKOUT")


def _run(tmp_path, sources, alures_rows=None, extra_case=None):
    cfg = write_study(tmp_path, sources, extra_case)
    alures_dir = None
    if alures_rows is not None:
        alures_dir = tmp_path / "alures"
        alures_dir.mkdir()
        write_csv(
            alures_dir / "synthetic_nts_projects.csv", ["NTS ID", "Version", "Country"], alures_rows
        )
    return run_study(cfg, tmp_path, alures_dir, COMMIT, None)


def _nts(res):
    return [lk for lk in res["links"] if lk["identifier_system"] == "ALURES-NTS"]


def test_exact_match_against_synthetic_alures_is_a_verified_pair(tmp_path):
    res = _run(tmp_path, {"a": source("a", [ANIMALS])}, [["NTS-ES-111111", "2", "Spain"]])
    (lk,) = _nts(res)
    assert (lk["link_state"], lk["alures_state"]) == ("VERIFIED_PAIR", "confirmed-exact")
    assert lk["receipt_status"] == "exact" and lk["alures_row_numbers"] == [[2]]
    assert gate0_verdict(res["links"]) == PASSED


def test_no_alures_dataset_is_blocked_not_verified(tmp_path):
    res = _run(tmp_path, {"a": source("a", [ANIMALS])})
    (lk,) = _nts(res)
    assert lk["link_state"] == "ARTICLE_CONFIRMED_ALURES_UNAVAILABLE"
    assert gate0_verdict(res["links"]) == BLOCKED


def test_id_absent_from_alures_is_reported_not_fabricated(tmp_path):
    res = _run(tmp_path, {"a": source("a", [ANIMALS])}, [["NTS-ES-999999", "1", "Spain"]])
    (lk,) = _nts(res)
    assert (
        lk["link_state"] == "ARTICLE_CONFIRMED_ID_ABSENT_IN_ALURES"
        and lk["alures_row_numbers"] == []
    )
    assert gate0_verdict(res["links"]) == BLOCKED


def test_amended_nts_is_not_silently_a_clean_match(tmp_path):
    res = _run(
        tmp_path,
        {"a": source("a", [section("Animals", "Approved as NTS-ES-111111 v.1, 3 May 2025.")])},
        [["NTS-ES-111111", "1", "Spain"], ["NTS-ES-111111", "3", "Spain"]],
    )
    (lk,) = _nts(res)
    assert lk["alures_state"] == "confirmed-exact-amended" and lk["alures_current_version"] == "3"
    assert lk["version_cited"] == "1"


def test_expected_id_mismatch_blocks_verification(tmp_path):
    res = _run(
        tmp_path,
        {"a": source("a", [ANIMALS])},
        [["NTS-ES-111111", "2", "Spain"]],
        extra_case={"expected_nts_id": "NTS-ES-222222"},
    )
    (lk,) = _nts(res)
    assert lk["article_id_matches_expected"] is False
    assert not lk["link_state"].startswith("VERIFIED")


def test_missing_nts_id_gives_explicit_no_id_row_and_missing_counts(tmp_path):
    res = _run(
        tmp_path, {"c": source("c", [section("Ethics", "Registered under APAFIS#1234-5678.")])}
    )
    (lk,) = _nts(res)
    assert lk["link_state"] == "NO_NTS_ID_REPORTED"
    auth = [x for x in res["links"] if x["identifier_system"].startswith("national")]
    assert (
        auth[0]["link_state"] == "NO_NTS_CROSSWALK" and auth[0]["alures_state"] == "not-applicable"
    )
    na = [o for o in res["observations"] if o["rule_id"] == "none-found"]
    assert na and na[0]["number"] == "not reported"


def test_ambiguous_national_number_is_never_joined_to_alures(tmp_path):
    text = (
        "The project authorization was issued by the National Authority (406/29 April 2024). "
        "The ALURES code is NTS-RO-406074 v.1, 8 October 2025."
    )
    res = _run(
        tmp_path,
        {"b": source("b", [section("Animals", text)])},
        [["NTS-RO-406074", "1", "Romania"]],
    )
    auth = [x for x in res["links"] if x["identifier_system"].startswith("national")]
    assert [x["identifier_normalized"] for x in auth] == ["406/29 April 2024"]
    assert all(x["link_state"] == "NO_NTS_CROSSWALK" for x in auth)
    assert _nts(res)[0]["identifier_normalized"] == "NTS-RO-406074"


def test_same_project_multiple_publications_share_one_planned_number(tmp_path):
    srcs = {"a": source("a", [ANIMALS]), "b": source("b", [ANIMALS])}
    res = _run(tmp_path, srcs, [["NTS-ES-111111", "2", "Spain"]])
    by_case = {lk["case_id"]: lk for lk in _nts(res)}
    assert by_case["a"]["shared_with_other_articles_in_run"] == ["b"]
    assert by_case["b"]["shared_with_other_articles_in_run"] == ["a"]
    assert all(
        "also cited by other publications" in f for c in res["comparisons"] for f in c["flags"]
    )


def test_mixed_authorization_paper_scopes_the_nts(tmp_path):
    text = (
        "Pax6 null mice experiments were performed under the Project Licence PPL PA03F7F07 in the UK. "
        "Db/db mice procedures were approved (ALURES #nts-es-111111) in Spain."
    )
    res = _run(
        tmp_path, {"m": source("m", [section("Animals", text)])}, [["NTS-ES-111111", "1", "Spain"]]
    )
    (lk,) = _nts(res)
    assert lk["applies_to"] == "Db/db mice" and lk["excluded_from_this_nts"] == ["Pax6 null mice"]
    uk = [x for x in res["links"] if x["identifier_system"] == "national-authorization:uk-ppl"]
    assert uk and uk[0]["country"] == "GB" and uk[0]["link_state"] == "NO_NTS_CROSSWALK"


def test_comparison_never_computes_a_ratio_or_equates_ceiling_with_use(tmp_path):
    res = _run(tmp_path, {"a": source("a", [ANIMALS])}, [["NTS-ES-111111", "2", "Spain"]])
    (cmp,) = res["comparisons"]
    assert cmp["ratio_computed"] is False and cmp["unique_animal_matching_established"] is False
    assert cmp["planned"] == "not reported"  # no species table supplied: not guessed
    assert cmp["article_reported_total"] == 40


def test_overlapping_cohorts_are_flagged_and_not_summed(tmp_path):
    text = (
        "Rats received three doses (2 males and 2 females per dose). The 0.104 mg/kg group "
        "(dose selected for the main study) was expanded with 6 additional males and 6 females."
    )
    res = _run(tmp_path, {"a": source("a", [section("Protocol", text)])})
    assert any(o["possible_overlap"] for o in res["observations"] if o["source_kind"] == "article")
    (p,) = [x for x in res["cases"][0]["ex"]["proposals"] if x.kind == "animal-counts"]
    assert p.content["numeric_results"]  # exact numbers, with explicit n definitions
    assert all(
        n["n"]["definition"] in {"group", "unknown", "article-level"}
        for n in p.content["numeric_results"]
    )


def test_ambiguous_passage_stays_ambiguous_unless_it_can_be_made_unique(tmp_path):
    same = "Values are reported (two females + two males per group; total n = 4)."
    twice = [section("One", same), section("Two", "Other text first. " + same)]
    res = _run(tmp_path, {"a": source("a", twice)})
    by_section = {p.section_index: p for p in res["cases"][0]["ex"]["proposals"]}
    assert by_section[0].receipt["status"] == "ambiguous"  # cannot be disambiguated: stays so
    assert by_section[0].receipt["start"] is None
    assert by_section[1].receipt["status"] == "exact"  # grown by one sentence to be unique
    _write_packet(tmp_path / "pk", dict(res["exploratory"].files))
    assert sep.validate_packet(tmp_path / "pk")["valid"]


def test_corrupted_quotation_receipt_is_not_exact(tmp_path):
    frozen = freeze(source("a", [ANIMALS]), packets.__dict__ and __import__("uuid").UUID(int=1))
    r = match_receipt(frozen, "A total of 41 rats were used.")
    assert r["status"] == "not_found" and r["start"] is None


def test_corrupted_receipt_yields_valid_packet_with_null_offsets(tmp_path):
    res = _run(tmp_path, {"a": source("a", [ANIMALS])})
    frozen, proposals = res["frozen_cases"][0]
    bad = copy.deepcopy(proposals)
    bad[0].receipt.update(status="not_found", start=None, end=None, view_ref=None)
    rights = {str(frozen.snapshot.article_id): res["cases"][0]["rights"]}
    pk = packets.build_exploratory_packet([(frozen, bad)], COMMIT, rights)
    _write_packet(tmp_path / "pk", dict(pk.files))
    assert sep.validate_packet(tmp_path / "pk")["valid"]
    passages = [json.loads(x) for x in (tmp_path / "pk/passages.jsonl").read_text().splitlines()]
    assert any(
        p["match"]["status"] == "not_found" and p["source"]["start"] is None for p in passages
    )


def test_unreviewed_findings_are_withheld_by_the_unchanged_production_exporter(tmp_path):
    res = _run(tmp_path, {"a": source("a", [ANIMALS])})
    rep = res["production"].report
    assert rep["state"] == "withheld" and rep["exported"] == 0
    assert rep["omitted_by_reason"] == {"not-approved": len(res["cases"][0]["ex"]["proposals"])}
    assert all(r["review_state"] == "HUMAN_REVIEW_PENDING" for r in res["queue"])
    assert all(
        r["proposed_judgment"]["label"].startswith("AUTOMATION PROPOSAL") for r in res["queue"]
    )


@pytest.mark.parametrize(
    ("licence", "expected_state", "expected_rights"),
    [
        (OPEN, "included", "allowed"),
        (UNKNOWN, "withheld-rights", "metadata-only"),
        (NC, "withheld-rights", "metadata-only"),
    ],
)
def test_rights_unknown_restricted_and_permitted(
    tmp_path, licence, expected_state, expected_rights
):
    res = _run(tmp_path, {"a": source("a", [ANIMALS], license=licence)})
    _write_packet(tmp_path / "pk", dict(res["exploratory"].files))
    passages = [json.loads(x) for x in (tmp_path / "pk/passages.jsonl").read_text().splitlines()]
    assert {p["quote_state"] for p in passages} == {expected_state}
    if expected_state != "included":
        assert all(p["quote"] == "withheld" and len(p["quote_sha256"]) == 71 for p in passages)
    papers = [json.loads(x) for x in (tmp_path / "pk/papers.jsonl").read_text().splitlines()]
    assert papers[0]["rights"]["redistribution"] == expected_rights
    assert all(r["quote"] is None for r in res["queue"]) == (expected_state != "included")
    assert sep.validate_packet(tmp_path / "pk")["valid"]


def test_duplicate_import_is_idempotent(tmp_path):
    srcs = {"a": source("a", [ANIMALS])}
    one = _run(tmp_path, srcs)
    two = _run(tmp_path, srcs)
    assert one["exploratory"].files == two["exploratory"].files
    assert one["production"].files == two["production"].files
    assert [lk["link_id"] for lk in one["links"]] == [lk["link_id"] for lk in two["links"]]
    ids = [p.finding_id for p in one["cases"][0]["ex"]["proposals"]]
    assert len(ids) == len(set(ids))


def test_preprint_declared_against_a_run_article_is_deduplicated(tmp_path):
    a, b = source("a", [ANIMALS], doi="10.5555/journal"), source("b", [ANIMALS], doi="10.5555/pre")
    cfg = write_study(tmp_path, {"a": a, "b": b})
    cfg["cases"][1]["relations"] = [{"type": "preprint-of", "doi": "10.5555/journal"}]
    res = run_study(cfg, tmp_path, None, COMMIT, None)
    rows = {r["case_id"]: r for r in res["deduplication"]}
    assert rows["a"]["independent_output"] and not rows["b"]["independent_output"]
    assert rows["b"]["status"] == "duplicate-of:a"


def test_exploratory_packet_is_valid_but_distinct_from_reviewed(tmp_path):
    res = _run(tmp_path, {"a": source("a", [ANIMALS])})
    _write_packet(tmp_path / "pk", dict(res["exploratory"].files))
    out = _validate(tmp_path / "pk")
    assert out["for-import"]["valid"] and out["for-import"]["counts"]["assessments"] == 0
    manifest = json.loads((tmp_path / "pk/manifest.json").read_text())
    assert (
        manifest["creation_method"] == "manual"
        and manifest["access_scope"]["level"] == "restricted"
    )
    findings = [json.loads(x) for x in (tmp_path / "pk/findings.jsonl").read_text().splitlines()]
    assert {(f["export_state"], f["review_state"], f["verification_level"]) for f in findings} == {
        ("proposed", "unreviewed", "unverified")
    }


# --- incompatible / tampered packets -------------------------------------------------------


def _packet(tmp_path):
    res = _run(tmp_path, {"a": source("a", [ANIMALS])})
    _write_packet(tmp_path / "pk", dict(res["exploratory"].files))
    return tmp_path / "pk"


def test_tampered_artifact_bytes_are_rejected_by_the_diggypaper_validator(tmp_path):
    pk = _packet(tmp_path)
    f = pk / "findings.jsonl"
    f.write_bytes(f.read_bytes().replace(b"rat", b"cat", 1))
    codes = {e["code"] for e in sep.validate_packet(pk)["errors"]}
    assert "artifact-hash-mismatch" in codes


def test_unsupported_schema_version_is_rejected(tmp_path):
    pk = _packet(tmp_path)
    m = json.loads((pk / "manifest.json").read_text())
    m["schema_version"] = "source-evidence-packet/0.2.0"
    (pk / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    assert "unsupported-schema-version" in {e["code"] for e in sep.validate_packet(pk)["errors"]}


def test_private_scope_is_refused_for_import(tmp_path):
    pk = _packet(tmp_path)
    m = json.loads((pk / "manifest.json").read_text())
    m["access_scope"]["level"] = "private"
    (pk / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    assert "private-packet-not-exportable" in {e["code"] for e in sep.validate_packet(pk)["errors"]}


def test_overclaiming_human_review_is_rejected(tmp_path):
    """An agent that relabelled a finding human-reviewed without an assessment is caught."""
    pk = _packet(tmp_path)
    rows = [json.loads(x) for x in (pk / "findings.jsonl").read_text().splitlines()]
    rows[0].update(review_state="human-reviewed", export_state="approved-for-export")
    body = "".join(
        json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for r in rows
    )
    (pk / "findings.jsonl").write_text(body, encoding="utf-8")
    m = json.loads((pk / "manifest.json").read_text())
    m["artifacts"]["findings"]["sha256"] = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
    m["artifacts"]["findings"]["bytes"] = len(body.encode())
    (pk / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    codes = {e["code"] for e in sep.validate_packet(pk)["errors"]}
    assert {"review-without-assessment"} & codes


@pytest.mark.skipif(
    not CRT or shutil.which("node") is None, reason="set CRT_CHECKOUT to run the CRT importer"
)
def test_crt_importer_rejects_exploratory_and_refuses_tampered(tmp_path):
    script = Path(CRT) / "scripts/import-source-evidence-packet.mjs"
    pk = _packet(tmp_path)
    ok = subprocess.run(
        ["node", str(script), "--packet", str(pk), "--out", str(tmp_path / "o")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = json.loads((tmp_path / "o/import-report.json").read_text())
    assert ok.returncode == 0 and report["counts"]["findings"]["accepted"] == 0
    assert report["counts"]["findings"]["withheld"] == len(report["findings"]) > 0
    f = pk / "findings.jsonl"
    f.write_bytes(f.read_bytes().replace(b"rat", b"cat", 1))
    bad = subprocess.run(
        ["node", str(script), "--packet", str(pk), "--out", str(tmp_path / "o2")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert bad.returncode == 1 and not (tmp_path / "o2/cluster_evidence.json").exists()


def test_load_source_rejects_unsupported_section_kinds(tmp_path):
    p = tmp_path / "s.json"
    s = source("a", [section("Intro", "x", kind="introduction")])
    p.write_text(json.dumps(s))
    with pytest.raises(ValueError, match="unsupported section kinds"):
        load_source(p)


def test_confirmed_pair_with_species_and_rar_tables_reports_cells_and_flags(tmp_path):
    cfg = write_study(tmp_path, {"a": source("a", [ANIMALS])})
    d = tmp_path / "alures"
    d.mkdir()
    write_csv(
        d / "synthetic_nts_projects.csv",
        ["NTS ID", "Version", "Country"],
        [["NTS-ES-111111", "2", "Spain"]],
    )
    write_csv(
        d / "synthetic_nts_animals.csv",
        ["NTS ID", "Species", "Animals"],
        [["NTS-ES-111111", "Rats", "30"], ["NTS-ES-111111", "Mice", "n/a"]],
    )
    write_csv(
        d / "synthetic_rar.csv",
        ["NTS ID", "Species", "Animals used"],
        [["NTS-ES-111111", "Rats", "12"]],
    )
    res = run_study(cfg, tmp_path, d, COMMIT, None)
    cells = [
        o
        for o in res["observations"]
        if o["source_kind"] in ("alures", "rar") and o["dataset_cell"]
    ]
    assert {(o["count_category"], o["species"], o["number"]) for o in cells} == {
        ("NTS_planned_or_authorized", "Rats", 30),
        ("NTS_planned_or_authorized", "Mice", "not reported"),
        ("RAR_actual_used", "Rats", 12),
    }
    assert all(o["dataset_cell"]["file"].startswith("synthetic_") for o in cells)
    (cmp,) = res["comparisons"]
    assert cmp["ratio_computed"] is False
    assert any("exceeds the NTS authorized 30" in f for f in cmp["flags"])  # 40 reported > 30


def test_cli_offline_run_writes_every_deliverable(tmp_path):
    from app.nts.cli import main

    cfg = write_study(tmp_path, {"a": source("a", [ANIMALS])})
    (tmp_path / "study-config.json").write_text(json.dumps(cfg))
    assert main(["run", "--run-dir", str(tmp_path), "--offline"]) == 0
    out = tmp_path
    for name in (
        "run-manifest.json",
        "publication-nts-links.csv",
        "publication-nts-links.json",
        "animal-count-observations.csv",
        "finding-extraction-report.json",
        "crt-import-report.json",
        "nts-companion.json",
        "review-queue.json",
        "packet-validation-report.json",
        "source-evidence-packet/manifest.json",
        "source-evidence-packet-exploratory/manifest.json",
    ):
        assert (out / name).exists(), name
    companion = json.loads((out / "nts-companion.json").read_text())
    assert (
        companion["gate0_verdict"] == BLOCKED and companion["status"] == "RESEARCH_ONLY_UNREVIEWED"
    )
    crt = json.loads((out / "crt-import-report.json").read_text())
    assert (
        crt["acceptance"]["state"] == "BLOCKED_HUMAN_REVIEW_PENDING"
        and crt["validators_weakened"] is False
    )
    manifest = json.loads((out / "run-manifest.json").read_text())
    assert manifest["llm"]["used"] is False
