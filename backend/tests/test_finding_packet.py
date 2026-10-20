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


def test_commit_recorded_and_normalized_match(tmp_path):
    data = _data([_paper("art-a")], [_finding("f-n", ev=[_ev("ev-1", "normalized")])])
    result = build_packet(data)
    _validate(result, tmp_path)
    assert json.loads(result.files["manifest.json"])["producer"]["commit"] == COMMIT


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


@pytest.mark.parametrize("access", [{}, {"access_level": "public", "metadata_license": "CC0-1.0"}])
@pytest.mark.parametrize("commit", [None, "", "unknown", "synthetic-fixture", "C" * 40, "c" * 39])
def test_packet_requires_producer_commit(access, commit):
    # The frozen 0.1.0 schema has no "unknown" commit; a real export never invents one.
    data = {**_data([_paper("art-a")], [_finding("f1")]), **access, "producer_commit": commit}
    with pytest.raises(ValueError, match="producer commit"):
        build_packet(data)


# --- immutable revision history ---------------------------------------------------------


def _rev(fid, n, last, *, reviews="approve", ev=None, **kw):
    """Revision ``n`` of finding ``fid`` with its own evidence ``ev-<fid>-<n>`` and review."""
    ref = f"ev-{fid}-{n}"
    if reviews == "approve":
        reviews = [_review(f"rv-{fid}-{n}", RV1, ref=ref, rationale=f"why {n}")]
    elif reviews == "reject":
        reviews = [_review(f"rv-{fid}-{n}", RV1, "reject", "unclear", ref=ref, rationale="no")]
    elif reviews == "none":
        reviews = []
    return _finding(
        fid,
        revision=n,
        latest=(n == last),
        outcome=f"o{n}",
        time=f"day {n}",
        ev=ev if ev is not None else [_ev(ref, quote=f"quote {n}", start=10 * n, end=10 * n + 5)],
        reviews=reviews,
        **kw,
    )


def _chain(fid="f-h", *kinds):
    return [_rev(fid, n, len(kinds), reviews=k) for n, k in enumerate(kinds, start=1)]


def _states(result):
    return [
        (f["revision"], f["export_state"], f["review_state"], f["verification_level"])
        for f in _recs(result, "findings.jsonl")
    ]


def _build(findings, tmp_path, paper=None, name="p"):
    result = build_packet(_data([paper or _paper("art-a")], findings))
    _validate(result, tmp_path, name)
    return result


def test_approved_revision_one_alone_exports_one_approved_revision(tmp_path):
    result = _build(_chain("f-h", "approve"), tmp_path)
    assert _states(result) == [(1, "approved-for-export", "human-reviewed", "source-verified")]
    assert result.report["omitted"] == 0 and result.report["exported"] == 1


def test_revision_two_after_approved_revision_one_exports_whole_chain(tmp_path):
    result = _build(_chain("f-h", "approve", "approve"), tmp_path)
    findings = _recs(result, "findings.jsonl")
    assert [(f["finding_id"], f["revision"]) for f in findings] == [("finding:f-h", 1)] + [
        ("finding:f-h", 2)
    ]
    # exact historical content, context and numbers are preserved per revision
    assert [f["context"]["outcome"] for f in findings] == ["o1", "o2"]
    assert [f["statement"] for f in findings] == [
        "X changed o1 at day 1.",
        "X changed o2 at day 2.",
    ]
    assert all(f["numeric_results"] == _content("o", "t")["numeric_results"] for f in findings)
    assert [f["passage_ids"] for f in findings] == [["passage:ev-f-h-1"], ["passage:ev-f-h-2"]]
    assert [f["producer_ids"]["evidence_refs"] for f in findings] == [
        ["ev-f-h-1"],
        ["ev-f-h-2"],
    ]
    (paper,) = _recs(result, "papers.jsonl")
    assert paper["source_snapshot"]["snapshot_ref"] == "snap-A"
    assert json.loads(result.files["manifest.json"])["export_status"]["state"] == "complete"


