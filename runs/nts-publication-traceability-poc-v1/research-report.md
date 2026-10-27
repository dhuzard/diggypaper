# nts-publication-traceability-poc-v1 - research report

> **Where the full outputs are.** This directory keeps the human-readable record of the run
> (this report, `case-summary.md`, `discovery-log.md`, `review-queue.md`,
> `crt-interoperability-report.md`), its inputs (`study-config.json`, `sources/`,
> `gate0-acquisition.json`) and its manifest and logs (`run-manifest.json`, `logs/`). The machine
> outputs the pipeline writes (`publication-nts-links.csv/.json`, `animal-count-observations.csv`,
> `finding-extraction-report.json`, `comparisons.json`, `review-queue.json`,
> `packet-validation-report.json`, `crt-import-report.json`, `crt-interoperability-report.json`,
> `nts-companion.json`, `source-evidence-packet/`, `source-evidence-packet-exploratory/`) are no
> longer committed: the last commit that contains them is `3e13113` (`git show 3e13113:runs/nts-publication-traceability-poc-v1/<path>`),
> and `run-manifest.json` still records their SHA-256. Regenerate them with
> `cd runs/nts-publication-traceability-poc-v1 && OFFLINE=1 ./reproduce.sh` (or
> `python -m app.nts run --run-dir runs/nts-publication-traceability-poc-v1 --offline` from `backend/`);
> the private `cache/pmc11731260.json` (case A) and the ALURES CSVs are not in git, so a re-run
> without them reports case A as skipped. This note was added after the run; the hash of this
> file in `run-manifest.json` refers to the version at `3e13113`.

