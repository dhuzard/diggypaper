#!/usr/bin/env python3
"""Deterministic generator for finding-credit-companion/0.1.0 fixtures.

Writes, next to this script:

* ``packets/public/`` and ``packets/private/``: two synthetic source-evidence-packet/0.1.0
  packets with identical records (only ``access_scope.level`` differs);
* ``valid/public/credit.json`` (bound to the public packet) and
  ``valid/project-members/credit.json`` (bound to the private packet);
* ``invalid/<case>/credit.json`` plus ``expected.json`` (``packet`` to bind, ``errors``).

Stdlib only; no clock, no randomness; idempotent. Every invalid case is a valid companion plus
ONE mutation. All people are synthetic; DOIs use the 10.5555 example prefix; the only ORCID iD
is 0000-0002-1825-0097, ORCID's fictitious test identity (Josiah Carberry).
"""

import copy
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEP_VERSION = "source-evidence-packet/0.1.0"
FCC_VERSION = "finding-credit-companion/0.1.0"
TS = "2026-09-01T00:00:00Z"
TS_ASSESSED = "2026-09-02T00:00:00Z"
TS_APPROVED = "2026-09-03T00:00:00Z"
TS_CREATED = "2026-09-04T00:00:00Z"
TS_EXPIRES = "2027-03-01T00:00:00Z"
NA = "not-available"
TEST_ORCID = "0000-0002-1825-0097"

# Project-scoped pseudonyms (app.pseudonym.reviewer_reference, packet `rv-` form).
PROPOSER_1 = "rv-" + "1" * 32
PROPOSER_2 = "rv-" + "2" * 32
CURATOR_3 = "rv-" + "3" * 32
REVIEWER_A = "rv-" + "a" * 32
REVIEWER_B = "rv-" + "b" * 32
REVIEWER_C = "rv-" + "c" * 32
REVIEWER_D = "rv-" + "d" * 32