def test_unreviewed_history_is_proposed_not_approved_and_has_no_assessments(tmp_path):
    result = _build(_chain("f-h", "none", "approve"), tmp_path)
    assert _states(result) == [
        (1, "proposed", "unreviewed", "unverified"),
        (2, "approved-for-export", "human-reviewed", "source-verified"),
    ]
    assert {a["finding_revision"] for a in _recs(result, "assessments.jsonl")} == {2}


def test_three_revisions_with_distinct_evidence(tmp_path):
    result = _build(_chain("f-h", "approve", "approve", "approve"), tmp_path)
    assert [f["revision"] for f in _recs(result, "findings.jsonl")] == [1, 2, 3]
    assert [p["passage_id"] for p in _passages(result)] == [
        "passage:ev-f-h-1",
        "passage:ev-f-h-2",
        "passage:ev-f-h-3",
    ]
    assert [
        (a["finding_revision"], a["passage_id"]) for a in _recs(result, "assessments.jsonl")
    ] == [
        (1, "passage:ev-f-h-1"),
        (2, "passage:ev-f-h-2"),
        (3, "passage:ev-f-h-3"),
    ]
    assert len({a["assessed_snapshot_sha256"] for a in _recs(result, "assessments.jsonl")}) == 1


def test_assessments_stay_revision_local_and_approval_never_propagates(tmp_path):
    # rev1 was REJECTED by RV1; rev2 approved by RV2. The later approval must not reach rev1.
    chain = [
        _rev("f-h", 1, 2, reviews="reject"),
        _rev(
            "f-h",
            2,
            2,
            reviews=[_review("rv-f-h-2", RV2, ref="ev-f-h-2", rationale="rev2 ok")],
        ),
    ]
    result = _build(chain, tmp_path)
    assert _states(result) == [
        (1, "proposed", "human-reviewed", "human-reviewed"),
        (2, "approved-for-export", "human-reviewed", "source-verified"),
    ]
    by_rev = {}
    for a in _recs(result, "assessments.jsonl"):
        by_rev.setdefault(a["finding_revision"], []).append(a)
    assert [a["producer_ids"]["support_assessment_ref"] for a in by_rev[1]] == ["rv-f-h-1"]
    assert [a["status"] for a in by_rev[1]] == ["unclear"]
    assert [a["reviewer_ref"] for a in by_rev[2]] == ["rv-" + "2" * 64]
    assert [a["passage_id"] for a in by_rev[1]] == ["passage:ev-f-h-1"]
    # the rev1 rejection leaves no approved/supports trace anywhere in the packet
    assert "supports" not in {a["status"] for a in by_rev[1]}


def test_two_approvals_on_different_revisions_are_not_independent_verification(tmp_path):
    chain = [
        _rev("f-h", 1, 2),
        _rev("f-h", 2, 2, reviews=[_review("rv-f-h-2", RV2, ref="ev-f-h-2")]),
    ]
    result = _build(chain, tmp_path)
    assert [s[3] for s in _states(result)] == ["source-verified", "source-verified"]


@pytest.mark.parametrize(
    ("latest_kind", "reason"),
    [("reject", "rejected"), ("none", "not-approved")],
)
def test_latest_rejected_or_unreviewed_withholds_whole_chain(tmp_path, latest_kind, reason):
    result = _build(_chain("f-h", "approve", latest_kind), tmp_path)
    assert result.report["omitted_by_reason"] == {reason: 2}
    assert _recs(result, "findings.jsonl") == [] and _passages(result) == []
    assert {(e["revision"], e["exported"]) for e in result.report["findings"]} == {
        (1, False),
        (2, False),
    }
    manifest = json.loads(result.files["manifest.json"])
    assert manifest["export_status"]["state"] == "withheld"
    assert f"{reason}=2" in manifest["export_status"]["reasons"]
    assert b"f-h" not in b"".join(result.files.values())


