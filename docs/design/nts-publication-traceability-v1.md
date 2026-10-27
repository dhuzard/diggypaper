# NTS publication traceability (optional research module, issue #11)

Status: research PoC, integrated in the platform since 2026-10-19 (migrations `f0031`–`f0033`,
see below). Optional and offline-first; nothing in the existing Article x Search x
AnswerRecord, screening, gold, RO-Crate or Finding Curation behaviour changes. Code:
`backend/app/nts/`; CLI: `python -m app.nts run --run-dir runs/<study>`; first study:
`runs/nts-publication-traceability-poc-v1/`.

## What it does

1. **Frozen source.** A source file holds contiguous verbatim spans of an open-access article plus
   how they were obtained and the licence evidence. `freeze()` builds a real `SourceSnapshotV1`
   (deterministic ids) and every quotation is checked with the real `ArticleQuoteMatcher`, giving
   an `exact`/`normalized`/`ambiguous`/`not_found` receipt. A quote that is ambiguous is grown by
   whole sentences until unique, otherwise it stays `ambiguous` (null offsets).
2. **Deterministic extraction** (no model call). `identifiers.py` reads ALURES NTS ids
   (`NTS-<ISO2>-<digits>`, cited version/date) and, as *separate kinds*, APAFIS, UK project
   licences, Spanish regional numbers and Romanian authority numbers. `counts.py` reads written
   animal numbers into typed observations (species, strain, sex, cohort, unit counted,
   count category, scope, derived flag, overlap flags). Categories:
   `NTS_planned_or_authorized`, `RAR_actual_used`, `article_reported`, `breeding`, `control`,
   `experimental`, `unknown`. A figure computed from written numbers (groups x animals) is
   `derived` with its formula. Missing numbers stay `not reported`.
3. **Proposals, not findings of record.** Each passage becomes a finding *proposal* validated by
   `FindingContent` (several per article). Proposals carry no reviewer, no support judgment and
   no approval. The proposer is `automation:nts-deterministic-extractor-v1`.
4. **Exact-ID ALURES join** (`alures.py`). Schema is discovered, not assumed. States:
   `confirmed-exact`, `...-amended` (ALURES holds a newer version than the cited one),
   `...-cited-version-absent`, `ambiguous`, `duplicate-rows`, `candidate` (near match, never
   accepted), `unmatched`, `alures-unavailable`. Planned numbers are authorization ceilings.
5. **Comparison without a ratio** (`compare.py`). No percentage is computed: unique-animal
   cohort matching is not established by any text. Only conservative flags are emitted (shared
   NTS, species absent from the NTS, article total above an authorized ceiling).
6. **Packets.** `build_packet` is called *unchanged* with proposals and no reviews, so it
   withholds everything (`not-approved`). A second, clearly different exploratory packet
   (`proposed`/`unreviewed`/`unverified`, no assessments, `creation_method: manual`, restricted)
   lets the extraction be inspected and shows a consumer's rejection of unreviewed work.
   `nts-companion.json` (schema `nts-traceability-companion/0.1.0`) holds the NTS-specific typed
   data the frozen schema cannot carry and is bound to the packets by manifest digest.
7. **Review queue.** `review-queue.{json,md}`: passage, evidence, suggested extraction,
   uncertainties and an *automation-labelled* proposed judgment. State `HUMAN_REVIEW_PENDING`.
8. **CRT.** The real Phase 2 importer is run on both packets; results are recorded, never patched.

## Invariants kept

- Version and concept DOI untouched; NTS version cited vs ALURES current version distinct.
- Exact-ID joins only; fuzzy matches are candidates. National numbers never equal NTS ids.
- Rights: quotes included only for an allowlisted licence with an https evidence URL
  (`app.rights`); otherwise `withheld-rights` with a real `quote_sha256`. The exploratory
  determination is read from PMC licence metadata and labelled as *not* a LEAD declaration.
- No LLM, no invented reviewer, no `human-reviewed` flag, no weakening of export requirements.

## Known limits

- Source text of the PoC is a connector rendering excerpt (no italics/some symbols), not a
  byte-identical publisher copy; `python -m app.nts fetch` swaps in PMC OA JATS when reachable.
- Finding Curation's API links findings to `ArticlePrediction` evidence graphs. NTS proposals are
  deterministic, not predictions, so they are fed to the pure producer directly instead of the
  DB-backed endpoint. Creating them through the API (and the human review) is the next step.
