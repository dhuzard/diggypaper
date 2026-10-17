# source-evidence-packet/0.1.0

Status: draft contract for [issue #47](https://github.com/dhuzard/diggypaper/issues/47). Docs, JSON Schemas, synthetic fixtures and an offline validator only. No database migration, API, UI or LLM call is added.

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Scope and non-goals

A **Source Evidence Packet** (SEP) is a versioned, file-based export of *source-paper findings* with their passage evidence and native human support judgments, produced by Diggypaper and consumed by downstream tools.

Three kinds of object MUST NOT be conflated:

| Object | Owner | In this packet? |
|---|---|---|
| **Source-paper finding**: an atomic statement about what a published paper reports, with passage evidence and (optionally) human support assessments | Diggypaper (this contract) | Yes |
| **Review-authored claim**: a claim a reviewer makes in a ComputationalReviewTemplate (CRT) review | CRT | No. A finding can be *cited by* a CRT review; it never becomes one by export. |
| **ORAtlas TRUST record**: a trust or reliability judgment about a claim or source | ORAtlas | No. There is no TRUST, score, or confidence field in this packet. |

Non-goals: full text or PDFs (never included, section 9); effect-size computation; independent-replication tracking; adjudication or aggregation of conflicting reviewers; any scientific-support conclusion beyond the recorded human assessments; database or API changes; packaging beyond the optional RO-Crate wrapper.

Quote presence, quote-match success and model agreement are not support. Only an `EvidenceAssessment` records support, and only a human creates one.

## 2. Package layout

A packet is a directory with exactly these files (any other file is `unexpected-file`):

```
manifest.json  papers.jsonl  findings.jsonl  passages.jsonl  assessments.jsonl
```

Encoding rules (violations are `non-canonical-encoding`):

- UTF-8, `\n` line endings, final newline, no BOM.
- JSONL: one JSON object per line, keys sorted, separators `,` and `:` (no spaces), non-ASCII unescaped (`ensure_ascii=False`).
- `manifest.json`: pretty-printed JSON with sorted keys, final newline.
- Record order is not significant, but producers SHOULD sort by primary id (and `revision` for findings) for reproducible hashes.

JSON Schemas (2020-12, `additionalProperties: false`) are in `schemas/`. They check structure only; the semantic rules in section 7 are enforced by the validator.

## 3. Common vocabulary

### 3.1 Explicit unknowns

Absence of knowledge MUST be stated, never expressed as `null`, an empty string or a missing key. `null` appears in exactly one place: `passage.source.start` / `end`.

| Literal | Meaning |
|---|---|
| `"unknown"` | The value exists but the producer does not know it. |
| `"not-assessed"` | Nobody has looked. |
| `"not-available"` | It does not exist or cannot be obtained. |
| `"withheld"` | It exists and is restricted (rights/privilege). |

Producers MUST NOT invent a value to avoid these literals. Consumers MUST NOT coerce them to defaults, empty values or "no".

### 3.2 Identifiers, hashes, time

- IDs match `^[a-z][a-z0-9-]*:[A-Za-z0-9._-]{1,100}$` with prefixes `packet:`, `paper:`, `finding:`, `passage:`, `assessment:`. IDs are opaque, stable and producer-assigned. They MUST NOT be derived from, or compared via, text.
- Hashes: `sha256:` followed by 64 lowercase hex digits.
- Timestamps: RFC 3339 UTC with `Z` suffix.
- Text offsets: zero-based, half-open, Unicode code points of the referenced snapshot view (same convention as SEIP provenance v0.1).

### 3.3 Status vocabularies

| Field | Values |
|---|---|
| `manifest.creation_method` | `producer-export`, `synthetic-fixture`, `manual` |
| `manifest.access_scope.level` | `public`, `restricted`, `private`, `embargoed` |
| `manifest.export_status.state` | `complete`, `partial`, `withheld` |
| `paper.source_type` | `journal-article`, `preprint`, `review`, `dataset`, `other` |
| `paper.source_snapshot.state` | `captured`, `not-captured`, `unavailable` |
| `paper.rights.redistribution` | `allowed`, `metadata-only`, `restricted`, `unknown` |
| `finding.origin` | `source-explicit`, `ai-paraphrase`, `reviewer-interpretation` |
| `finding.export_state` | `proposed`, `approved-for-export` |
| `finding.review_state` | `unreviewed`, `human-reviewed` |
| `finding.verification_level` | `unverified`, `human-reviewed`, `source-verified`, `independently-verified` |
| `numeric_results[].n.definition` | `article-level`, `experimental-unit`, `group`, `unknown` |
| `passage.quote_state` | `included`, `withheld-rights` |
| `passage.match.status` | `exact`, `normalized`, `not_found`, `ambiguous`, `invalid`, `source_unavailable`, `not_checked`, `incomplete` (identical to Diggypaper `QuoteMatchReceipt`) |
| `assessment.status` | `supports`, `contradicts`, `insufficient`, `unclear`, `not_assessed`, `unknown`, `restricted` |

`assessment.status` extends the `seip-support-v1` judgments (`supports`, `contradicts`, `insufficient`, `unclear`, `not_assessed`) with `unknown` and `restricted` so that an exporter can say "exists but not disclosable" without fabricating a judgment. Only the first four are *judgments*.

## 4. Normative field tables

"Req" = MUST be present. Every object is closed: unknown keys are `schema-invalid:<file>`.

### 4.1 `manifest.json`

| Field | Req | Type / constraint |
|---|---|---|
| `schema_version` | MUST | const `"source-evidence-packet/0.1.0"` |
| `packet_id` | MUST | `packet:` id |
| `created_at` | MUST | timestamp |
| `creation_method` | MUST | enum (3.3) |
| `producer` | MUST | `{name: string, commit: 40-hex or "synthetic-fixture", schema_refs: [{name, version}]}` (e.g. `evidence-envelope-v1`, `seip-support-v1`, `evidence-links-v1`) |
| `artifacts` | MUST | object with exactly keys `papers`, `findings`, `passages`, `assessments`; each `{path, sha256, bytes, record_count}`. `path` is the fixed filename; `sha256` and `bytes` cover the exact file bytes. |
| `licenses` | MUST | array, at least 1: `{license_id: SPDX id or "proprietary" or "unknown", applies_to: "packet-metadata" or "quotes" or "source-text"}` |
| `access_scope` | MUST | `{level, embargo_until, redistributable_full_text}`. `embargo_until` is a timestamp, MUST be present iff `level` is `embargoed`. `redistributable_full_text` MUST be `false`. |
| `export_status` | MUST | `{state, withheld_counts: {papers, findings, passages, assessments: int>=0}, reasons: [string]}` |
| `ro_crate` | MAY | `{path, sha256, authority: "manifest"}`. Packaging only; manifest + JSONL remain authoritative. The crate MUST NOT contain PDFs or full text. |

`export_status`: `complete` requires all withheld counts 0; `partial` requires some count >0; `withheld` requires every artifact `record_count` 0 and non-empty `reasons`.

### 4.2 `papers.jsonl` (Paper)

| Field | Req | Type / constraint |
|---|---|---|
| `paper_id` | MUST | `paper:` id, unique |
| `identifiers` | MUST | `{doi, pmid, pmcid}`, each a string or `"not-available"`; at least one MUST be a real value |
| `title` | MUST | string |
| `authors` | MUST | array of strings: the **published-article** authors (section 5.3). Not claim origin. |
| `source_type` | MUST | enum |
| `source_snapshot` | MUST | `{state, snapshot_ref, sha256}`; `sha256` is a hash iff `state` is `captured`, else `"not-available"` |
| `rights` | MUST | `{license, redistribution, full_text_included}`; `full_text_included` MUST be `false` |
| `producer_ids` | MUST | `{article_ref}` |

### 4.3 `findings.jsonl` (Finding)

| Field | Req | Type / constraint |
|---|---|---|
| `finding_id` | MUST | `finding:` id; stable across revisions |
| `revision` | MUST | int >=1; `(finding_id, revision)` unique; immutable |
| `paper_id` | MUST | existing paper |
| `statement` | MUST | one atomic statement |
| `origin` | MUST | enum: who *worded* the statement, not who authored the article |
| `export_state` | MUST | enum |
| `review_state` | MUST | enum |
| `verification_level` | MUST | enum (rules in 5.4) |
| `context` | MUST | `{population, exposure, comparator, outcome, time, experimental_unit}`, each a non-empty string (`"unknown"` etc. allowed) |
| `numeric_results` | MUST | array, empty if none. Items: `{name: string, value: number, unit: string ("unknown" allowed), n: {value: int>=0, definition: enum}}` |
| `passage_ids` | MUST | array of `passage:` ids; MAY be empty only while `review_state` is `unreviewed` and `export_state` is `proposed` |
| `producer_ids` | MUST | `{answer_ref?, evidence_refs?: [..], prediction_ref?}` original Diggypaper producer IDs, preserved verbatim |
| `created_at` | MUST | timestamp |

There is deliberately **no** field for effect size, independent replication, source validation status beyond `verification_level`, or any TRUST score. Producers MUST NOT add one; the schema rejects it.

### 4.4 `passages.jsonl` (PassageEvidence)

| Field | Req | Type / constraint |
|---|---|---|
| `passage_id` | MUST | `passage:` id, unique |
| `paper_id` | MUST | existing paper |
| `quote_state` | MUST | enum |
| `quote` | MUST | the quote string if `included`; the literal `"withheld"` iff `withheld-rights` |
| `quote_sha256` | MUST | SHA-256 of the UTF-8 quote bytes; MUST be present even when the quote is withheld so it stays verifiable |
| `source` | MUST | `{snapshot_ref, view_id, start, end}`; `start` int>=0 or `null`; `end` int>`start` or `null` |
| `match` | MUST | `{receipt_ref, status, matcher}`; `matcher` is `"quote-matcher-v1"` or `"unknown"` |
| `producer_ids` | MUST | `{evidence_ref}` |

`start`/`end` MUST be non-null when `status` is `exact` or `normalized`, and MUST be `null` for every other status (`match-offset-inconsistent`).

### 4.5 `assessments.jsonl` (EvidenceAssessment)

| Field | Req | Type / constraint |
|---|---|---|
| `assessment_id` | MUST | `assessment:` id, unique |
| `finding_id`, `finding_revision` | MUST | an existing finding revision |
| `passage_id` | MUST | existing passage |
| `protocol` | MUST | const `"seip-support-v1"` |
| `status` | MUST | enum |
| `rationale` | MUST | non-empty if `status` is `supports`, `contradicts`, `insufficient` or `unclear`; otherwise the literal `"not-available"` |
| `reviewer_ref` | MUST | `^rv-[0-9a-f]{16,64}$`, project-scoped pseudonym (the `app.pseudonym.reviewer_reference` derivation). MUST NOT be a raw user id or email. |
| `assessed_at` | MUST | timestamp |
| `assessed_snapshot_sha256` | MUST | hash of the snapshot the reviewer saw |
| `producer_ids` | MUST | `{support_assessment_ref}` |

Assessments are native human judgments. An exporter MUST NOT synthesize one from quote matching, answer agreement or model confidence. Conflicting reviewers' assessments stay as separate records; the packet does not adjudicate.

## 5. Semantics

### 5.1 Identity and lineage

- A finding is identified by `finding_id` plus an immutable `revision`. A change to the statement, context, numbers, passages or verification creates a new revision line; the old line is never edited. Revisions for a finding_id MUST be exactly 1..n (`revision-gap`). Assessments bind to one revision and MUST NOT carry over to another.
- Producer IDs (`answer_ref`, `evidence_refs`, `prediction_ref`, `evidence_ref`, `support_assessment_ref`, `article_ref`, `receipt_ref`) are preserved verbatim for audit. They are lineage, not packet identity.
- Lineage chain: `Prediction/AnswerRecord` (`prediction_ref`, `answer_ref`) -> `EvidenceRecord` (`evidence_ref`) -> `QuoteMatchReceipt` (`receipt_ref`) -> `SourceSnapshot` (`snapshot_ref`) -> reviewer's `EvidenceSupportAssessment` (`support_assessment_ref`, `reviewer_ref`).
- Identity MUST NOT be merged by text. Two findings with identical or near-identical `statement` from the same or different papers stay separate unless the producer assigns the same `finding_id`. Two findings of one paper with different outcome/time/context are distinct. Consumers MUST NOT deduplicate, cluster or match findings by statement, quote or hash of text.

### 5.2 Finding origin vs article authors

`paper.authors` identifies who published the article. `finding.origin` identifies who worded the finding: `source-explicit` (taken from the paper's own statement), `ai-paraphrase` (model-worded), `reviewer-interpretation` (human-worded reading). A consumer MUST NOT attribute an `ai-paraphrase` or `reviewer-interpretation` finding to the article authors, and MUST NOT present a finding as a review-authored claim.

### 5.3 Verification levels

| Level | Meaning | Required evidence (else `verification-overclaim`) |
|---|---|---|
| `unverified` | No human judgment recorded | `review_state` is `unreviewed`, or no judgment-status assessment |
| `human-reviewed` | A human recorded a judgment | at least 1 assessment with `supports`, `contradicts`, `insufficient` or `unclear` on this revision |
| `source-verified` | Human judgment tied to a matched, captured source | the above, plus at least 1 `supports`/`contradicts`-assessed passage whose match is `exact` or `normalized`, and the paper snapshot `captured` |
| `independently-verified` | Two reviewers agree there is a judgment | the above, plus at least 2 distinct `reviewer_ref` with non-`unknown` judgments on the same revision |

`quote-presence-as-support`: a finding at `source-verified` or above whose only assessments are `not_assessed`, `unknown` or `restricted` is rejected. `review_state: human-reviewed` requires at least one assessment for that revision (`review-without-assessment`). `export_state: approved-for-export` requires `review_state: human-reviewed` (`approved-without-review`).

"Independently-verified" is a count of distinct pseudonymous reviewers in this packet; it does not assert qualification, blinding, or Stage A lock (those are protocol controls outside this contract).

### 5.4 Sample size

`numeric_results[].n` records what N counts. **Article-level N** (e.g. total enrolled/animals in the paper) and **experimental-unit N** (the unit the statistic is computed on: animal, litter, cage, cell, etc.) are different quantities and MUST be distinguished by `n.definition`. `context.experimental_unit` names the unit. If the definition is not stated, use `unknown`; do not default to `article-level`. A consumer MUST NOT treat an `article-level` N as the statistical N, or sum Ns across results. Effect sizes are not part of this contract; when absent they stay absent.

### 5.5 Timestamps and source change

- `assessed_snapshot_sha256` MUST equal the paper's `source_snapshot.sha256`; otherwise the assessment was made against different source text (`assessed-snapshot-mismatch`, "mismatched review"). A paper whose snapshot is not `captured` has sha256 `"not-available"` and cannot carry a matching assessment of a hash.
- `passage.source.snapshot_ref` MUST equal the paper's `source_snapshot.snapshot_ref` (`snapshot-ref-mismatch`).
- If the source changes, the producer MUST create a new snapshot and re-review; existing assessments MUST NOT be re-pointed. Stale reviews remain in an older packet.
- The validator performs no clock reads. `created_at` and `assessed_at` are producer assertions, not verified. `embargo_until` is compared only to `manifest.created_at`.

### 5.6 Rights, copyright and privilege

- Full text and PDFs never appear in a packet; `redistributable_full_text` and `full_text_included` are const `false` (`full-text-flag`).
- Quotes are included only where `paper.rights.redistribution` is `allowed`. For `metadata-only`, `restricted` or `unknown` the passage MUST use `quote_state: withheld-rights`, `quote: "withheld"` and a real `quote_sha256` (`restricted-quote-leak`, `withheld-quote-inconsistent`).
- A finding of a paper whose redistribution is `restricted` or `unknown` MUST NOT be `approved-for-export` (`approved-in-restricted-packet`).
- `access_scope.level` `private` or `embargoed` packets are refused on import (`private-packet-not-exportable`). The `--allow-restricted` mode downgrades this to a warning for trusted internal use only. `embargoed` requires `embargo_until` (`embargo-expired-or-missing`).
- Reviewer identity is pseudonymous only. No raw user id, email or salted global digest.

### 5.7 Completeness

Omission MUST be visible. `export_status.withheld_counts` states how many records were left out. `complete` with non-zero counts, or a finding referencing an absent passage in a non-partial packet, is `completeness-fabricated`. `withheld` with non-zero records is `silent-omission`.

## 6. Version and evolution policy

- Version string `source-evidence-packet/MAJOR.MINOR.PATCH`, currently `0.1.0`.
- 0.x additive rule: within a minor line (`0.1.x`) changes are additive only: new OPTIONAL fields, new documentation, new fixtures, new validator warnings. Required fields, enum values already published, hash and canonicalization rules do not change.
- Anything breaking (removing/renaming a field, narrowing a type, changing an enum, tightening a rule that rejects previously valid packets, changing canonical encoding) is a **new minor** (`0.2.0`) with its own directory `contracts/source-evidence-packet/v0.2.0/`. Old directories remain frozen.
- Importers MUST reject any `schema_version` they do not implement (`unsupported-schema-version`). They MUST NOT attempt best-effort parsing, ignore unknown fields (schemas are closed), or silently upgrade.
- Producers MUST NOT emit enum values or fields from a later version under this version string.

## 7. Validator error codes

All codes are implemented and have a negative fixture in `fixtures/invalid/<code>/` except where noted. Sorted output order is `(code, file, id)`.

| Code | Condition |
|---|---|
| `invalid-json` | A file is not parseable JSON/JSONL |
| `schema-invalid:<file>` | JSON Schema failure in `manifest`, `papers`, `findings`, `passages` or `assessments` |
| `unsupported-schema-version` | `schema_version` is not `source-evidence-packet/0.1.0` |
| `artifact-missing` | A declared artifact file is absent |
| `unexpected-file` | A file in the directory is not declared |
| `artifact-hash-mismatch` | File sha256 differs from the manifest |
| `artifact-length-mismatch` | File byte length differs from the manifest |
| `record-count-mismatch` | Line count differs from `record_count` |
| `non-canonical-encoding` | Not sorted-key compact LF JSON with a final newline |
| `duplicate-id` | A paper/passage/assessment id, or `(finding_id, revision)`, repeats |
| `dangling-paper-ref` | `paper_id` has no paper |
| `dangling-passage-ref` | `passage_ids`/`passage_id` has no passage |
| `dangling-finding-ref` | Assessment `finding_id` has no finding |
| `dangling-revision-ref` | Assessment `finding_revision` absent for that finding |
| `passage-paper-mismatch` | A finding's passage belongs to a different paper |
| `snapshot-ref-mismatch` | Passage `snapshot_ref` differs from the paper's |
| `assessed-snapshot-mismatch` | `assessed_snapshot_sha256` differs from the paper snapshot sha256 |
| `revision-gap` | Revisions of a finding are not 1..n |
| `approved-without-review` | `approved-for-export` while `unreviewed` |
| `review-without-assessment` | `human-reviewed` with no assessment for the revision |
| `verification-overclaim` | `verification_level` not supported per 5.3 |
| `quote-presence-as-support` | `source-verified`+ resting only on `not_assessed`/`unknown`/`restricted` |
| `match-offset-inconsistent` | Offsets null/non-null contrary to the match status |
| `unmatched-passage-approved` | Approved finding with no `exact`/`normalized` passage |
| `ambiguous-passage-approved` | Approved finding supported only by `ambiguous`/`not_found`/other non-matching passages |
| `quote-hash-mismatch` | sha256(quote) differs from `quote_sha256` |
| `withheld-quote-inconsistent` | `withheld-rights` without `"withheld"`, or `included` with `"withheld"` |
| `restricted-quote-leak` | Quote included for a paper not `allowed` |
| `full-text-flag` | A full-text flag is true (schema const catches it; kept for the semantic path, no separate negative fixture) |
| `approved-in-restricted-packet` | Approved finding on a `restricted`/`unknown` paper |
| `private-packet-not-exportable` | `private`/`embargoed` level in `--for-import` mode (warning with `--allow-restricted`) |
| `embargo-expired-or-missing` | `embargoed` without usable `embargo_until` |
| `completeness-fabricated` | `complete` while counts >0, or absent/withheld passage in a non-partial packet |
| `silent-omission` | `withheld` state with non-zero artifact records |

## 8. Consumer requirements and mapping

### 8.1 Rejected and omitted counts

A consumer MUST NOT silently drop records. On import it MUST report, per run:

- `rejected`: records refused, by reason, for **incompatible** (verification below the importer's threshold, `unreviewed`, `proposed`) and **restricted** (withheld quote, private/embargoed rights) classes;
- `omitted`: records the producer declared withheld (`export_status.withheld_counts`), reported separately from rejections.

A consumer MUST NOT render a partial packet as complete, nor a withheld quote as present. `sep_validate.py --mapping-report` emits these counts per record. The validator output `counts` block carries `findings_by_export_state`, `findings_by_verification`, `omitted` and `rejected`.

### 8.2 Mapping to Diggypaper source contracts

| Packet element | Diggypaper contract | Mapping and loss |
|---|---|---|
| `finding.producer_ids.answer_ref` | `EvidenceEnvelopeV1` `AnswerRecord.answer_ref` | Preserved verbatim; packet `finding_id` is separate, not the answer ref. The answer `value` is not exported; `statement` is a curated atomic wording. |
| `finding.producer_ids.evidence_refs`, `passage.producer_ids.evidence_ref` | `EvidenceRecord.evidence_ref` | Preserved. `local_ref`, `origin`, `source_binding_ref` are not exported. |
| Answer-evidence edge (`finding.passage_ids`) | `EvidenceLink` (`answer_ref`, `evidence_ref`) | Edge becomes membership of `passage_ids`. Many-to-many is kept via repeated passage ids. Link carries no support (matches evidence-links-v1). |
| `passage.match` | `QuoteMatchReceipt` | `status` vocabulary identical; `receipt_ref` preserved; candidate offsets and resource-limit metadata are not copied. Only `exact`/`normalized` keep offsets. |
| `paper.source_snapshot`, `passage.source` | `SourceSnapshot` (SEIP provenance v0.1) | `snapshot_ref` and the snapshot sha256 are preserved; raw text/views are never exported. Offsets refer to a view the consumer cannot read from the packet. |
| `finding.producer_ids.prediction_ref` | `Invocation` / prediction | Reference only. Prompts, requests, model identifiers, parameters are not exported. A consumer needing them must resolve against Diggypaper. |
| `assessment` | `EvidenceSupportAssessment` `seip-support-v1` | Judgment, rationale, time preserved; `reviewer_ref` is the project-scoped pseudonym; `support_assessment_ref` preserved. `unknown`/`restricted` are packet-level additions with no v1 equivalent: a consumer importing into seip-support-v1 MUST map them to `not_assessed` and keep the original in lineage. |

### 8.3 Crosswalk to ComputationalReviewTemplate (Phase 2 `finding`)

**All target field names below are PROVISIONAL and unverified.** The CRT repository was not available when this was written. Open item: pin the Phase 2 `finding` schema in issue #47 and update this table; until then a CRT importer MUST NOT rely on these names.

| Packet field | Provisional CRT `finding` field | Dropped / never invented |
|---|---|---|
| `finding_id` + `revision` | `finding.id` / `finding.version` (PROVISIONAL) | Revision is kept as a reference; CRT must not collapse revisions. |
| `statement` | `finding.statement` (PROVISIONAL) | `origin` should map to a provenance/author-kind field; if CRT has none it is dropped and the finding MUST NOT be attributed as review-authored. |
| `paper_id`, `paper.identifiers`, `title` | `finding.source` / citation (PROVISIONAL) | `authors` are citation metadata only, never claim author. |
| `context.*` | `finding.context` / PICO-like fields (PROVISIONAL) | Values kept including `"unknown"`; nothing is filled in. |
| `numeric_results[]` | `finding.result` (PROVISIONAL) | Only recorded `value`, `unit`, `n`+`definition`. **Effect size**: absent from the packet, so `not-assessed` on the CRT side. Never computed or imputed. |
| `n` | `finding.sample_size` (PROVISIONAL) | `definition` is carried or the N is dropped; an article-level N is never relabeled as an experimental-unit N. |
| `passage_ids`, `quote`, `match` | `finding.evidence` / quotes (PROVISIONAL) | Withheld quotes arrive as hash only. Offsets and receipts are dropped unless CRT supports them. |
| `assessments[]`, `verification_level` | `finding.support_assessment` (PROVISIONAL) | Reviewer pseudonyms only. **Source validation** beyond the packet's `verification_level` is `not-assessed`. |
| (not in packet) independent replication | `finding.replication` (PROVISIONAL) | Always `not-assessed`/absent. Never inferred from `independently-verified`, which counts reviewers, not replications. |
| (not in packet) TRUST | none | Out of scope; owned by ORAtlas. |

## 9. Example lineage

```
prediction_ref  pred-0001                        (Diggypaper prediction)
  answer_ref    8a1f...  -> finding:synth-a-f1 rev 1   (AnswerRecord)
  evidence_ref  c4d2...  -> passage:synth-a-p1          (EvidenceRecord)
  receipt_ref   qm-0007  match.status = exact           (QuoteMatchReceipt)
  snapshot_ref  snap-A   sha256:<S>                     (SourceSnapshot)  <- paper:synth-a
  assessment:synth-a-a1  supports, reviewer rv-<16+ hex>, assessed_snapshot_sha256 = sha256:<S>
                         (support_assessment_ref = sa-0003, seip-support-v1)
finding:synth-a-f2 rev 1: same paper, different outcome/time, proposed, unreviewed,
                          ambiguous passage only. Never merged with f1 by text.
paper:synth-b: metadata-only rights, quote withheld (hash only), snapshot not captured.
```

Full synthetic data: `fixtures/valid/canonical/`. Fixtures use fake `10.0000/` DOIs and "Synthetic" authors; no real paper text.

## 10. How to validate

```
python docs/contracts/source-evidence-packet/v0.1.0/validator/sep_validate.py <dir>
python docs/contracts/source-evidence-packet/v0.1.0/validator/sep_validate.py --allow-restricted <dir>
python docs/contracts/source-evidence-packet/v0.1.0/validator/sep_validate.py --mapping-report <dir>
```

Default is `--for-import`. Offline, deterministic, no clock or randomness. Prints JSON `{"valid", "errors":[{code,file,id,message}], "warnings", "counts"}` sorted by `(code, file, id)`. Exit 0 valid, 1 invalid, 2 usage. Tests: `backend/tests/test_source_evidence_packet.py`. Regenerate fixtures with `python docs/contracts/source-evidence-packet/v0.1.0/fixtures/build_fixtures.py` (idempotent).

## 11. Open items

1. Pin ComputationalReviewTemplate Phase 2 `finding` field names in issue #47 (section 8.3 is PROVISIONAL).
2. Decide the importer's minimum `verification_level` threshold for CRT (this contract leaves it to the consumer, section 8.1).
3. Confirm whether `unknown`/`restricted` assessment statuses are accepted upstream or remain packet-only.