def test_superseded_latest_flag_withholds_chain(tmp_path):
    chain = _chain("f-h", "approve", "approve")
    chain[1]["is_latest"] = False
    assert _build(chain, tmp_path).report["omitted_by_reason"] == {"not-latest": 2}


def test_gap_and_duplicate_histories_never_produce_an_invalid_packet(tmp_path):
    gap = [_rev("f-g", 1, 3), _rev("f-g", 3, 3)]
    dup = [_rev("f-d", 1, 2), _rev("f-d", 2, 2), _rev("f-d", 2, 2)]
    ok = _chain("f-ok", "approve")
    result = _build([*gap, *dup, *ok], tmp_path)
    assert result.report["omitted_by_reason"] == {"duplicate-revision": 3, "revision-gap": 2}
    assert [f["finding_id"] for f in _recs(result, "findings.jsonl")] == ["finding:f-ok"]


def test_missing_or_unrepresentable_history_withholds_whole_chain(tmp_path):
    no_evidence = _chain("f-1", "approve", "approve")
    no_evidence[0]["evidence"] = []
    bad_match = _chain("f-2", "approve", "approve")
    bad_match[0]["evidence"][0]["match"]["start"] = None  # exact match without offsets
    no_quote = _chain("f-3", "approve", "approve")
    no_quote[0]["evidence"][0]["quote"] = None
    blank_reason = _chain("f-4", "approve", "approve")
    blank_reason[0]["reviews"][0]["judgments"][0]["rationale"] = " "
    stray = _chain("f-5", "approve", "approve")
    stray[0]["reviews"][0]["judgments"][0]["evidence_ref"] = "ev-elsewhere"
    selfrev = _chain("f-6", "approve", "approve")
    selfrev[0]["reviews"].append(_review("rv-self", PROPOSER, ref="ev-f-6-1"))
    result = _build(
        [*no_evidence, *bad_match, *no_quote, *blank_reason, *stray, *selfrev], tmp_path
    )
    assert result.report["omitted_by_reason"] == {"unrepresentable-history": 12}
    assert result.report["exported"] == 0


def test_missing_or_foreign_historical_snapshot_withholds_whole_chain(tmp_path):
    none_snap = _chain("f-1", "approve", "approve")
    none_snap[0]["evidence"][0]["snapshot_id"] = None
    foreign = _chain("f-2", "approve", "approve")
    foreign[0]["evidence"][0]["snapshot_id"] = "snap-unknown"
    result = _build([*none_snap, *foreign], tmp_path)
    assert result.report["omitted_by_reason"] == {"no-source-snapshot": 4}


def test_mixed_snapshot_history_fails_closed_without_repointing(tmp_path):
    chain = _chain("f-h", "approve", "approve")
    chain[1]["evidence"][0]["snapshot_id"] = "snap-B"
    paper = {
        **_paper("art-a"),
        "snapshots": [
            {"id": "snap-A", "sha256": SHA_A},
            {"id": "snap-B", "sha256": SHA_B},
        ],
    }
    result = _build(chain, tmp_path, paper)
    assert result.report["omitted_by_reason"] == {"mixed-snapshot": 2}
    assert _recs(result, "papers.jsonl") == []


def test_two_findings_of_one_paper_on_different_snapshots_conflict(tmp_path):
    a = _chain("f-a", "approve")
    b = _chain("f-b", "approve")
    b[0]["evidence"][0]["snapshot_id"] = "snap-B"
    paper = {
        **_paper("art-a"),
        "snapshots": [{"id": "snap-A", "sha256": SHA_A}, {"id": "snap-B", "sha256": SHA_B}],
    }
    result = _build([*a, *b], tmp_path, paper)
    assert result.report["omitted_by_reason"] == {"paper-snapshot-conflict": 2}
    # a clean finding of ANOTHER paper is unaffected
    c = _finding("f-c", article="art-c")
    both = build_packet(_data([paper, _paper("art-c")], [*a, *b, c]))
    _validate(both, tmp_path, "mixed")
    assert [f["finding_id"] for f in _recs(both, "findings.jsonl")] == ["finding:f-c"]