Diggypaper #11 (publication -> ALURES NTS traceability), built on Finding Curation (#46) and the frozen
`source-evidence-packet/0.1.0` (#47). Executed 2026-10-18 on code commit
`61a4829e66fc45ccfc46951a03ffd74af8c546ef`; CRT checkout `ce8f29cf251aadfc4c44d109db65d15dd0f629c0`.

## 1. Outcome in one paragraph

**Gate 0 verdict: `BLOCKED_NO_VERIFIED_PAIR`.** Both article-reported identifiers (`NTS-ES-414463`,
`NTS-RO-406074 v.1`) were located in the retrieved methods text with exact quote-match receipts, but
**no official ALURES dataset could be downloaded**: the session's network policy denies
`environmental-data.ec.europa.eu` (and every other official, PMC, Europe PMC, publisher, DOI and preprint
host). No ALURES row was created, inferred or substituted. Consequently **no publication-NTS pair is
confirmed against both real sources, the positive-match experiment stops here, and the task's definition of
done is NOT met.** Everything that does not need ALURES was built, tested and executed: deterministic
extraction, typed animal counts, frozen snapshots, quote receipts, rights handling, the real Diggypaper
producer and validator, the real CRT importer, tests, a review queue, a companion artifact and a reproducible run.
The study should be re-run with ALURES reachable (`./reproduce.sh`; no code change needed).

## 2. Gates

| Gate | State | Evidence |
|---|---|---|
| Gate 0a: publication-side ID in the article's methods/ethics text | **PASSED** (both seeds) | `publication-nts-links.csv`: `nts-es-414463` (A, view 412-653) and `NTS-RO-406074` v1, "8 October 2025" (B, view 1187-1258); receipts `exact`, quote sha256 recorded |
| Gate 0b: ID found in the official ALURES export | **BLOCKED** (network policy) | `gate0-acquisition.json`: `Tunnel connection failed: 403 Forbidden`; `discovery-log.md` |
| Gate 0c: at least one pair confirmed on both sources | **NOT MET** | verdict `BLOCKED_NO_VERIFIED_PAIR` |
| Extraction / linkage / counts | executed | `finding-extraction-report.json`, `animal-count-observations.csv` |
| Real producer packet produced and validated | **PASSED** | `source-evidence-packet/` (state `withheld`, 0 findings, 13 omitted `not-approved`), validator valid in `for-import` and `allow-restricted`, digest `sha256:9e97100feae5a44fc58ae1923d095a8289b864a6b2a122bb4d98f7851c0021ab` |
| Exploratory (unreviewed) packet validated | **PASSED** | `source-evidence-packet-exploratory/`, 13 `proposed/unreviewed/unverified` findings, 0 assessments, digest `sha256:c51f114f67c60b28a7c72fd48d0fb34c313c03d57ad9790e00f5d6e4dfd68009` |
| CRT validate-only / import | **PASSED, 0 findings accepted** | `crt-import-report.json`; CRT's `manifest_sha256` equals Diggypaper's `packet_digest` for both packets |
| CRT acceptance of any finding | **BLOCKED: `HUMAN_REVIEW_PENDING`** | no independent human review exists; CRT and Diggypaper both require one; nothing was weakened |
| PostgreSQL migration 0029 (and 0030) | **PASSED** | `logs/postgres-0029-0030.log`: 12 passed on a disposable PG 16.15 |
| Backend suite | **PASSED** | `logs/backend-full-after-nts.log`: 1083 passed, 4 skipped (numpy/onnxruntime absent). Baseline before the module: 1017 passed, 5 skipped |

## 3. Study selection

Seeds as given by the task; one control found by PubMed search; see `discovery-log.md`.

| Case | Article | Role | Licence (as verified) |
|---|---|---|---|
| A-ES-mouse | 10.1016/j.isci.2024.111518 (PMC11731260) | positive seed | **not verified** (`license.type=null`): quotes withheld, source excerpt kept in the git-ignored cache |
| B-RO-rat | 10.3390/ph19060846 (PMC13304911) | positive seed | CC BY 4.0 per PMC metadata: quotes included, excerpt committed with attribution |
| C-FR-negative-control | 10.1371/journal.pntd.0006997 (PMC6392334) | negative control | CC BY 4.0 per PMC metadata |

No additional positive candidates were searched for: the protocol expands the corpus only if Gate 0 confirms.
The preprint of B could not be located (no reachable host), so it is reported as `preprint not-located`
and is **not** counted as a second output; the preprint/version-of-record deduplication rule is implemented
and tested (`app/nts/dedup.py`) but could not be exercised on real data.

## 4. Method

1. **Source.** The only reachable route to article text was the PubMed connector. The executing agent
   transcribed contiguous verbatim paragraphs (Animals, per-group counts, two dose-selection result
   sentences) into `sources/*.json` / `cache/*.json`. This is *not* a byte-identical PMC or publisher copy
   (the connector drops italics and symbols such as the statistic `n`; figure legends and tables were not
   returned). `python -m app.nts fetch` replaces the excerpt with PMC OA JATS text when NCBI is reachable.
2. **Snapshot and receipts.** Real `SourceSnapshotV1` (deterministic ids) and the real `ArticleQuoteMatcher`
   (`article-input-quote-v1.2`, normalized matching allowed). All 13 proposals have an `exact` receipt.
3. **Extraction.** Deterministic rules only; **no LLM or API call was made** (`run-manifest.json: llm.used=false`).
4. **Join.** Exact normalized NTS id against discovered ALURES columns (none available in this run).
5. **Packets.** The unchanged production `build_packet` was fed the proposals with no reviews. A separate,
   clearly labelled exploratory writer produced a restricted packet of unreviewed proposals.
6. **Rights.** Quotes are included only for an allowlisted licence with an https evidence URL; the
   determination here is read from PMC licence metadata and is labelled "not a LEAD declaration".

## 5. Results

### 5.1 Compact table per case (`case-summary.md`)

| DOI | NTS ID / version | Species (strain) | Planned animals (NTS) | Reported experimental animals | Count comparability | Match status |
|---|---|---|---|---|---|---|
| 10.1016/j.isci.2024.111518 | NTS-ES-414463, version not cited | mouse (db/db for the NTS-covered work; C57BL/6 for the UK knockout line) | not reported (ALURES unavailable) | not reported in the retrieved text | not comparable: no confirmed ALURES record | `ARTICLE_CONFIRMED_ALURES_UNAVAILABLE` |
| 10.3390/ph19060846 | NTS-RO-406074 v1 (8 Oct 2025) | rat (Wistar) | not reported (ALURES unavailable) | article total 146 (stated); stated/derived cohorts below | not comparable: no confirmed ALURES record; cohorts may overlap | `ARTICLE_CONFIRMED_ALURES_UNAVAILABLE` |
| 10.1371/journal.pntd.0006997 | none reported (APAFIS only) | mouse (OF1), blood-meal hosts for mosquitoes | not applicable | not reported | not applicable | `NO_NTS_ID_REPORTED` |

### 5.2 Case A: Spanish NTS vs UK licence in one article

- The identifier is written `ALURES #nts-es-414463` (lower case, no version). Verbatim form is kept;
  the normalized form `NTS-ES-414463` is what would be joined.
- The same Animals paragraph attributes **Db/db mouse procedures** to the Andalusian authority
  (regional number `#3/11/2021/171/A`, stored as `national-authorization:es-regional`, not an NTS id) and
  **Pax6os1 null mouse experiments** to a UK Project Licence (`PPL PA03F7F07`, jurisdiction GB, no NTS crosswalk).
  The link row therefore records `applies_to: Db/db mice` and `excluded_from_this_nts: [Pax6os1 null mice]`.
  The sentence "All experiments were performed in male and female adult mice (6-12 weeks old)" spans both
  authorizations and is deliberately not attributed to either.
- No animal numbers were found in the retrieved text (figure legends were not returned), so the count
  record is `not reported`; this means "not found in what was retrieved", not "absent from the article".

### 5.3 Case B: Romanian NTS and the national numbers

- `NTS-RO-406074 v.1, 8 October 2025`. Three Romanian numbers are stored separately and never joined:
  ethics approval `435/13 March 2024`, project authorization `406/29 April 2024`, facility authorization
  `926/6 June 2021`. The project authorization shares its leading digits with the NTS id: a purely
  numeric coincidence that the module keeps apart on purpose.
- Animal counts (all from Materials and Methods unless noted):

| Cohort (article section) | Reported | Derived | Notes |
|---|---|---|---|
| Whole study (4.1) | **146** | - | counted noun absent from the extracted sentence; species from context |
| Xylazine dose-finding (4.2.1) | 20 rats; 2 M + 2 F per dose, five doses | 5 x 4 = 20 | stated and derived agree |
| FT+XZ dose-finding (4.2.2) | 2 M + 2 F per dose, three doses | 3 x 4 = 12 | |
| ... 0.104 mg/kg group extension | +6 M +6 F | | extends the same group (one cohort, not two) |
| ... 0.130 mg/kg group extension | +1 M +1 F | | |
| Main study treatment groups (4.2.4) | 5 M + 5 F per group, six groups | 6 x 10 = 60 | |
| Positive + negative control (4.2.4) | 7 M + 7 F each | 2 x 14 = 28 | category `control` |
| Results text (2.1.1/2.1.2) | "two females + two males per group; total n = 4" | | restatement of dose-finding cohorts; consistent (2+2=4); mapping to methods cohorts not stated |

- **Reconciliation:** stated cohorts sum to 134; the article total is 146; **12 animals are not attributed to
  any stated cohort.** (The "FT alone" dose-selection cohort of 3 x 4 = 12 would match, but the methods do not state
  it, so this is a hypothesis for a reviewer, not a result.) The 0.104 mg/kg group is described as the dose
  "selected for the main study"; whether those animals are also in the main-study groups is not stated, so
  overlap is flagged and nothing is summed beyond the arithmetic above.
- **No percentage of animals "published" was computed**, and none can be: unique-animal matching is not
  established, and the NTS figure (not available here) is an authorization ceiling for the whole project.

### 5.4 Case C: negative control

A French `APAFIS#6573-201606l412077987 v2` (verbatim, including the odd lowercase `l`) and no NTS id. Result:
`NO_NTS_ID_REPORTED`, `NO_NTS_CROSSWALK` for the APAFIS row. The mice are blood-meal hosts; the study unit is
the mosquito. Case A's UK Project Licence and the Romanian national numbers serve as further
ambiguous-authorization controls inside the seeds.

### 5.5 Findings and linkage

13 finding proposals (A 3, B 9, C 1), 22 article count observations (B), 9 link rows. Linkage success:
article side 2/2 NTS ids found with exact receipts; ALURES side 0/2 evaluable. Missingness: counts absent
for A and C; planned (`NTS_planned_or_authorized`) and actual-use (`RAR_actual_used`) counts `not reported`
for both seeds; no RAR was obtained (none resolvable in this environment).
`finding-extraction-report.json`: imported as proposals 13, rejected 0, incomplete 0 (all exact),
withheld by the production exporter 13, quotes withheld for rights 3 (case A).

## 6. Human review and rights

- Review queue: `review-queue.md/json` (13 items; article text, evidence, suggested extraction,
  uncertainties, automation-labelled proposed judgment). Every item is `HUMAN_REVIEW_PENDING`. Proposed
  judgments are labelled "AUTOMATION PROPOSAL" and appear in no packet; no reviewer identity was created.
  Quotes are shown in the committed queue only for the CC BY articles.
- The production exporter withheld all 13 because they have no independent human approval. That is the
  requirement working, not a defect.
- Rights tests cover unknown, non-commercial (not allowlisted) and permitted material
  (`tests/test_nts_pipeline.py::test_rights_unknown_restricted_and_permitted`).

## 7. CRT interoperability (`crt-interoperability-report.md`)

No schema, validator or digest incompatibility was found: Diggypaper's validator and CRT's `--validate-only`
agree for both packets and the normative packet-digest rule gives the same value in both repositories.
CRT rejects/withholds all 13 exploratory findings with explicit reasons (`unreviewed`, `not-approved-for-export`,
`no-supporting-judgment`, `verification-below-threshold`, `packet-scope-restricted`, and `paper-rights-not-redistributable`
for A) and reports the production packet's 13 as `omitted_by_producer`. The real gap is semantic: Phase 2 has no
field for NTS ids or animal-count observations, and `n` is imported only from an experimental-unit N, so the
typed NTS data live only in `nts-companion.json`. CRT and ORAtlas were not modified.

## 8. Tests

`backend/tests/test_nts_*.py`: 65 tests (64 pass without a CRT checkout, 65 with one). Coverage: exact
match, missing id, near-match candidate, version amendment, cited version absent, ambiguous and duplicate
rows, ambiguous national numbers, same-project multiple publications, mixed authorization, overlap, missing N,
corrupted receipts, restricted/unknown/permitted rights, unreviewed findings, duplicate (idempotent) import, preprint
deduplication, tampered/unsupported/private/overclaiming packets, and the real CRT importer. **All ALURES
fixtures are synthetic and labelled as such.**

## 9. Errors, deviations and limitations

- ALURES, NCBI/PMC, Europe PMC, DOI/Crossref, MDPI, bioRxiv and Zenodo are denied by the sandbox policy
  (`CONNECT 403`); `WebFetch` reports `ENOTFOUND`. This is the single blocker for the verified pair.
- First attempt at the PostgreSQL test used a unix-socket URL; the existing Alembic test helper cannot take
  a percent-encoded URL (`ValueError: invalid interpolation syntax`, configparser). Not changed; re-run over
  TCP, 12 passed.
- Tests found and fixed two defects in my own code: the ALURES state vocabulary passed to the comparison did not
  match its `confirmed*` check; the total reconciliation filtered methods sections by title prefix instead of kind.
- A self-audit of the committed outputs found that, for the licence-unverified article A, the exploratory finding `statement` repeated the verbatim sentence although the passage quote was withheld. Fixed before the final run (machine-worded, hash-referenced statement, `origin: ai-paraphrase`) with a regression test; the final outputs contain no verbatim text of article A.
- Article text is an agent-transcribed connector excerpt, not a verified full-text snapshot; title restored
  for case C from the DOI record because the connector dropped the taxon name. Article A's licence is unverified.
- The finding export uses the pure producer fed directly, not the DB-backed API, because Finding Curation links
  findings to `ArticlePrediction` evidence graphs and NTS proposals are not predictions.
- The Aedes control is about mosquitoes; mouse numbers were not reported there, which is the point of the control.

## 10. Next steps

1. Re-run with network access to the EC ALURES host: `FETCH=1 ./reproduce.sh` (adds PMC JATS full text and the
   live CSV download); expect Gate 0 to move to `PASS_AT_LEAST_ONE_VERIFIED_PAIR` or to a documented
   `...ID_ABSENT_IN_ALURES` / `...amended` state per case. Record the CSV SHA-256 in `run-manifest.json`.
2. Have an independent member create the 13 proposals in Finding Curation (needs a prediction evidence link), review
   them with `seip-support-v1`, and declare redistribution rights as LEAD for case A once its licence is verified.
3. Locate the preprint of B and exercise the deduplication on real data.

## 11. Reproduction

```bash
cd runs/nts-publication-traceability-poc-v1
CRT_CHECKOUT=/path/to/ComputationalReviewTemplate_trust-knowledge ./reproduce.sh        # whole run + validators + tests
FETCH=1 ./reproduce.sh                                                                   # with network: full text + live ALURES
cd ../../backend && SEIP_TEST_POSTGRES_URL=postgresql+asyncpg://postgres@127.0.0.1:54329/postgres \
  pytest tests/test_finding_curation_postgres.py -q -rs                                  # migration 0029/0030 on a throwaway server
```

Source attribution: B text excerpts (CC BY 4.0) from Pharmaceuticals 2026, 19(6), 846 (doi 10.3390/ph19060846);
C excerpts (CC BY 4.0) from PLoS Negl Trop Dis, doi 10.1371/journal.pntd.0006997. Article text obtained via PubMed
(PMC) as provided by the PubMed connector.
