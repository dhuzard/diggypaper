# SEIP proof-of-concept test plan

Prepared 2026-10-17 after the [merge audit](../audits/2026-10-17-seip-merge-audit.md). Governs the first end-to-end exercise of the merged SEIP features on real papers with the provisional pilot staff named in the [qualification protocol](../protocols/seip-qualification-v0.1.md) (two reviewers, one adjudicator). It is a plan, not a result. Finding ids (F-n) refer to the merge audit.

## Goal and non-goals

Goal: show, on a frozen 30-paper set, that a prediction's quoted evidence resolves to a retained source version and passage with an explicit match outcome, that a human can record scientific support on exact answer/evidence links, that the record survives offline export and replay, and that the pilot scorer produces protocol metrics with explicit denominators.

Non-goals: a population accuracy claim, a model comparison, or calling the result scientifically validated. If any gate in §4 fails, the stage is reported as exploratory.

## Phases

### Phase 0, engineering readiness (before any paper is loaded)

Blocking fixes: F-0 (Windows import), F-1 (blind list leak), F-3, F-4, F-5. Then run, on the deployment that will host the pilot:

1. `alembic upgrade head` on a PostgreSQL copy of a populated pre-SEIP database (`0021`), then `pytest tests/test_provenance_migration.py tests/test_review_binding_postgres.py` with `SEIP_TEST_POSTGRES_URL` set. Record the output in the pilot folder (F-10 until CI covers it).
2. Full backend suite, frontend `tsc`, `lint`, `jest`, `build`.
3. Offline validators on the checked-in examples:

```bash
cd backend
python -m app.seip_pilot_manifest ../docs/examples/seip-pilot-manifest-draft-v1.json
python -m app.seip_reference_ledger ../docs/examples/seip-pilot-manifest-draft-v1.json ../docs/examples/seip-independent-reference-draft-v1.json
python -m app.portable_replay ../docs/examples/seip-portable-replay-v1.json
python -m app.evidence_crate <path to an exported crate>/evidence
```

4. Adversarial checks that must fail: `tests/fixtures/portable_replay_adversarial_v1.json` cases, `quote_matcher_v1.json` negative cases (invented quote, example-only text, case change, PDF hyphen), and a manual crate with one edited hash.

Exit: everything above green, results archived with commit hash.

### Phase 1, software smoke on development papers (6 papers, no blinding needed)

Use the six development papers from the manifest. One project, one search, one fixed chat model and prompt, decoding settings frozen.

| Step | Action | Expected evidence |
|---|---|---|
| 1.1 | Import 2 JATS, 2 PDF-upload, 2 abstract-only papers | `fulltext_source` set per source; PDF→JATS replacement leaves no stale PDF (also confirms F-12 fix) |
| 1.2 | Open each article page twice | `source_view_token` returned; snapshot rows created (count them, F-11 baseline) |
| 1.3 | Run inference once | one succeeded invocation per article and stage; `quote_checks` present on every chat prediction with states among exact / normalized / not_found / ambiguous |
| 1.4 | Replace one article's full text after inference | prediction still points at the old snapshot; `current_display_text_drift` reported; no rebinding |
| 1.5 | Submit a review with the displayed token, then one with a stale token after a replacement | first binds `view_bound`; second is rejected with 422, nothing stored |
| 1.6 | Record support assessments on two links (one `supports`, one `contradicts` with reason) | append-only rows; a new inference on the same article does not inherit them |
| 1.7 | Add the reviews to gold, refresh once | `GoldRevision` history of 2; source/evidence/answer staleness reported separately |
| 1.8 | Run a SystemOne or ONNX scorer on the same lot | invocations retain windows/feeds; `quote_checks` NULL and producer `not_produced` (F-5 after fix) |
| 1.9 | Export RO-Crate, then validate offline | `python -m app.evidence_crate` and `python -m app.portable_replay` pass; human entries carry `annotation_id`, `created_at`, pseudonymous ref, no e-mail |
| 1.10 | Delete one reviewer account in a disposable copy | `actor_ref` / `reviewer_ref` retained and identical policy in crate (F-4) |

Exit: every row's evidence archived; any deviation opens an issue before Phase 2.

### Phase 2, independent references on held-out papers (24 papers, Stage A)