def test_shared_passage_with_conflicting_content_withholds_both_chains(tmp_path):
    a = _chain("f-a", "approve")
    b = _chain("f-b", "approve")
    for f in (a[0], b[0]):
        f["evidence"][0]["evidence_ref"] = "ev-shared"
        f["reviews"][0]["judgments"][0]["evidence_ref"] = "ev-shared"
    b[0]["evidence"][0]["quote"] = "a different quote"
    result = _build([*a, *b], tmp_path)
    assert result.report["omitted_by_reason"] == {"passage-conflict": 2}
    # identical shared passages remain fine
    b[0]["evidence"][0] = copy.deepcopy(a[0]["evidence"][0])
    ok = _build([*a, *b], tmp_path, name="ok")
    assert [p["passage_id"] for p in _passages(ok)] == ["passage:ev-shared"]


def _history_rights(rights):
    return {**_paper("art-a"), "rights": rights}


def test_history_without_rights_declaration_withholds_every_quote(tmp_path):
    result = _build(_chain("f-h", "approve", "approve"), tmp_path)
    assert {p["quote"] for p in _passages(result)} == {"withheld"}
    for n, p in enumerate(_passages(result), start=1):
        assert p["quote_sha256"] == "sha256:" + hashlib.sha256(f"quote {n}".encode()).hexdigest()
    assert b"quote 1" not in b"".join(result.files.values())
    (paper,) = _recs(result, "papers.jsonl")
    assert paper["rights"]["redistribution"] == "metadata-only"


def test_history_metadata_only_and_disallowed_licences_never_release_quotes(tmp_path):
    for i, rights in enumerate(
        [
            {"revision": 1, "license_id": "CC-BY-4.0", "redistribution": "metadata-only"},
            {"revision": 1, "license_id": "CC-BY-NC-4.0", "redistribution": "allowed"},
            {"revision": 1, "license_id": None, "redistribution": "allowed"},
            {"revision": 1, "license_id": "CC-BY-4.0", "redistribution": "bogus"},
        ]
    ):
        result = _build(
            _chain("f-h", "approve", "approve"), tmp_path, _history_rights(rights), f"r{i}"
        )
        assert {p["quote_state"] for p in _passages(result)} == {"withheld-rights"}
        assert b"quote 1" not in b"".join(result.files.values())
        assert _recs(result, "papers.jsonl")[0]["rights"]["redistribution"] == "metadata-only"


def test_history_restricted_rights_omit_the_whole_chain(tmp_path):
    rights = {"revision": 3, "license_id": "proprietary", "redistribution": "restricted"}
    result = _build(_chain("f-h", "approve", "approve"), tmp_path, _history_rights(rights))
    assert result.report["omitted_by_reason"] == {"paper-rights-restricted": 2}
    assert _recs(result, "findings.jsonl") == []


def test_history_allowed_licence_releases_quotes_of_every_revision(tmp_path):
    result = _build(_chain("f-h", "approve", "approve"), tmp_path, _history_rights(CC_BY))
    assert [p["quote"] for p in _passages(result)] == ["quote 1", "quote 2"]
    assert result.report["rights"][0]["quotes_included"] == 2


def test_history_export_is_deterministic_and_validates_repeatedly(tmp_path):
    chain = [*_chain("f-h", "none", "reject", "approve"), *_chain("f-i", "approve", "approve")]
    first = build_packet(_data([_paper("art-a")], chain))
    shuffled = copy.deepcopy(chain)
    shuffled.reverse()
    second = build_packet(_data([_paper("art-a")], shuffled))
    assert first.files == second.files and first.report == second.report
    for i in range(2):
        _validate(first, tmp_path, f"run{i}")


