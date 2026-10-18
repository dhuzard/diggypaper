# NTS publication traceability (optional research module, issue #11)

Status: research PoC. Optional and offline-first; nothing in the existing Article x Search x
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
