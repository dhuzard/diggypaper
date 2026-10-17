#!/usr/bin/env python3
"""Deterministic generator for source-evidence-packet/0.1.0 fixtures.

Writes fixtures/valid/canonical/ and fixtures/invalid/<case>/ next to this
script. Stdlib only; no clock, no randomness; idempotent (output dirs are
wiped and rewritten). Every invalid case is the canonical packet plus ONE
mutation, with manifest hashes/lengths/counts recomputed so that only the
intended defect fires (hash/length/count/encoding cases corrupt after hashing).
"""
import copy
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_VERSION = "source-evidence-packet/0.1.0"
ARTIFACTS = [
    ("papers", "papers.jsonl"),
    ("findings", "findings.jsonl"),
    ("passages", "passages.jsonl"),
    ("assessments", "assessments.jsonl"),
]
TS = "2026-01-01T00:00:00Z"
TS_ASSESSED = "2026-01-02T00:00:00Z"
NA = "not-available"


def sha(b):
    if isinstance(b, str):
        b = b.encode("utf-8")
    return "sha256:" + hashlib.sha256(b).hexdigest()


def canon_line(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canon_jsonl(records):
    return "".join(canon_line(r) + "\n" for r in records).encode("utf-8")


def pretty(obj):
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------- canonical
SNAP_A = (
    "Synthetic Study A reports a toy experiment.\n"
    "In synthetic mice, treatment X lowered marker Y at day 14 (mean difference -2.5, n = 12 animals).\n"
    "By day 28 the change in marker Y was reported for both cohort one and cohort two.\n"
)
SNAP_A_SHA = sha(SNAP_A)
OTHER_SHA = sha("some other snapshot")

Q1 = "treatment X lowered marker Y at day 14"
Q2_SPAN = "In synthetic mice, treatment X lowered marker Y at day 14"
Q2 = "In synthetic mice,  treatment X lowered marker Y at day 14"  # extra space: normalized match
Q3 = "marker Y"  # occurs several times: ambiguous
QB = "Synthetic Study B quote withheld for rights."


def span(text):
    s = SNAP_A.index(text)
    return s, s + len(text)


def canonical():
    s1, e1 = span(Q1)
    s2, e2 = span(Q2_SPAN)
    papers = [
        {
            "paper_id": "paper:synth-a",
            "identifiers": {"doi": "10.0000/synth.a", "pmid": NA, "pmcid": NA},
            "title": "Synthetic Study A of a Toy Treatment",
            "authors": ["Synthetic, Alice", "Synthetic, Bob"],
            "source_type": "journal-article",
            "source_snapshot": {"state": "captured", "snapshot_ref": "snapshot:synth-a-1", "sha256": SNAP_A_SHA},
            "rights": {"license": "CC-BY-4.0", "redistribution": "allowed", "full_text_included": False},
            "producer_ids": {"article_ref": "article-synth-a"},
        },
        {
            "paper_id": "paper:synth-b",
            "identifiers": {"doi": NA, "pmid": "00000002", "pmcid": NA},
            "title": "Synthetic Study B of Another Toy Treatment",
            "authors": ["Synthetic, Carol"],
            "source_type": "preprint",
            "source_snapshot": {"state": "unavailable", "snapshot_ref": NA, "sha256": NA},
            "rights": {"license": "unknown", "redistribution": "metadata-only", "full_text_included": False},
            "producer_ids": {"article_ref": "article-synth-b"},
        },
    ]
    findings = [
        {
            "finding_id": "finding:synth-a-f1",
            "revision": 1,
            "paper_id": "paper:synth-a",
            "statement": "Treatment X lowered marker Y at day 14 in synthetic mice.",
            "origin": "source-explicit",
            "export_state": "approved-for-export",
            "review_state": "human-reviewed",
            "verification_level": "source-verified",
            "context": {
                "population": "synthetic mice",
                "exposure": "treatment X",
                "comparator": "vehicle",
                "outcome": "marker Y level",
                "time": "day 14",
                "experimental_unit": "animal",
            },
            "numeric_results": [
                {"name": "mean difference", "value": -2.5, "unit": "unknown",
                 "n": {"value": 12, "definition": "experimental-unit"}}
            ],
            "passage_ids": ["passage:synth-a-p1", "passage:synth-a-p2"],
            "producer_ids": {"answer_ref": "answer-synth-1", "evidence_refs": ["evidence-synth-1", "evidence-synth-2"],
                             "prediction_ref": "prediction-synth-1"},
            "created_at": TS,
        },
        {
            "finding_id": "finding:synth-a-f2",
            "revision": 1,
            "paper_id": "paper:synth-a",
            "statement": "Marker Y changed by day 28 in both synthetic cohorts.",
            "origin": "ai-paraphrase",
            "export_state": "proposed",
            "review_state": "unreviewed",
            "verification_level": "unverified",
            "context": {
                "population": "synthetic mice, cohort one and cohort two",
                "exposure": "treatment X",
                "comparator": "unknown",
                "outcome": "marker Y change",
                "time": "day 28",
                "experimental_unit": "cohort",
            },
            "numeric_results": [],
            "passage_ids": ["passage:synth-a-p3"],
            "producer_ids": {"answer_ref": "answer-synth-2", "evidence_refs": ["evidence-synth-3"]},
            "created_at": TS,
        },
        {
            "finding_id": "finding:synth-b-f1",
            "revision": 1,
            "paper_id": "paper:synth-b",
            "statement": "Treatment Z was associated with outcome W in a synthetic cohort.",
            "origin": "ai-paraphrase",
            "export_state": "proposed",
            "review_state": "unreviewed",
            "verification_level": "unverified",
            "context": {
                "population": "synthetic cohort",
                "exposure": "treatment Z",
                "comparator": "unknown",
                "outcome": "outcome W",
                "time": "unknown",
                "experimental_unit": "unknown",
            },
            "numeric_results": [],
            "passage_ids": ["passage:synth-b-p1"],
            "producer_ids": {"answer_ref": "answer-synth-3", "evidence_refs": ["evidence-synth-4"]},
            "created_at": TS,
        },
    ]
    passages = [
        {
            "passage_id": "passage:synth-a-p1", "paper_id": "paper:synth-a",
            "quote_state": "included", "quote": Q1, "quote_sha256": sha(Q1),
            "source": {"snapshot_ref": "snapshot:synth-a-1", "view_id": "view:text-v1", "start": s1, "end": e1},
            "match": {"receipt_ref": "receipt:synth-a-p1", "status": "exact", "matcher": "quote-matcher-v1"},
            "producer_ids": {"evidence_ref": "evidence-synth-1"},
        },
        {
            "passage_id": "passage:synth-a-p2", "paper_id": "paper:synth-a",
            "quote_state": "included", "quote": Q2, "quote_sha256": sha(Q2),
            "source": {"snapshot_ref": "snapshot:synth-a-1", "view_id": "view:text-v1", "start": s2, "end": e2},
            "match": {"receipt_ref": "receipt:synth-a-p2", "status": "normalized", "matcher": "quote-matcher-v1"},
            "producer_ids": {"evidence_ref": "evidence-synth-2"},
        },
        {
            "passage_id": "passage:synth-a-p3", "paper_id": "paper:synth-a",
            "quote_state": "included", "quote": Q3, "quote_sha256": sha(Q3),
            "source": {"snapshot_ref": "snapshot:synth-a-1", "view_id": "view:text-v1", "start": None, "end": None},
            "match": {"receipt_ref": "receipt:synth-a-p3", "status": "ambiguous", "matcher": "quote-matcher-v1"},
            "producer_ids": {"evidence_ref": "evidence-synth-3"},
        },
        {
            "passage_id": "passage:synth-b-p1", "paper_id": "paper:synth-b",
            "quote_state": "withheld-rights", "quote": "withheld", "quote_sha256": sha(QB),
            "source": {"snapshot_ref": NA, "view_id": NA, "start": None, "end": None},
            "match": {"receipt_ref": NA, "status": "source_unavailable", "matcher": "unknown"},
            "producer_ids": {"evidence_ref": "evidence-synth-4"},
        },
    ]
    rv = "rv-0123456789abcdef"
    assessments = [
        {
            "assessment_id": "assessment:synth-1", "finding_id": "finding:synth-a-f1", "finding_revision": 1,
            "passage_id": "passage:synth-a-p1", "protocol": "seip-support-v1", "status": "supports",
            "rationale": "Synthetic rationale: the passage states the day 14 result.",
            "reviewer_ref": rv, "assessed_at": TS_ASSESSED, "assessed_snapshot_sha256": SNAP_A_SHA,
            "producer_ids": {"support_assessment_ref": "support-synth-1"},
        },
        {
            "assessment_id": "assessment:synth-2", "finding_id": "finding:synth-a-f1", "finding_revision": 1,
            "passage_id": "passage:synth-a-p2", "protocol": "seip-support-v1", "status": "not_assessed",
            "rationale": NA,
            "reviewer_ref": rv, "assessed_at": TS_ASSESSED, "assessed_snapshot_sha256": SNAP_A_SHA,
            "producer_ids": {"support_assessment_ref": "support-synth-2"},
        },
        {
            "assessment_id": "assessment:synth-3", "finding_id": "finding:synth-b-f1", "finding_revision": 1,
            "passage_id": "passage:synth-b-p1", "protocol": "seip-support-v1", "status": "restricted",
            "rationale": NA,
            "reviewer_ref": rv, "assessed_at": TS_ASSESSED, "assessed_snapshot_sha256": NA,
            "producer_ids": {"support_assessment_ref": "support-synth-3"},
        },
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "packet_id": "packet:synth-canonical",
        "created_at": TS,
        "creation_method": "synthetic-fixture",
        "producer": {
            "name": "diggypaper-fixture-builder",
            "commit": "synthetic-fixture",
            "schema_refs": [
                {"name": "evidence-envelope-v1", "version": "1"},
                {"name": "seip-support-v1", "version": "1"},
                {"name": "evidence-links-v1", "version": "1"},
            ],
        },
        "licenses": [
            {"license_id": "CC0-1.0", "applies_to": "packet-metadata"},
            {"license_id": "unknown", "applies_to": "quotes"},
        ],
        "access_scope": {"level": "public", "redistributable_full_text": False},
        "export_status": {
            "state": "complete",
            "withheld_counts": {"papers": 0, "findings": 0, "passages": 0, "assessments": 0},
            "reasons": [],
        },
    }
    return {"papers": papers, "findings": findings, "passages": passages,
            "assessments": assessments, "manifest": manifest}


# ------------------------------------------------------------------ writing
def build(recs, post=None, serializers=None):
    """Return {filename: bytes|None} incl. manifest.json, applying `post(files, manifest)` after hashing."""
    recs = copy.deepcopy(recs)
    manifest = recs.pop("manifest")
    files = {}
    manifest["artifacts"] = {}
    for key, fname in ARTIFACTS:
        data = (serializers or {}).get(key, canon_jsonl)(recs[key])
        files[fname] = data
        manifest["artifacts"][key] = {
            "path": fname, "sha256": sha(data), "bytes": len(data), "record_count": len(recs[key]),
        }
    if post:
        post(files, manifest)
    files["manifest.json"] = pretty(manifest)
    return files


def write_dir(d, files, errors=None):
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for name, data in sorted(files.items()):
        if data is not None:
            (d / name).write_bytes(data)
    if errors is not None:
        (d / "expected.json").write_bytes(pretty({"errors": sorted(errors)}))


# ----------------------------------------------------------------- mutations
def by_id(recs, key, idfield, value):
    return next(r for r in recs[key] if r[idfield] == value)


def new_finding(recs, fid, **over):
    f = copy.deepcopy(by_id(recs, "findings", "finding_id", "finding:synth-a-f2"))
    f.update({"finding_id": fid, "passage_ids": ["passage:synth-a-p1"], "statement": "Synthetic extra finding " + fid + "."})
    f.update(over)
    recs["findings"].append(f)
    return f


def new_assessment(recs, aid, fid, rev, pid, status, rationale=None, **over):
    a = copy.deepcopy(recs["assessments"][0])
    a.update({"assessment_id": aid, "finding_id": fid, "finding_revision": rev, "passage_id": pid,
              "status": status, "rationale": rationale or NA,
              "producer_ids": {"support_assessment_ref": "support-" + aid.split(":")[1]}})
    a.update(over)
    recs["assessments"].append(a)
    return a


def hdr(recs):
    return recs["manifest"]


CASES = {}  # name -> (errors, mutate(recs), post, serializers)


def case(name, errors, post=None, serializers=None):
    def deco(fn):
        CASES[name] = (errors, fn, post, serializers)
        return fn
    return deco


# --- structure / hash
@case("schema-invalid", ["schema-invalid:papers.jsonl"])
def _(r):
    del r["papers"][0]["title"]


@case("artifact-missing", ["artifact-missing"], post=lambda files, m: files.__setitem__("assessments.jsonl", None))
def _(r):
    pass


def _flip(files, m):
    b = files["papers.jsonl"]
    i = b.index(b"Synthetic Study A")
    files["papers.jsonl"] = b[:i] + b"Synthetic Study Z" + b[i + len(b"Synthetic Study A"):]  # same length


@case("artifact-hash-mismatch", ["artifact-hash-mismatch"], post=_flip)
def _(r):
    pass


def _len(files, m):
    m["artifacts"]["findings"]["bytes"] += 1


@case("artifact-length-mismatch", ["artifact-length-mismatch"], post=_len)
def _(r):
    pass


def _cnt(files, m):
    m["artifacts"]["passages"]["record_count"] += 1


@case("record-count-mismatch", ["record-count-mismatch"], post=_cnt)
def _(r):
    pass


@case("unexpected-file", ["unexpected-file"], post=lambda files, m: files.__setitem__("extra.txt", b"undeclared\n"))
def _(r):
    pass


def _noncanon(records):
    return "".join(json.dumps(x, separators=(", ", ": "), ensure_ascii=False) + "\n" for x in records).encode("utf-8")


@case("non-canonical-encoding", ["non-canonical-encoding"], serializers={"papers": _noncanon})
def _(r):
    pass


@case("duplicate-id", ["duplicate-id"])
def _(r):
    r["papers"].append(copy.deepcopy(r["papers"][1]))


@case("unsupported-schema-version", ["unsupported-schema-version"])
def _(r):
    r["manifest"]["schema_version"] = "source-evidence-packet/9.9.9"


@case("version-unsupported", ["unsupported-schema-version"])
def _(r):
    r["manifest"]["schema_version"] = "source-evidence-packet/0.2.0"


def _badjson(files, m):
    b = files["findings.jsonl"]
    files["findings.jsonl"] = b + b"{not valid json\n"
    m["artifacts"]["findings"].update(sha256=sha(files["findings.jsonl"]), bytes=len(files["findings.jsonl"]),
                                      record_count=m["artifacts"]["findings"]["record_count"] + 1)


@case("invalid-json", ["invalid-json"], post=_badjson)
def _(r):
    pass


# --- references
@case("dangling-paper-ref", ["dangling-paper-ref"])
def _(r):
    new_finding(r, "finding:synth-zz-f1", paper_id="paper:synth-missing", passage_ids=[])


@case("dangling-passage-ref", ["dangling-passage-ref", "completeness-fabricated"])
def _(r):
    by_id(r, "findings", "finding_id", "finding:synth-a-f2")["passage_ids"].append("passage:synth-missing")


@case("dangling-finding-ref", ["dangling-finding-ref"])
def _(r):
    new_assessment(r, "assessment:synth-9", "finding:synth-missing", 1, "passage:synth-a-p1", "unclear",
                   "Synthetic rationale.", assessed_snapshot_sha256=SNAP_A_SHA)


@case("dangling-revision-ref", ["dangling-revision-ref"])
def _(r):
    new_assessment(r, "assessment:synth-9", "finding:synth-a-f2", 2, "passage:synth-a-p3", "unclear",
                   "Synthetic rationale.", assessed_snapshot_sha256=SNAP_A_SHA)


@case("passage-paper-mismatch", ["passage-paper-mismatch"])
def _(r):
    by_id(r, "findings", "finding_id", "finding:synth-a-f2")["passage_ids"].append("passage:synth-b-p1")


@case("snapshot-ref-mismatch", ["snapshot-ref-mismatch"])
def _(r):
    by_id(r, "passages", "passage_id", "passage:synth-a-p3")["source"]["snapshot_ref"] = "snapshot:synth-a-other"


# --- review / version
@case("assessed-snapshot-mismatch", ["assessed-snapshot-mismatch"])
def _(r):
    r["assessments"][0]["assessed_snapshot_sha256"] = OTHER_SHA


@case("revision-gap", ["revision-gap"])
def _(r):
    new_finding(r, "finding:synth-a-f1", revision=3, passage_ids=[], export_state="proposed",
                review_state="unreviewed", verification_level="unverified")


@case("approved-without-review", ["approved-without-review"])
def _(r):
    new_finding(r, "finding:synth-a-f4", export_state="approved-for-export", review_state="unreviewed",
                verification_level="unverified")


@case("review-without-assessment", ["review-without-assessment"])
def _(r):
    new_finding(r, "finding:synth-a-f4", export_state="proposed", review_state="human-reviewed",
                verification_level="unverified")


@case("verification-overclaim", ["verification-overclaim"])
def _(r):
    by_id(r, "findings", "finding_id", "finding:synth-a-f1")["verification_level"] = "independently-verified"


@case("quote-presence-as-support", ["quote-presence-as-support", "verification-overclaim"])
def _(r):
    a = r["assessments"][0]
    a["status"], a["rationale"] = "not_assessed", NA


# --- passages
@case("match-offset-inconsistent", ["match-offset-inconsistent"])
def _(r):
    s = by_id(r, "passages", "passage_id", "passage:synth-a-p1")["source"]
    s["start"], s["end"] = 50, 20  # end must exceed start (schema cannot express this)


def _approved_f4(r, pid):
    new_finding(r, "finding:synth-a-f4", export_state="approved-for-export", review_state="human-reviewed",
                verification_level="human-reviewed", passage_ids=[pid])
    new_assessment(r, "assessment:synth-9", "finding:synth-a-f4", 1, pid, "insufficient", "Synthetic rationale.",
                   assessed_snapshot_sha256=SNAP_A_SHA)


@case("unmatched-passage-approved", ["unmatched-passage-approved"])
def _(r):
    q = "toy experiment"
    r["passages"].append({
        "passage_id": "passage:synth-a-p4", "paper_id": "paper:synth-a",
        "quote_state": "included", "quote": q, "quote_sha256": sha(q),
        "source": {"snapshot_ref": "snapshot:synth-a-1", "view_id": "view:text-v1", "start": None, "end": None},
        "match": {"receipt_ref": "receipt:synth-a-p4", "status": "not_checked", "matcher": "unknown"},
        "producer_ids": {"evidence_ref": "evidence-synth-5"},
    })
    _approved_f4(r, "passage:synth-a-p4")


@case("ambiguous-passage-approved", ["ambiguous-passage-approved", "unmatched-passage-approved"])
def _(r):
    _approved_f4(r, "passage:synth-a-p3")


@case("quote-hash-mismatch", ["quote-hash-mismatch"])
def _(r):
    by_id(r, "passages", "passage_id", "passage:synth-a-p1")["quote_sha256"] = OTHER_SHA


@case("withheld-quote-inconsistent", ["withheld-quote-inconsistent"])
def _(r):
    p = by_id(r, "passages", "passage_id", "passage:synth-a-p3")
    p["quote"], p["quote_sha256"] = "withheld", sha("withheld")  # state included but placeholder literal (paper A redistributable: no leak)


# --- rights / privilege
@case("private-packet", ["private-packet-not-exportable"])
def _(r):
    r["manifest"]["access_scope"]["level"] = "private"


@case("embargoed-packet", ["private-packet-not-exportable"])
def _(r):
    r["manifest"]["access_scope"].update(level="embargoed", embargo_until="2027-01-01T00:00:00Z")


@case("embargo-expired-or-missing", ["embargo-expired-or-missing", "private-packet-not-exportable"])
def _(r):
    r["manifest"]["access_scope"].update(level="embargoed", embargo_until="2025-12-31T00:00:00Z")


@case("restricted-quote-leak", ["restricted-quote-leak"])
def _(r):
    p = by_id(r, "passages", "passage_id", "passage:synth-b-p1")
    p["quote_state"], p["quote"] = "included", QB


@case("approved-in-restricted-packet", ["approved-in-restricted-packet"])
def _(r):
    by_id(r, "papers", "paper_id", "paper:synth-a")["rights"]["redistribution"] = "restricted"
    for p in r["passages"]:  # avoid restricted-quote-leak: quotes of paper A become withheld
        if p["paper_id"] == "paper:synth-a":
            p["quote_state"], p["quote"] = "withheld-rights", "withheld"


@case("completeness-fabricated", ["completeness-fabricated"])
def _(r):
    r["manifest"]["export_status"]["withheld_counts"]["passages"] = 1


@case("silent-omission", ["silent-omission"])
def _(r):
    r["manifest"]["export_status"].update(
        state="withheld", reasons=["synthetic: withheld for rights review"],
        withheld_counts={"papers": 2, "findings": 3, "passages": 4, "assessments": 3})


def main():
    base = canonical()
    out = HERE / "invalid"
    if out.exists():
        shutil.rmtree(out)
    write_dir(HERE / "valid" / "canonical", build(base))
    for name in sorted(CASES):
        errors, mutate, post, ser = CASES[name]
        recs = copy.deepcopy(base)
        mutate(recs)
        write_dir(out / name, build(recs, post, ser), errors)
    print("wrote canonical + %d invalid cases" % len(CASES))


if __name__ == "__main__":
    main()