def test_history_export_leaks_no_free_text_notes_or_raw_actor_ids(tmp_path):
    chain = _chain("f-h", "approve", "approve")
    for f in chain:
        f["reviews"][0]["rationale"] = "PRIVATE-NOTE alice@example.org"
    result = _build(chain, tmp_path)
    blob = b"".join(result.files.values()).decode()
    assert "PRIVATE-NOTE" not in blob and "alice@example.org" not in blob
    assert RV1 not in blob and PROPOSER not in blob


# --- repair: bindings, strict content, supplied match state ---------------------------------


def _withheld(findings, tmp_path, reason="unrepresentable-history", n=2):
    result = _build(findings, tmp_path)
    assert result.report["omitted_by_reason"] == {reason: n}
    assert result.report["exported"] == 0
    return result


def _two_evidence(fid, n, **second):
    refs = (f"ev-{fid}-{n}", f"ev-{fid}-{n}b")
    ev = [
        _ev(refs[0], quote=f"quote {n}", start=10 * n, end=10 * n + 5),
        {**_ev(refs[1], quote=f"quote {n}b", start=100 + n, end=105 + n), **second},
    ]
    return ev


def test_revision_with_several_answers_withholds_whole_chain(tmp_path):
    chain = _chain("f-m", "approve", "approve")
    chain[0]["evidence"] = _two_evidence("f-m", 1, answer_ref="ans-2")
    chain[0]["reviews"] = [_review("rv-m", RV1, ref="ev-f-m-1")]
    _withheld(chain, tmp_path)


def test_revision_with_several_predictions_withholds_whole_chain(tmp_path):
    chain = _chain("f-m", "approve", "approve")
    chain[1]["evidence"] = _two_evidence("f-m", 2, prediction_id="pred-2")
    chain[1]["reviews"] = [_review("rv-m2", RV1, ref="ev-f-m-2")]
    _withheld(chain, tmp_path)


def test_partially_bound_revision_withholds_whole_chain(tmp_path):
    chain = _chain("f-m", "approve", "approve")
    chain[0]["evidence"] = _two_evidence("f-m", 1, answer_ref=None)
    chain[0]["reviews"] = [_review("rv-m", RV1, ref="ev-f-m-1")]
    _withheld(chain, tmp_path)


def test_distinct_bindings_across_revisions_are_representable(tmp_path):
    chain = _chain("f-d", "approve", "approve")
    chain[1]["evidence"][0].update(answer_ref="ans-2", prediction_id="pred-2")
    result = _build(chain, tmp_path)
    ids = [f["producer_ids"] for f in _recs(result, "findings.jsonl")]
    assert [(i["answer_ref"], i["prediction_ref"]) for i in ids] == [
        ("ans-1", "pred-1"),
        ("ans-2", "pred-2"),
    ]


def test_several_evidence_one_binding_exports_that_binding(tmp_path):
    chain = _chain("f-s", "approve")
    chain[0]["evidence"] = _two_evidence("f-s", 1)
    result = _build(chain, tmp_path)
    ids = _recs(result, "findings.jsonl")[0]["producer_ids"]
    assert (ids["answer_ref"], ids["prediction_ref"]) == ("ans-1", "pred-1")


def _mutated(mutate):
    chain = _chain("f-c", "approve", "approve")
    mutate(chain[0]["content"])
    return chain


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c["context"].pop("population"),
        lambda c: c["context"].update(extra="x"),
        lambda c: c.update(extra="x"),
        lambda c: c["numeric_results"][0].update(effect_size=1),
        lambda c: c["numeric_results"][0]["n"].update(extra=1),
        lambda c: c["numeric_results"][0].update(value="1.5"),
        lambda c: c["numeric_results"][0].update(value=True),
        lambda c: c["numeric_results"][0]["n"].update(value=3.0),
        lambda c: c["numeric_results"][0]["n"].update(value=True),
        lambda c: c["numeric_results"][0]["n"].update(value="3"),
        lambda c: c["numeric_results"][0]["n"].update(definition="other"),
        lambda c: c["numeric_results"][0].update(value=float("nan")),
        lambda c: c["numeric_results"].append(dict(c["numeric_results"][0])),
        lambda c: c["context"].update(time=5),
        lambda c: c["context"].update(time="  padded "),
        lambda c: c.update(statement=" padded "),
        lambda c: c.pop("numeric_results"),
        lambda c: c.update(numeric_results=tuple(c["numeric_results"])),
    ],
)
def test_content_must_be_exactly_valid_to_be_exported(tmp_path, mutate):
    _withheld(_mutated(mutate), tmp_path)