def sha(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def jsonl(records):
    lines = (
        json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for r in records
    )
    return "".join(line + "\n" for line in lines).encode("utf-8")


def pretty(obj):
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ------------------------------------------------------------------ packet
SNAP_A = (
    "Synthetic Credit Study A.\n"
    "Compound Q reduced synthetic marker R at week 2 in synthetic rats.\n"
    "At week 6 synthetic marker R returned to baseline in both groups.\n"
)
SNAP_B = (
    "Synthetic Credit Study B.\nCompound Q was associated with outcome S in a synthetic cohort.\n"
)
SNAP_C = "Synthetic Credit Study C (restricted rights, text never exported).\n"
QA1 = "Compound Q reduced synthetic marker R at week 2"
QA2 = "At week 6 synthetic marker R returned to baseline"
QB1 = "Compound Q was associated with outcome S"
QC1 = "restricted rights, text never exported"


def _span(text, quote):
    start = text.index(quote)
    return start, start + len(quote)


def _paper(pid, doi, authors, snap_text, redistribution, license_id):
    return {
        "paper_id": pid,
        "identifiers": {"doi": doi, "pmid": NA, "pmcid": NA},
        "title": f"Synthetic Credit Study {pid[-1].upper()}",
        "authors": authors,
        "source_type": "journal-article",
        "source_snapshot": {
            "state": "captured",
            "snapshot_ref": f"snapshot:{pid[6:]}-1",
            "sha256": sha(snap_text),
        },
        "rights": {
            "license": license_id,
            "redistribution": redistribution,
            "full_text_included": False,
        },
        "producer_ids": {"article_ref": f"article-{pid[6:]}"},
    }


def _passage(pid, paper, snap_text, quote, included=True, status="exact"):
    start, end = _span(snap_text, quote) if status == "exact" else (None, None)
    return {
        "passage_id": pid,
        "paper_id": paper["paper_id"],
        "quote_state": "included" if included else "withheld-rights",
        "quote": quote if included else "withheld",
        "quote_sha256": sha(quote),
        "source": {
            "snapshot_ref": paper["source_snapshot"]["snapshot_ref"],
            "view_id": "view:text-v1",
            "start": start,
            "end": end,
        },
        "match": {
            "receipt_ref": f"qm-{pid[8:]}" if status == "exact" else NA,
            "status": status,
            "matcher": "quote-matcher-v1" if status == "exact" else "unknown",
        },
        "producer_ids": {"evidence_ref": f"evidence-{pid[8:]}"},
    }


def _context(outcome, time, unit="animal"):
    return {
        "population": "synthetic rats",
        "exposure": "compound Q",
        "comparator": "vehicle",
        "outcome": outcome,
        "time": time,
        "experimental_unit": unit,
    }


def _finding(fid, rev, paper, statement, origin, passages, level, context, approved=True):
    return {
        "finding_id": fid,
        "revision": rev,
        "paper_id": paper["paper_id"],
        "statement": statement,
        "origin": origin,
        "export_state": "approved-for-export" if approved else "proposed",
        "review_state": "human-reviewed" if approved else "unreviewed",
        "verification_level": level if approved else "unverified",
        "context": context,
        "numeric_results": [],
        "passage_ids": [p["passage_id"] for p in passages],
        "producer_ids": {
            "answer_ref": f"answer-{fid[8:]}-r{rev}",
            "evidence_refs": [p["producer_ids"]["evidence_ref"] for p in passages],
            "prediction_ref": f"prediction-{fid[8:]}",
        },
        "created_at": TS,
    }


def _assessment(aid, finding, passage, paper, reviewer):
    return {
        "assessment_id": aid,
        "finding_id": finding["finding_id"],
        "finding_revision": finding["revision"],
        "passage_id": passage["passage_id"],
        "protocol": "seip-support-v1",
        "status": "supports",
        "rationale": "Synthetic rationale: the passage states the finding.",
        "reviewer_ref": reviewer,
        "assessed_at": TS_ASSESSED,
        "assessed_snapshot_sha256": paper["source_snapshot"]["sha256"],
        "producer_ids": {"support_assessment_ref": f"support-{aid[11:]}"},
    }


def packet_records():
    pa = _paper(
        "paper:credit-a",
        "10.5555/credit.a",
        ["Synthetic, Dana", "Synthetic, Eli"],
        SNAP_A,
        "allowed",
        "CC-BY-4.0",
    )
    pb = _paper(
        "paper:credit-b", "10.5555/credit.b", ["Synthetic, Fay"], SNAP_B, "allowed", "CC-BY-4.0"
    )
    pc = _paper(
        "paper:credit-c",
        "10.5555/credit.c",
        ["Synthetic, Gus"],
        SNAP_C,
        "restricted",
        "proprietary",
    )
    a1 = _passage("passage:credit-a-p1", pa, SNAP_A, QA1)
    a2 = _passage("passage:credit-a-p2", pa, SNAP_A, QA2)
    b1 = _passage("passage:credit-b-p1", pb, SNAP_B, QB1)
    c1 = _passage("passage:credit-c-p1", pc, SNAP_C, QC1, included=False, status="not_checked")
    # Two separately versioned findings of one article (same DOI, different results).
    f1r1 = _finding(
        "finding:credit-a-f1",
        1,
        pa,
        "Compound Q reduced marker R in synthetic rats.",
        "source-explicit",
        [a1],
        "source-verified",
        _context("marker R", "unknown"),
    )
    f1r2 = _finding(
        "finding:credit-a-f1",
        2,
        pa,
        "Compound Q reduced marker R at week 2 in synthetic rats.",
        "source-explicit",
        [a1],
        "source-verified",
        _context("marker R", "week 2"),
    )
    f2 = _finding(
        "finding:credit-a-f2",
        1,
        pa,
        "Marker R was back to baseline at week 6 in both groups.",
        "reviewer-interpretation",
        [a2],
        "independently-verified",
        _context("marker R", "week 6", "group"),
    )
    g1 = _finding(
        "finding:credit-b-f1",
        1,
        pb,
        "Compound Q was associated with outcome S.",
        "ai-paraphrase",
        [b1],
        "source-verified",
        _context("outcome S", "unknown", "unknown"),
    )
    h1 = _finding(
        "finding:credit-c-f1",
        1,
        pc,
        "Synthetic restricted-rights finding.",
        "ai-paraphrase",
        [c1],
        "unverified",
        _context("unknown", "unknown", "unknown"),
        approved=False,
    )
    assessments = [
        _assessment("assessment:credit-a1", f1r1, a1, pa, REVIEWER_A),
        _assessment("assessment:credit-a2", f1r2, a1, pa, REVIEWER_B),
        _assessment("assessment:credit-a3", f2, a2, pa, REVIEWER_C),
        _assessment("assessment:credit-a4", f2, a2, pa, REVIEWER_D),
        _assessment("assessment:credit-b1", g1, b1, pb, REVIEWER_A),
    ]
    return {
        "papers": [pa, pb, pc],
        "findings": [f1r1, f1r2, f2, g1, h1],
        "passages": [a1, a2, b1, c1],
        "assessments": assessments,
    }


def write_packet(directory, level):
    records = packet_records()
    files = {f"{k}.jsonl": jsonl(v) for k, v in records.items()}
    manifest = {
        "schema_version": SEP_VERSION,
        "packet_id": "packet:credit-synthetic",
        "created_at": TS_ASSESSED,
        "creation_method": "synthetic-fixture",
        "producer": {
            "name": "diggypaper-fixture-builder",
            "commit": "synthetic-fixture",
            "schema_refs": [{"name": "seip-support-v1", "version": "1"}],
        },
        "artifacts": {
            k: {
                "path": f"{k}.jsonl",
                "sha256": sha(files[f"{k}.jsonl"]),
                "bytes": len(files[f"{k}.jsonl"]),
                "record_count": len(v),
            }
            for k, v in records.items()
        },
        "licenses": [
            {"license_id": "CC0-1.0", "applies_to": "packet-metadata"},
            {"license_id": "CC-BY-4.0", "applies_to": "quotes"},
        ],
        "access_scope": {"level": level, "redistributable_full_text": False},
        "export_status": {
            "state": "complete",
            "withheld_counts": {"papers": 0, "findings": 0, "passages": 0, "assessments": 0},
            "reasons": [],
        },
    }
    files["manifest.json"] = pretty(manifest)
    directory.mkdir(parents=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return sha(files["manifest.json"])


# --------------------------------------------------------------- companion
def _disclosure(consent="not-granted", controller="not-granted", membership="active", ref=NA):
    return {
        "person_consent": consent,
        "controller_authorization": controller,
        "membership": membership,
        "consent_ref": ref,
    }


def _contribution(
    cid, capacity, status, contributor, roles=(), assessments=(), blinding=None, disclosure=None
):
    return {
        "contribution_id": cid,
        "capacity": capacity,
        "status": status,
        "contributor": contributor,
        "credit_roles": list(roles),
        "assessment_ids": list(assessments),
        "review_blinding": blinding
        or ("unknown" if capacity == "support-assessor" else "not-applicable"),
        "disclosure": disclosure or _disclosure(),
    }


def pseudonym(ref):
    return {"kind": "pseudonym", "ref": ref}


WITHHELD = {"kind": "withheld", "ref": "attribution-withheld"}


def role(name, degree, n):
    return {"role": name, "degree": degree, "declaration_ref": f"declaration:synth-{n}"}


def _binding(digest, records, fid, rev):
    f = next(x for x in records["findings"] if (x["finding_id"], x["revision"]) == (fid, rev))
    paper = next(p for p in records["papers"] if p["paper_id"] == f["paper_id"])
    return {
        "packet_digest": digest,
        "finding_id": fid,
        "finding_revision": rev,
        "finding_origin": f["origin"],
        "paper_id": f["paper_id"],
        "source_snapshot_ref": paper["source_snapshot"]["snapshot_ref"],
        "source_snapshot_sha256": paper["source_snapshot"]["sha256"],
        "passage_ids": list(f["passage_ids"]),
        "assessment_protocol": "seip-support-v1",
        "assessment_ids": sorted(
            a["assessment_id"]
            for a in records["assessments"]
            if (a["finding_id"], a["finding_revision"]) == (fid, rev)
        ),
    }


QUOTE_MATCHER = {
    "agent_type": "software",
    "name": "quote-matcher-v1",
    "version": "1",
    "activity": "quote-matching",
    "run_ref": NA,
}


def _model(activity, fid):
    return {
        "agent_type": "ml-model",
        "name": "synthetic-extraction-model",
        "version": "0.0-synthetic",
        "activity": activity,
        "run_ref": f"prediction-{fid[8:]}",
    }


def public_entries(digest, records):
    curator_one = {
        "kind": "disclosed",
        "display_name": "Synthetic Curator One",
        "orcid": {
            "id": TEST_ORCID,
            "verification": "oauth-verified",
            "verified_at": TS_ASSESSED,
            "verification_ref": "orcid-verification:synth-1",
        },
    }
    curator_two = {
        "kind": "disclosed",
        "display_name": "Synthetic Curator Two",
        "orcid": {
            "id": TEST_ORCID,  # same string, but only *claimed*: never merged with Curator One
            "verification": "claimed-unverified",
            "verified_at": NA,
            "verification_ref": NA,
        },
    }
    consented = _disclosure("granted", "granted", "active", "consent:synth-1")
    consented_two = _disclosure("granted", "granted", "active", "consent:synth-2")
    return [
        {
            "entry_id": "credit-entry:credit-a-f1-r2",
            "binding": _binding(digest, records, "finding:credit-a-f1", 2),
            "contributions": [
                _contribution(
                    "contribution:a-f1-proposer",
                    "finding-proposer",
                    "source-declared",
                    pseudonym(PROPOSER_1),
                ),
                _contribution(
                    "contribution:a-f1-curator",
                    "finding-curator",
                    "confirmed",
                    curator_one,
                    [role("Data curation", "lead", 1)],
                    disclosure=consented,
                ),
                _contribution(
                    "contribution:a-f1-assessor-b",
                    "support-assessor",
                    "source-declared",
                    pseudonym(REVIEWER_B),
                    assessments=["assessment:credit-a2"],
                    blinding="unblinded",
                ),
            ],
            "production": [QUOTE_MATCHER],
        },
        {
            "entry_id": "credit-entry:credit-a-f2-r1",
            "binding": _binding(digest, records, "finding:credit-a-f2", 1),
            "contributions": [
                _contribution(
                    "contribution:a-f2-proposer",
                    "finding-proposer",
                    "source-declared",
                    pseudonym(PROPOSER_2),
                ),
                _contribution(
                    "contribution:a-f2-curator",
                    "finding-curator",
                    "self-declared",
                    curator_two,
                    [role("Investigation", "supporting", 2)],
                    disclosure=consented_two,
                ),
                _contribution(
                    "contribution:a-f2-assessor-c",
                    "support-assessor",
                    "source-declared",
                    pseudonym(REVIEWER_C),
                    [role("Validation", "equal", 3)],
                    assessments=["assessment:credit-a3"],
                    blinding="blinded",
                ),
                _contribution(
                    "contribution:a-f2-assessor-d",
                    "support-assessor",
                    "withheld",
                    WITHHELD,
                    assessments=["assessment:credit-a4"],
                    blinding="blinded",
                ),
            ],
            "production": [QUOTE_MATCHER, _model("extraction", "finding:credit-a-f2")],
        },
        {
            "entry_id": "credit-entry:credit-b-f1-r1",
            "binding": _binding(digest, records, "finding:credit-b-f1", 1),
            "contributions": [
                _contribution(
                    "contribution:b-f1-proposer", "finding-proposer", "withheld", WITHHELD
                ),
                _contribution(
                    "contribution:b-f1-assessor-a",
                    "support-assessor",
                    "source-declared",
                    pseudonym(REVIEWER_A),
                    assessments=["assessment:credit-b1"],
                ),
                _contribution(
                    "contribution:b-f1-team",
                    "project-team",
                    "confirmed",
                    WITHHELD,
                    [role("Project administration", "lead", 4)],
                ),
            ],
            "production": [QUOTE_MATCHER, _model("paraphrase", "finding:credit-b-f1")],
        },
    ]


def companion(digest, records, audience_level):
    entries = public_entries(digest, records)
    doc = {
        "schema_version": FCC_VERSION,
        "companion_id": f"credit:synthetic-{audience_level}",
        "created_at": TS_CREATED,
        "creation_method": "synthetic-fixture",
        "producer": {"name": "diggypaper-fixture-builder", "commit": "synthetic-fixture"},
        "packet": {
            "schema_version": SEP_VERSION,
            "packet_id": "packet:credit-synthetic",
            "packet_digest": digest,
        },
        "credit_semantics": {
            "role_vocabulary": "CRediT ANSI/NISO Z39.104-2022",
            "implies_authorship": False,
        },
    }
    if audience_level == "public":
        doc["audience"] = {
            "level": "public",
            "recipient_ref": NA,
            "purpose": "Synthetic fixture: agreed public credit statements.",
            "expires_at": NA,
        }
        doc["release"] = {
            "state": "controller-approved",
            "approval_ref": "approval:synth-1",
            "approved_at": TS_APPROVED,
        }
        # The proposed finding on the restricted paper is not shared publicly: counted, not hidden.
        doc["export_status"] = {
            "state": "partial",
            "withheld_counts": {"entries": 1, "contributions": 0},
            "reasons": ["unapproved-finding=1"],
        }
    else:
        # Private trail for scope-bound project members: includes the proposed finding.
        entries.append(
            {
                "entry_id": "credit-entry:credit-c-f1-r1",
                "binding": _binding(digest, records, "finding:credit-c-f1", 1),
                "contributions": [
                    _contribution(
                        "contribution:c-f1-proposer",
                        "finding-proposer",
                        "source-declared",
                        pseudonym(PROPOSER_1),
                    ),
                    _contribution(
                        "contribution:c-f1-curator",
                        "finding-curator",
                        "self-declared",
                        pseudonym(CURATOR_3),
                        [role("Data curation", "equal", 5)],
                    ),
                ],
                "production": [_model("paraphrase", "finding:credit-c-f1")],
            }
        )
        doc["audience"] = {
            "level": "project-members",
            "recipient_ref": NA,
            "purpose": "Synthetic fixture: private source and credit trail for project members.",
            "expires_at": TS_EXPIRES,
        }
        doc["release"] = {"state": "not-released", "approval_ref": NA, "approved_at": NA}
        doc["export_status"] = {
            "state": "complete",
            "withheld_counts": {"entries": 0, "contributions": 0},
            "reasons": [],
        }
    doc["entries"] = entries
    return doc


# ----------------------------------------------------------- invalid cases
def _contrib(doc, cid):
    return next(
        c for e in doc["entries"] for c in e["contributions"] if c["contribution_id"] == cid
    )


def _stale(doc):
    e = doc["entries"][0]
    e["binding"]["finding_revision"] = 1
    e["binding"]["assessment_ids"] = ["assessment:credit-a1"]
    e["contributions"] = e["contributions"][:2]


def _restricted(doc):
    doc["entries"].append(
        {
            "entry_id": "credit-entry:credit-c-f1-r1",
            "binding": _binding(
                doc["packet"]["packet_digest"], packet_records(), "finding:credit-c-f1", 1
            ),
            "contributions": [],
            "production": [],
        }
    )


def _forbidden_field(doc):
    _contrib(doc, "contribution:a-f1-curator")["contributor"]["email"] = "curator"


def _correlator(doc):
    doc["entries"][1]["production"][1]["run_ref"] = "f" * 64


def _software_person(doc):
    _contrib(doc, "contribution:a-f2-curator")["contributor"]["display_name"] = (
        "synthetic-extraction-model"
    )


def _withdrawn_roles(doc):
    c = _contrib(doc, "contribution:b-f1-team")
    c["status"] = "withdrawn"


def _withheld_visible(doc):
    _contrib(doc, "contribution:a-f1-proposer")["status"] = "withheld"


def _assessor_binding(doc):
    _contrib(doc, "contribution:a-f1-proposer")["assessment_ids"] = ["assessment:credit-a2"]


def _duplicate_entry(doc):
    dup = copy.deepcopy(doc["entries"][0])
    dup["entry_id"] = "credit-entry:credit-a-f1-r2-again"
    for c in dup["contributions"]:
        c["contribution_id"] += "-again"
    doc["entries"].append(dup)


def _duplicate_id(doc):
    _contrib(doc, "contribution:a-f2-proposer")["contribution_id"] = "contribution:a-f1-proposer"


def _named_no_expiry(doc):
    doc["audience"]["level"] = "named-recipient"
    doc["audience"]["recipient_ref"] = "recipient:synth-consumer"


CASES = {
    "schema-invalid": (_forbidden_field, ["schema-invalid"]),
    "non-credit-role": (
        lambda d: _contrib(d, "contribution:a-f1-curator")["credit_roles"].append(
            role("Peer review", "lead", 9)
        ),
        ["schema-invalid"],
    ),
    "ai-with-orcid": (
        lambda d: d["entries"][1]["production"][1].update({"orcid": TEST_ORCID}),
        ["schema-invalid"],
    ),
    "implies-authorship": (
        lambda d: d["credit_semantics"].update({"implies_authorship": True}),
        ["schema-invalid"],
    ),
    "unsupported-schema-version": (
        lambda d: d.update({"schema_version": "finding-credit-companion/0.2.0"}),
        ["unsupported-schema-version"],
    ),
    "packet-digest-mismatch": (
        lambda d: d["packet"].update({"packet_digest": sha("another packet")}),
        ["packet-digest-mismatch"],
    ),
    "entry-packet-digest-mismatch": (
        lambda d: d["entries"][0]["binding"].update({"packet_digest": sha("another packet")}),
        ["packet-digest-mismatch"],
    ),
    "stale-finding-revision": (_stale, ["stale-finding-revision"]),
    "dangling-finding-ref": (
        lambda d: d["entries"][0]["binding"].update({"finding_id": "finding:credit-z"}),
        ["dangling-finding-ref"],
    ),
    "finding-binding-mismatch": (
        lambda d: d["entries"][1]["binding"].update({"finding_origin": "source-explicit"}),
        ["finding-binding-mismatch"],
    ),
    "evidence-digest-changed": (
        lambda d: d["entries"][0]["binding"].update({"source_snapshot_sha256": sha("edited")}),
        ["evidence-digest-changed"],
    ),
    "evidence-binding-mismatch": (
        lambda d: d["entries"][1]["binding"].update({"passage_ids": ["passage:credit-a-p1"]}),
        ["evidence-binding-mismatch"],
    ),
    "assessor-pseudonym-mismatch": (
        lambda d: _contrib(d, "contribution:a-f1-assessor-b").update(
            {"contributor": pseudonym(REVIEWER_A)}
        ),
        ["assessor-pseudonym-mismatch"],
    ),
    "assessor-binding-mismatch": (_assessor_binding, ["assessor-binding-mismatch"]),
    "consent-missing": (
        lambda d: _contrib(d, "contribution:a-f1-curator")["disclosure"].update(
            {"person_consent": "not-granted"}
        ),
        ["consent-missing"],
    ),
    "consent-revoked": (
        lambda d: _contrib(d, "contribution:a-f1-curator")["disclosure"].update(
            {"controller_authorization": "revoked"}
        ),
        ["consent-revoked"],
    ),
    "membership-revoked": (
        lambda d: _contrib(d, "contribution:a-f2-curator")["disclosure"].update(
            {"membership": "revoked"}
        ),
        ["consent-revoked"],
    ),
    "blind-reviewer-disclosed": (
        lambda d: _contrib(d, "contribution:a-f2-assessor-c").update(
            {
                "contributor": {
                    "kind": "disclosed",
                    "display_name": "Synthetic Reviewer C",
                    "orcid": NA,
                },
                "disclosure": _disclosure("granted", "granted", "active", "consent:synth-3"),
            }
        ),
        ["blind-reviewer-disclosed"],
    ),
    "inactive-assertion-disclosed": (
        lambda d: _contrib(d, "contribution:a-f1-curator").update(
            {"status": "disputed", "credit_roles": []}
        ),
        ["inactive-assertion-disclosed"],
    ),
    "credit-role-not-asserted": (_withdrawn_roles, ["credit-role-not-asserted"]),
    "duplicate-credit-role": (
        lambda d: _contrib(d, "contribution:a-f1-curator")["credit_roles"].append(
            role("Data curation", "supporting", 8)
        ),
        ["duplicate-credit-role"],
    ),
    "withheld-status-inconsistent": (_withheld_visible, ["withheld-status-inconsistent"]),
    "orcid-verification-inconsistent": (
        lambda d: _contrib(d, "contribution:a-f2-curator")["contributor"]["orcid"].update(
            {"verified_at": TS_ASSESSED}
        ),
        ["orcid-verification-inconsistent"],
    ),
    "orcid-checksum-invalid": (
        lambda d: _contrib(d, "contribution:a-f1-curator")["contributor"]["orcid"].update(
            {"id": "0000-0002-1825-0098"}
        ),
        ["orcid-checksum-invalid"],
    ),
    "private-identifier-leak": (
        lambda d: _contrib(d, "contribution:a-f1-curator")["contributor"].update(
            {"display_name": "curator.one@example.org"}
        ),
        ["private-identifier-leak"],
    ),
    "cross-project-correlator": (_correlator, ["private-identifier-leak"]),
    "software-as-person": (_software_person, ["software-as-person"]),
    "release-not-approved": (
        lambda d: d.update(
            {"release": {"state": "not-released", "approval_ref": NA, "approved_at": NA}}
        ),
        ["release-not-approved"],
    ),
    "release-inconsistent": (
        lambda d: d["release"].update({"approved_at": "2026-12-01T00:00:00Z"}),
        ["release-inconsistent"],
    ),
    "handoff-expired": (
        lambda d: d["audience"].update({"expires_at": "2026-09-01T00:00:00Z"}),
        ["handoff-expired"],
    ),
    "handoff-expiry-missing": (_named_no_expiry, ["handoff-expiry-missing"]),
    "audience-inconsistent": (
        lambda d: d["audience"].update({"recipient_ref": "recipient:synth-consumer"}),
        ["audience-inconsistent"],
    ),
    "restricted-paper-shared": (
        _restricted,
        ["restricted-redistribution", "unapproved-finding-shared"],
    ),
    "duplicate-entry": (_duplicate_entry, ["duplicate-entry"]),
    "duplicate-id": (_duplicate_id, ["duplicate-id"]),
    "completeness-fabricated": (
        lambda d: d["export_status"].update({"state": "complete"}),
        ["completeness-fabricated"],
    ),
    "silent-omission": (
        lambda d: d["export_status"].update({"state": "withheld"}),
        ["silent-omission"],
    ),
}


def main():
    for sub in ("packets", "valid", "invalid"):
        shutil.rmtree(HERE / sub, ignore_errors=True)
    digests = {
        level: write_packet(HERE / "packets" / level, level) for level in ("public", "private")
    }
    records = packet_records()
    public = companion(digests["public"], records, "public")
    members = companion(digests["private"], records, "project-members")
    for name, doc in (("public", public), ("project-members", members)):
        (HERE / "valid" / name).mkdir(parents=True)
        (HERE / "valid" / name / "credit.json").write_bytes(pretty(doc))

    def write_case(name, data, errors, packet="public"):
        d = HERE / "invalid" / name
        d.mkdir(parents=True)
        (d / "credit.json").write_bytes(data)
        (d / "expected.json").write_bytes(pretty({"errors": errors, "packet": packet}))

    for name, (mutate, errors) in CASES.items():
        doc = copy.deepcopy(public)
        mutate(doc)
        write_case(name, pretty(doc), errors)
    # Public credit for a private packet (correct digest): the audience is wider than the packet.
    wide = copy.deepcopy(public)
    wide["packet"]["packet_digest"] = digests["private"]
    for e in wide["entries"]:
        e["binding"]["packet_digest"] = digests["private"]
    write_case(
        "audience-exceeds-packet-scope", pretty(wide), ["audience-exceeds-packet-scope"], "private"
    )
    write_case("invalid-json", b'{"schema_version": ', ["invalid-json"])
    write_case(
        "non-canonical-encoding",
        (json.dumps(public, sort_keys=False, indent=2, ensure_ascii=False) + "\n").encode(),
        ["non-canonical-encoding"],
    )


if __name__ == "__main__":
    main()