Prerequisites: codebook v1 signed by both reviewers and the adjudicator; manifest frozen and validated (`cohort_sha256`, `configuration_sha256`); reviewer qualifications and conflicts recorded per paper.

Embargo (F-7 has no code enforcement yet), done by configuration:

- One pilot project containing only the 24 held-out papers, with the two reviewers as annotators and nobody else. No model registered on the project. No benchmark, gold or export performed before Stage A lock.
- The adjudicator is not a member until both reviewers have locked.
- The project owner records a permission snapshot (`GET /projects`, member list, model registry, admin status for each person) before unblinding and stores it beside the manifest.
- Reviews are made in a `blind` search. The articles list must not show another reviewer's answers (F-1 fixed and re-tested with the second reviewer annotating last).

Reviewers annotate all seven fields per the codebook. Because annotations are not typed per codebook status/scope/span, each reviewer also fills the Stage A ledger rows (status, value, unit, scope, passage, offsets) from their own annotation export. Lock: compute `digest()` of each reviewer's rows and record the timestamp; ledger validator must pass with `stage: a_locked` once that state exists (F-6 ledger extension), otherwise archive the digests and timestamps manually.

Adjudication: the adjudicator resolves disagreements defined on (status, value, unit, scope), not on free-text reason (F-8). Original judgments are preserved. Report raw agreement and kappa before adjudication.

Exit: ledger validates; adjudication trail complete; embargo attestation archived.

### Phase 3, held-out model run and support assessment (Stage B)

1. Register the frozen model and prompt on the pilot project after Stage A lock. Run inference once on the 24 papers; every attempted call, including failures and invalid JSON, becomes an `attempts` row with its retained output hash. No reruns unless the predeclared retry rule applies.
2. Freeze the prediction revision ids. Both reviewers independently record support assessments in the UI on every proposed link, with rationale. The adjudicator resolves Stage B disagreements.
3. Export the RO-Crate (lead), validate offline, and build the ledger `stage_b` rows from the crate's `evidence/` bundle (adapter from F-6; until it exists, by script from the crate JSON with the mapping recorded).

### Phase 4, scoring and report

Run the scorer from manifest plus ledger (`python -m app.seip_scoring`, F-6). Publish for the 24 held-out papers: eligible coverage, reporting-status correctness, categorical and numeric correctness, binary checklist P/R/F1, verbatim rate, verified attribution, link correctness, false existence/unique attribution, unverifiable rate, support error, challenge error, with raw counts, `NA` for zero denominators, by stratum, cluster bootstrap intervals with seed and undefined-replicate counts, plus pre-adjudication agreement. Keep development papers out of every estimate. End with a human-approved continue/stop decision.

## Gates summary

| Gate | Condition | Blocking findings |
|---|---|---|
| G0 engineering | Phase 0 all green, PG migration evidence archived | F-0, F-1, F-3, F-4, F-5, F-10 |
| G1 smoke | Phase 1 table fully evidenced on 6 papers | F-12 (source type), F-2 if a scorer is in scope |
| G2 independence | signed codebook, embargo attestation, Stage A lock before any model run | F-7 process, F-8 |
| G3 scoring | scorer runs from ledger with every metric populated | F-6 |

## Deliverables and owners

| Deliverable | Owner | Where |
|---|---|---|
| Fix PRs F-0, F-1, F-3, F-4, F-5 | engineering | one PR each, tests included |
| Ledger v1.1 schema, ledger→scorer adapter, scorer CLI, agreement/kappa | engineering | `app/seip_reference_ledger.py`, `app/seip_scoring.py`, `docs/schemas/` |
| Signed codebook v1 | two reviewers + adjudicator | `docs/protocols/`, SHA-256 in manifest |
| Frozen manifest, exclusions, source files, hashes | project owner | pilot folder, outside the repo if licensing requires |
| Permission snapshot and lock timestamps | project owner | pilot folder |
| Phase 1 evidence table | engineering | pilot folder |
| Scoring report and decision | all | `docs/audits/` |

## Schedule assumption

Phase 0 and the five blocking fixes: about one week of engineering. Phase 1: two days. Phase 2 depends on reviewer availability (protocol budget 6–10 reviewer-days). Phases 3–4: three days after the adapter exists. Re-estimate after Phase 1.