def test_valid_content_with_integer_value_is_written_as_supplied(tmp_path):
    chain = _chain("f-i", "approve")
    chain[0]["content"]["numeric_results"][0]["value"] = 3
    result = _build(chain, tmp_path)
    assert _recs(result, "findings.jsonl")[0]["numeric_results"][0]["value"] == 3


@pytest.mark.parametrize(
    "start,end",
    [(10, True), (True, 20), (False, 5), (10, 10), (-1, 5), (10.0, 20), (10, "20")],
)
def test_matched_offsets_must_be_plain_ordered_integers(tmp_path, start, end):
    chain = _chain("f-o", "approve", "approve")
    chain[0]["evidence"][0]["match"].update(start=start, end=end)
    _withheld(chain, tmp_path)


@pytest.mark.parametrize(
    "match",
    [
        {"ref": "qm-x", "status": "weird", "view_ref": "view:text-v1", "start": None, "end": None},
        {"ref": "qm-x", "status": None, "view_ref": None, "start": None, "end": None},
        {"ref": "qm-x", "status": ["exact"], "view_ref": None, "start": None, "end": None},
        {"ref": 7, "status": "not_found", "view_ref": None, "start": None, "end": None},
        {"ref": "qm-x", "status": "not_found", "view_ref": "", "start": None, "end": None},
        {"ref": " qm-x ", "status": "not_found", "view_ref": None, "start": None, "end": None},
        {"ref": "qm-x", "status": "not_found", "view_ref": None, "start": 3, "end": None},
        {
            "ref": "qm-x",
            "status": "not_found",
            "view_ref": None,
            "start": None,
            "end": None,
            "x": 1,
        },
        {},
        "not_found",
    ],
)
def test_supplied_but_unsupported_match_state_withholds_instead_of_erasing(tmp_path, match):
    chain = _chain("f-u", "approve", "approve")
    chain[0]["evidence"].append({**_ev("ev-extra"), "match": match})
    _withheld(chain, tmp_path)


def test_absent_match_is_not_checked_and_supplied_states_keep_their_receipt(tmp_path):
    chain = _chain("f-n", "approve", "approve")
    chain[0]["evidence"] += [
        {**_ev("ev-absent"), "match": None},
        {
            **_ev("ev-inc"),
            "match": {
                "ref": "qm-inc",
                "status": "incomplete",
                "view_ref": None,
                "start": None,
                "end": None,
            },
        },
        {
            **_ev("ev-nf"),
            "match": {
                "ref": "qm-nf",
                "status": "not_found",
                "view_ref": "view:v",
                "start": None,
                "end": None,
            },
        },
    ]
    result = _build(chain, tmp_path)
    by = {p["producer_ids"]["evidence_ref"]: p for p in _recs(result, "passages.jsonl")}
    assert by["ev-absent"]["match"] == {
        "receipt_ref": "not-available",
        "status": "not_checked",
        "matcher": "unknown",
    }
    assert by["ev-inc"]["match"] == {
        "receipt_ref": "qm-inc",
        "status": "incomplete",
        "matcher": "quote-matcher-v1",
    }
    assert by["ev-nf"]["match"]["receipt_ref"] == "qm-nf"
    assert by["ev-nf"]["source"]["view_id"] == "view:v"