- ALURES access was blocked in the first run (see the run's `research-report.md`).

## Platform integration (database, API, UI) — 2026-10-19

The file-based PoC above stays as the offline, reproducible study. The same extractor is now
also a first-class producer inside the platform, without any change to the frozen Finding
Curation tables or to the `source-evidence-packet/0.1.0` contract:

1. **Deterministic inference backend.** `model_registry.backend_type = DETERMINISTIC`
   (migration `f0031` seeds `nts-deterministic-extractor-v1`). An ordinary inference job runs
   `app/nts/backend.py` in process: the platform captures the `SourceSnapshot`, prepares the
   `Invocation` from the rendered messages, records the program's JSON output verbatim
   (sha256) as the response, checks every quote with the real matcher and builds the
   structured evidence envelope. The reconciliation names the transport
   (`deterministic-program`), so nothing pretends an HTTP call happened. No confidence is
   reported: every NTS prediction is `NEEDS_HUMAN_REVIEW`.
2. **NTS project template.** `POST /projects/{id}/nts/setup` (LEAD) installs the question and
   the three text fields (`nts_identifier`, `national_authorization`, `animal_counts`), the
   `methods`/`results` sections and the model. A project that already holds predictions for
   another question is refused (409). The typed extraction (proposals, mentions, count
   observations, receipts) is stored under `decision_json.nts_extraction`, never as a field.
3. **Proposals as findings.** `POST /projects/{id}/nts/propose` creates one finding per
   extractor proposal: revision 1, `proposer_ref = sha256("automation:<identifier>")`,
   `proposer_id = NULL`, evidence = the prediction's own (answer, evidence) link. Idempotent by
   `uuid5(prediction_id, proposal.finding_id)`. Review, support judgments, rights and the
   packet export are the existing findings endpoints; any member other than the automation
   proposer can review, two distinct reviewers give `independently-verified`.
4. **ALURES with receipts.** `POST …/nts/alures/sync` (Celery, `import_jobs.kind =
   nts-alures`) downloads the official exports into `settings.nts_data_dir/<project>/alures`
   and records `nts_datasets` rows (sha256, bytes, rows, url, time). `offline=true` re-receipts
   files placed there by hand.
5. **Join and observations.** `POST …/nts/join` (Celery, `nts-join`) recomputes `nts_links`
   and `nts_count_observations` per latest NTS prediction (delete + insert, never patched):
   exact-ID join states as in the PoC, `VERIFIED_PAIR` only for an exact article receipt and a
   confirmed ALURES row, national numbers always `NO_NTS_CROSSWALK`, planned figures read
   from `alures_nts_projects.total_number_of_animals` (a project-wide ceiling, species not
   split; fate/purpose side tables are never used as authorization figures).
6. **Adjudication.** `POST …/nts/links/{id}/adjudications` appends a human decision
   (`confirmed` / `rejected` / `unresolved`, rationale, pseudonymous adjudicator) on a
   publication-NTS link; a Postgres trigger makes the table append-only. A re-join produces new
   derived rows, so adjudications are read together with the link they were made on.
7. **Read models.** `GET …/nts` (status), `/links`, `/observations`, `/summary` (one row per
   article: confirmed ids, planned ceiling, reported numbers, comparability note and flags, no
   ratio), `/review-queue` (automation proposals not yet reviewed, with quote, match status and
   the ids needed to open the review), `/alures/datasets`.

## Extractor v2: short statements — 2026-10-22

`nts-deterministic-extractor-v1` stated every finding as its quoted passage, so a reviewer read a
whole Methods paragraph as the "claim" of an animal-count finding. `-v2`
(`diggypaper.nts.extract/2.0.0`, migration `f0033`) words the claim in one machine sentence with
`origin: ai-paraphrase`, for example "Animal numbers reported (rat (Wistar)): 2 female per group;
2 male per group." or "The article cites the ALURES non-technical summary NTS-ES-414463, written
"nts-es-414463"…". Quotes, windows, receipts, answers and typed values are byte-identical to v1.

- Both versions stay registered and runnable (`backend.EXTRACTORS`); a registry row whose
  identifier does not match its name is refused, so a run is always attributable to the code that
  produced it. The file-based PoC keeps v1 (`propose(..., statements="verbatim")`).
- Each version has its own automation proposer reference; findings from v1 keep theirs.
- Re-extracting an article (any version) supersedes its untouched automation proposals in the
  review queue and in `proposed_findings`; they stay in the history. A proposal a member has
  edited is never dropped.

Tests: `tests/test_nts_backend.py` (backend, provenance) and `tests/test_nts_api.py` (end to end
on a real extractor run with a synthetic ALURES export).
