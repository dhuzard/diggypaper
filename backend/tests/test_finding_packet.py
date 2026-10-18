"""Pure tests of the finding -> source-evidence-packet builder (no database)."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.finding_packet import build_packet

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "docs/contracts/source-evidence-packet/v0.1.0/validator/sep_validate.py"
_spec = importlib.util.spec_from_file_location("sep_validate_fp", VALIDATOR)
assert _spec is not None and _spec.loader is not None
sep = importlib.util.module_from_spec(_spec)
sys.modules["sep_validate_fp"] = sep
_spec.loader.exec_module(sep)

SHA_A = "a" * 64
SHA_B = "b" * 64
RV1 = "reviewer-" + "1" * 64
RV2 = "reviewer-" + "2" * 64
PROPOSER = "reviewer-" + "9" * 64
COMMIT = "c" * 40


def _content(outcome: str, time: str) -> dict:
    return {
        "statement": f"X changed {outcome} at {time}.",
        "origin": "source-explicit",
        "context": {
            "population": "mice",
            "exposure": "X",
            "comparator": "vehicle",
            "outcome": outcome,
            "time": time,
            "experimental_unit": "animal",
        },
        "numeric_results": [
            {
                "name": "diff",
                "value": -2.5,
                "unit": "unknown",
                "n": {"value": 12, "definition": "group"},
            }
        ],
    }


def _ev(ref: str, status="exact", snap="snap-A", quote="the quote é", start=10, end=20):
    match = {
        "ref": f"qm-{ref}",
        "status": status,
        "view_ref": "view:text-v1",
        "start": start if status in ("exact", "normalized") else None,
        "end": end if status in ("exact", "normalized") else None,
    }
    return {
        "prediction_id": "pred-1",
        "answer_ref": "ans-1",
        "evidence_ref": ref,
        "quote": quote,
        "match": match,
        "snapshot_id": snap,
    }


def _review(rid, who, decision="approve", status="supports", ref="ev-1", rationale="yes"):
    return {
        "id": rid,
        "reviewer_ref": who,
        "decision": decision,
        "judgments": [{"evidence_ref": ref, "status": status, "rationale": rationale}],
        "created_at": "2026-03-02T10:00:00+00:00",
    }


def _finding(
    fid, article="art-a", revision=1, latest=True, outcome="o", time="t", ev=None, reviews=None
):
    return {
        "finding_id": fid,
        "article_id": article,
        "revision": revision,
        "is_latest": latest,
        "content": _content(outcome, time),
        "proposer_ref": PROPOSER,
        "created_at": "2026-03-01T09:00:00",
        "evidence": ev if ev is not None else [_ev("ev-1")],
        "reviews": reviews if reviews is not None else [_review(f"rev-{fid}", RV1)],
    }


def _paper(aid, doi="10.0000/x", pmid=None, pmcid=None, snap=("snap-A", SHA_A)):
    return {
        "article_id": aid,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "title": f"Paper {aid}",
        "authors": ["Synthetic, A"],
        "pub_types": ["Journal Article"],
        "snapshot": {"id": snap[0], "sha256": snap[1], "raw_availability": "available"}
        if snap
        else None,
    }


def _data(papers, findings):
    return {"producer_commit": COMMIT, "papers": papers, "findings": findings}


def _validate(result, tmp_path, name="p"):
    out = tmp_path / name
    out.mkdir()
    for fname, blob in result.files.items():
        (out / fname).write_bytes(blob)
    res = sep.validate_packet(out, "for-import")
    assert res["errors"] == [], res["errors"]
    assert res["valid"] is True
    return res


def _recs(result, name):
    return [json.loads(x) for x in result.files[name].decode().splitlines()]


def _rich():
    f1 = _finding(
        "f-1",
        outcome="marker Y",
        time="day 14",
        ev=[_ev("ev-1"), _ev("ev-2", "ambiguous")],
        reviews=[_review("rev-1", RV1), _review("rev-2", RV2, ref="ev-2", status="unclear")],
    )
    f2 = _finding("f-2", outcome="weight", time="day 28", ev=[_ev("ev-1")])  # shares passage ev-1
    f3 = _finding(
        "f-3",
        article="art-b",
        ev=[_ev("ev-3", snap="snap-B", quote="other")],
        reviews=[_review("rev-3", RV1, ref="ev-3")],
    )
    return _data(
        [_paper("art-a"), _paper("art-b", doi=None, pmid="123", snap=("snap-B", SHA_B))],
        [f1, f2, f3],
    )


def test_rich_export_validates_and_maps(tmp_path):
    result = build_packet(_rich())
    _validate(result, tmp_path)
    assert result.report["exported"] == 3 and result.report["omitted"] == 0
    findings = _recs(result, "findings.jsonl")
    assert [f["finding_id"] for f in findings] == ["finding:f-1", "finding:f-2", "finding:f-3"]
    # two findings of one paper stay distinct; passage ev-1 shared once
    assert {f["paper_id"] for f in findings} == {"paper:art-a", "paper:art-b"}
    assert [p["passage_id"] for p in _recs(result, "passages.jsonl")] == [
        "passage:ev-1",
        "passage:ev-2",
        "passage:ev-3",
    ]
    assert [f["verification_level"] for f in findings] == [
        "independently-verified",
        "source-verified",
        "source-verified",
    ]
    for p in _recs(result, "passages.jsonl"):
        assert p["quote"] == "withheld" and p["quote_state"] == "withheld-rights"
    p1 = _recs(result, "passages.jsonl")[0]
    assert p1["quote_sha256"] == "sha256:" + hashlib.sha256("the quote é".encode()).hexdigest()
    papers = _recs(result, "papers.jsonl")
    assert papers[1]["identifiers"] == {
        "doi": "not-available",
        "pmid": "123",
        "pmcid": "not-available",
    }
    assert papers[0]["rights"]["redistribution"] == "metadata-only"
    manifest = json.loads(result.files["manifest.json"])
    assert manifest["export_status"]["state"] == "complete"
    assert manifest["access_scope"]["level"] == "restricted"
    assessments = _recs(result, "assessments.jsonl")
    assert all(
        a["reviewer_ref"] == "rv-" + "1" * 64 or a["reviewer_ref"] == "rv-" + "2" * 64
        for a in assessments
    )
    assert {a["assessed_snapshot_sha256"] for a in assessments} == {
        "sha256:" + SHA_A,
        "sha256:" + SHA_B,
    }
    assert manifest["created_at"] == "2026-03-02T10:00:00Z"


def test_omitted_cases_counted_and_never_exported(tmp_path):
    good = _finding("f-ok")
    cases = {
        "not-latest": _finding("f-old", latest=False),
        "not-approved": _finding("f-unrev", reviews=[]),
        "rejected": _finding(
            "f-rej", reviews=[_review("r1", RV1), _review("r2", RV2, decision="reject")]
        ),
        "no-matched-passage": _finding("f-nomatch", ev=[_ev("ev-1", "ambiguous")]),
        "no-public-identifier": _finding("f-noid", article="art-noid"),
        "no-source-snapshot": _finding("f-nosnap", article="art-nosnap"),
    }
    # proposer self-approval does not count
    cases["not-approved"]["reviews"] = [_review("rp", PROPOSER)]
    papers = [
        _paper("art-a"),
        _paper("art-noid", doi=None),
        _paper("art-nosnap", snap=None),
    ]
    result = build_packet(_data(papers, [good, *cases.values()]))
    _validate(result, tmp_path)
    assert result.report["omitted_by_reason"] == {k: 1 for k in sorted(cases)}
    assert [f["finding_id"] for f in _recs(result, "findings.jsonl")] == ["finding:f-ok"]
    manifest = json.loads(result.files["manifest.json"])
    assert manifest["export_status"]["state"] == "partial"
    assert manifest["export_status"]["withheld_counts"]["findings"] == 6
    omitted_ids = {e["finding_id"] for e in result.report["findings"] if not e["exported"]}
    blob = b"".join(result.files.values()).decode()
    assert all(i not in blob for i in omitted_ids)


def test_only_non_supporting_judgment_is_not_exported(tmp_path):
    f = _finding("f-x", reviews=[_review("r1", RV1, status="insufficient")])
    result = build_packet(_data([_paper("art-a")], [f]))
    assert result.report["omitted_by_reason"] == {"no-matched-passage": 1}
    assert json.loads(result.files["manifest.json"])["export_status"]["state"] == "withheld"
    _validate(result, tmp_path)


def test_two_independent_approvers_independently_verified(tmp_path):
    f = _finding("f-i", reviews=[_review("r1", RV1), _review("r2", RV2, status="contradicts")])
    result = build_packet(_data([_paper("art-a")], [f]))
    _validate(result, tmp_path)
    assert _recs(result, "findings.jsonl")[0]["verification_level"] == "independently-verified"
    assert len(_recs(result, "assessments.jsonl")) == 2


def test_second_approver_not_assessed_stays_source_verified(tmp_path):
    f = _finding(
        "f-i", reviews=[_review("r1", RV1), _review("r2", RV2, status="not_assessed", rationale="")]
    )
    result = build_packet(_data([_paper("art-a")], [f]))
    _validate(result, tmp_path)
    assert _recs(result, "findings.jsonl")[0]["verification_level"] == "source-verified"
    na = [a for a in _recs(result, "assessments.jsonl") if a["status"] == "not_assessed"]
    assert na and na[0]["rationale"] == "not-available"


def test_deterministic_bytes_and_input_not_mutated():
    data = _rich()
    snapshot = copy.deepcopy(data)
    a = build_packet(data)
    shuffled = copy.deepcopy(data)
    shuffled["findings"].reverse()
    b = build_packet(shuffled)
    assert a.files == b.files
    assert data == snapshot
    assert list(a.files) == sorted(a.files)


def test_empty_export_is_withheld_and_validates(tmp_path):
    result = build_packet(_data([], []))
    res = _validate(result, tmp_path, "empty")
    manifest = json.loads(result.files["manifest.json"])
    assert manifest["export_status"]["state"] == "withheld"
    assert manifest["created_at"] == "1970-01-01T00:00:00Z"
    assert all(v["record_count"] == 0 for v in manifest["artifacts"].values())
    assert all(
        result.files[f"{n}.jsonl"] == b"" for n in ("papers", "findings", "passages", "assessments")
    )
    assert res["counts"]["findings"] == 0


def test_unknown_commit_and_normalized_match(tmp_path):
    data = _data([_paper("art-a")], [_finding("f-n", ev=[_ev("ev-1", "normalized")])])
    data["producer_commit"] = "not-a-commit"
    result = build_packet(data)
    _validate(result, tmp_path)
    assert json.loads(result.files["manifest.json"])["producer"]["commit"] == "unknown"


# --- redistribution rights ---------------------------------------------------------------

CC_BY = {"revision": 1, "license_id": "CC-BY-4.0", "redistribution": "allowed"}


def _passages(result):
    return _recs(result, "passages.jsonl")


def test_no_declaration_withholds_quotes_and_stays_restricted(tmp_path):
    result = build_packet(_data([_paper("art-a")], [_finding("f1")]))
    _validate(result, tmp_path)
    (passage,) = _passages(result)
    assert passage["quote_state"] == "withheld-rights"
    assert passage["quote"] == "withheld"
    assert passage["quote_sha256"] == "sha256:" + hashlib.sha256("the quote é".encode()).hexdigest()
    (paper,) = _recs(result, "papers.jsonl")
    assert paper["rights"]["redistribution"] == "metadata-only"
    assert paper["rights"]["license"] == "unknown"
    manifest = json.loads(result.files["manifest.json"])
    assert manifest["access_scope"]["level"] == "restricted"
    assert [x["applies_to"] for x in manifest["licenses"]] == ["packet-metadata"]
    assert b"the quote" not in b"".join(result.files.values())


def test_allowed_open_licence_includes_quotes_and_validates(tmp_path):
    paper = {**_paper("art-a"), "rights": CC_BY}
    result = build_packet(
        {
            **_data([paper], [_finding("f1")]),
            "access_level": "public",
            "metadata_license": "CC0-1.0",
        }
    )
    _validate(result, tmp_path)
    (passage,) = _passages(result)
    assert passage["quote_state"] == "included"
    assert passage["quote"] == "the quote é"
    assert passage["quote_sha256"] == "sha256:" + hashlib.sha256("the quote é".encode()).hexdigest()
    (paper_rec,) = _recs(result, "papers.jsonl")
    assert paper_rec["rights"] == {
        "license": "CC-BY-4.0",
        "redistribution": "allowed",
        "full_text_included": False,
    }
    manifest = json.loads(result.files["manifest.json"])
    assert manifest["access_scope"] == {"level": "public", "redistributable_full_text": False}
    assert manifest["licenses"] == [
        {"license_id": "CC0-1.0", "applies_to": "packet-metadata"},
        {"license_id": "CC-BY-4.0", "applies_to": "quotes"},
    ]
    assert result.report["rights"] == [
        {
            "article_id": "art-a",
            "declaration_revision": 1,
            "redistribution": "allowed",
            "license_id": "CC-BY-4.0",
            "quotes_included": 1,
            "quotes_withheld": 0,
        }
    ]


@pytest.mark.parametrize(
    "rights",
    [
        # A non-allowlisted licence never releases quotes, even if marked allowed upstream.
        {"revision": 1, "license_id": "CC-BY-NC-4.0", "redistribution": "allowed"},
        {"revision": 1, "license_id": "CC-BY-ND-4.0", "redistribution": "allowed"},
        {"revision": 1, "license_id": "proprietary", "redistribution": "allowed"},
        {"revision": 1, "license_id": "CC-BY-4.0", "redistribution": "metadata-only"},
        {"revision": 1, "license_id": "CC-BY-4.0", "redistribution": "ALLOWED"},
        {"revision": 1, "license_id": None, "redistribution": "allowed"},
    ],
)
def test_anything_but_allowlisted_allowed_withholds(tmp_path, rights):
    paper = {**_paper("art-a"), "rights": rights}
    result = build_packet(_data([paper], [_finding("f1")]))
    _validate(result, tmp_path)
    (passage,) = _passages(result)
    assert passage["quote_state"] == "withheld-rights"
    assert b"the quote" not in b"".join(result.files.values())
    (paper_rec,) = _recs(result, "papers.jsonl")
    assert paper_rec["rights"]["redistribution"] == "metadata-only"


def test_restricted_declaration_omits_the_finding(tmp_path):
    restricted = {"revision": 2, "license_id": "proprietary", "redistribution": "restricted"}
    papers = [{**_paper("art-a"), "rights": restricted}, {**_paper("art-b"), "rights": CC_BY}]
    result = build_packet(_data(papers, [_finding("f1"), _finding("f2", article="art-b")]))
    _validate(result, tmp_path)
    assert [f["finding_id"] for f in _recs(result, "findings.jsonl")] == ["finding:f2"]
    assert [p["title"] for p in _recs(result, "papers.jsonl")] == ["Paper art-b"]
    assert result.report["omitted_by_reason"] == {"paper-rights-restricted": 1}
    manifest = json.loads(result.files["manifest.json"])
    assert "paper-rights-restricted" in json.dumps(manifest["export_status"])


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"access_level": "open"}, "access level"),
        ({"access_level": "public"}, "packet-metadata licence"),
        ({"metadata_license": "CC-BY-NC-4.0"}, "metadata licence"),
        ({"access_level": "public", "metadata_license": "unknown"}, "metadata licence"),
    ],
)
def test_packet_scope_options_rejected(extra, message):
    with pytest.raises(ValueError, match=message):
        build_packet({**_data([_paper("art-a")], [_finding("f1")]), **extra})


def test_public_packet_requires_producer_commit():
    data = {
        **_data([_paper("art-a")], [_finding("f1")]),
        "access_level": "public",
        "metadata_license": "CC0-1.0",
    }
    for commit in (None, "unknown", "C" * 40):
        with pytest.raises(ValueError, match="producer commit"):
            build_packet({**data, "producer_commit": commit})
