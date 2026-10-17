# SEIP merge audit and fix priorities

Audit date: 2026-10-17. Scope: the 28 stacked PRs (#8, #12–#39) merged into `dhuzard/diggypaper` `main` on 2026-10-17, from baseline `9e5859c1d00921fb19f5c61146deeb39977b2df9` to the merge of PR #39 (`09abd6f`). This is a source-inspection audit of the merged result; it reports no empirical model accuracy and no scientific result. It complements, and does not replace, the [original SEIP audit](2026-10-scientific-evidence-integrity.md).

## 1. What was merged and how

115 files changed, about 21k lines added. Seven additive Alembic migrations `0022`–`0028` form a linear chain from `0021`. Two sibling conflicts were resolved during the merge by reusing the author's own resolutions from the `seip/integration-smoke` history:

- `backend/app/tasks/inference.py`, `scorer-provenance` (#28) vs `answer-link-integration` (#27): scorer-aware `bound` invocation, quote checks skipped for scorers, evidence graph built only when checks exist.
- `backend/app/api/v1/gold.py`, `gold-integrity` (#34) vs `blind-review-guard` (#25): reviewer-scoped `latest_reviews`, assessment-reference guard, gold visibility restricted to the reviewer's own source annotation in blind searches.

The final `main` tree is byte-identical to the `integration-smoke` tip plus the three documentation-only branches. Four PRs (#34, #36, #37, #39) needed a `main` merge pushed to their branch because GitHub's conflict check disagreed with git's clean merge; no code changed in those merges.

### Checks executed on the merged `main`

| Check | Result |
|---|---|
| `ruff check app tests` | pass |
| Frontend `tsc --noEmit`, `next lint`, `jest` | pass, 47 tests in 6 suites |
| Backend `pytest` (SQLite) on Windows | **collection error** in 4 modules, see F-0 below. With a one-file stub for the Unix `resource` module on `PYTHONPATH`: 832 passed, 16 skipped (Postgres-only), 10 min 31 s |
| PostgreSQL migration tests (`test_provenance_migration.py`, `test_review_binding_postgres.py`) | **not run**: they skip unless `SEIP_TEST_POSTGRES_URL` is set, and CI has no Postgres service |
| Alembic chain | linear, `0021 → 0022 → … → 0028` |

## 2. Implementation status against the plan

Based on code, not on PR titles. Issue numbers refer to the GitHub epic #1 sub-issues.

| Wave / issue | Status | Evidence |
|---|---|---|
| 0 contract | done | `docs/design/seip-provenance-contract-v0.1.md`, `tests/fixtures/provenance_v1.json` |
| 1A #2 source coherence | done | `fulltext.py` `_apply_fulltext` atomic under savepoint, stale PDF deleted on JATS adoption, no methods inheritance; `test_fulltext.py` |
| 1B #6 export loss fix | done | `crate.py` human entries keep `annotation_id`, `created_at`, quotes, pseudonymous ref; `test_crate_human_evidence.py` |
| 2A/2B #2 storage and chat capture | done | migration `0022`, `provenance_capture.py`, `provenance_chat.py`, `tasks/inference.py`, chat path of `tasks/benchmark.py` |
| 2C #2 scorer traces | **partial** | `provenance_scorer.py` used only by normal inference; benchmark scorer path uncaptured (F-2) |
| 2D #2 review binding | done | `review_source.py`, `annotations.py` GET token / POST validation, `test_review_binding_postgres.py` (PG-only) |
| 3A #3 deterministic matching | done | `quote_matcher.py`, `quote_checks.py`, migration `0023`, `QuoteOccurrencePanel.tsx` |
| 3B #4 answer links | done for normal chat, **partial** overall | `evidence_links.py`, `evidence_prediction.py`, `0025`; benchmark predictions and human annotations carry no graph |
| 4 #5 human assessment and gold | done | `support_assessments.py`, `0027`, `gold.py`, `0028`, `SupportAssessmentPanel.tsx` |
| 5 #6 portable bundle | done | `portable_replay.py`, `evidence_crate.py`, schemas and examples |
| 6 #7 qualification | **partial** | protocol, scorer, manifest and ledger validators exist; no ledger→scorer adapter, no CLI to score, no agreement/kappa, no embargo enforcement (F-6, F-7, F-8) |

## 3. Findings and fix priorities

Priority: **P0** blocks correct use of the merged features or leaks data; **P1** contract gap or likely bug that must be closed before the pilot; **P2** quality, to schedule. "Pre-existing" means the defect was present at the baseline and the merge did not introduce it.

### P0

**F-0. Backend cannot import on Windows.** `backend/app/tasks/benchmark.py:14` does `import resource` at module level (Unix-only), used once at line 346 for RSS reporting. On Windows, `test_app.py`, `test_benchmark.py`, `test_provenance_migration.py` and `test_review_binding_postgres.py` fail at collection and the Celery worker cannot import the task module. Pre-existing since the initial commit, but the merge made Windows the project owner's development machine for the pilot. Fix: guard the import (`try/except ImportError`) and report `None` for RSS when unavailable. One line, add a test that imports the module.

**F-1. Blind articles list reveals other reviewers' latest judgment.** `backend/app/api/v1/projects.py:673` calls `latest_reviews(db, ids, scope)` without `reviewer_id`; once the caller has their own annotation on an article, the `review`, `confidence`, `answers` and `review.by` shown in the list come from whoever annotated last. Every sibling endpoint (annotation GET, gold list, `gold_from_reviews`, support assessments) was narrowed to the caller's own annotation in this merge; the list was not. Pre-existing, but it now contradicts the documented blind guarantee and defeats the Stage A blinding of the pilot. Fix: pass `reviewer_id=user.id` when `scope.kind == "blind"` and add a test where another member annotates *after* the caller.

### P1

**F-2. Benchmark scorer calls are not captured.** `backend/app/tasks/benchmark.py:254` calls `score_prediction(model, project, article)` with the mutable ORM article and no trace; SystemOne windows and ONNX feeds in benchmark runs are never recorded, and the text scored is not the frozen snapshot. Rows are honestly `legacy_unversioned`, so nothing is misbound, but plan waves 2B/2C are only half met. Fix: route the scorer branch through `capture_article_source` + `provenance_scorer` as normal inference does.

**F-3. `gold_from_reviews` accepts article ids from other projects (PLAUSIBLE).** `backend/app/api/v1/gold.py:394-400`: `payload.article_ids` is never checked against `Article.project_id`; for the default search `in_search` matches `search_id IS NULL` legacy annotations. A member of project A could create a `GoldArticle` in A from a legacy annotation of project B. `_reviewed_ids` is project-joined, so only the explicit-ids path is exposed. Pre-existing. Fix: filter `article_ids` by project before `latest_reviews`; add a cross-project test.

**F-4. Two incompatible reviewer pseudonyms in one crate.** `evidence_crate.py:221` exports `actor_ref` = global `anonymized_actor_ref(email)` (`pseudonym.py:8`), while `crate.py:58` exports annotations' `reviewer_ref` as a project-scoped derivation and the crate README says refs are project-scoped. Inside one archive the same person's annotation and assessment cannot be correlated; across archives the global `actor_ref` links the same reviewer between unrelated projects. Not reversible without the salt. Fix: choose one policy (project-scoped is the documented one) and use it for assessments and gold `review_actor_ref` too; document it in audit §12 terms.

**F-5. `not_produced` is indistinguishable from "legacy, not checked" in API and UI.** `annotations.py:127` returns `quote_checks: NULL` both for pre-SEIP predictions and for scorer predictions (`tasks/inference.py` skips checks for scorers); `QuoteOccurrencePanel.tsx` renders both as "not checked". Only the evidence crate derives `not_produced`. Contract item 4 requires the producer state to be explicit. Fix: expose `quotation_producer` from the invocation in the article GET payload and add a distinct UI string.

**F-6. The pilot scorer cannot be run on pilot data.** `seip_scoring.score_pilot` is a library function with no CLI/API and nothing builds its `references` / `outputs` / `evidence` inputs from the reference ledger (`grep score_pilot` finds only the manifest module importing dataclasses). The ledger's Stage B rows also lack fields for existence/uniqueness claims, attribution outcome, negation/conflict challenge flags (`seip_reference_ledger.py:205-330`), so the protocol's false-existence, false-unique-attribution, unverifiable-quote and challenge metrics have no data path. Fix: extend the ledger schema v1.1 with those fields, add `ledger → scorer inputs` adapter and a `python -m app.seip_scoring manifest ledger` CLI.

**F-7. No project-wide embargo for the pilot.** Blinding is per search only; a reviewer with access to another search, an admin, or anyone with export rights can see held-out predictions, gold or benchmarks. The protocol's access gate (§"Blinding and access gate") has no enforcement and no attestation field in the manifest/ledger schemas. Fix for the PoC: a dedicated pilot project per reviewer role with no model run until Stage A lock, plus a recorded permission snapshot (see the PoC plan). Code fix later: a project-level `embargo_until_stage_a_lock` flag that blocks inference, benchmark, gold listing and export.

**F-8. Pre-adjudication agreement and kappa are not computed**, and `_stage_a` treats any dict inequality (including differing `reason` text or offsets on identical status/value) as a disagreement needing adjudication (`seip_reference_ledger.py:150-152`). Fix: define label equality on (status, value, unit, scope) and add raw agreement and Cohen's kappa to the scorer output.

**F-9. No UTF-16 adapter on the frontend.** Offsets are Unicode code points server-side (correct, tested with astral characters); nothing in `frontend/src` converts them, and the `QuoteChecks` type omits `candidates`. Nothing mis-renders today because no UI consumes offsets, but the first consumer will. Fix: add `codePointToUtf16` helper with an astral test before any offset-driven highlight ships.

**F-10. PostgreSQL migration tests never run in CI.** `.github/workflows/ci.yml` has no Postgres service; the PG-only CHECK constraints, advisory-locked invocation guard and the replacement race test exist only behind `SEIP_TEST_POSTGRES_URL`. SQLite trigger approximations in `models.py` cannot detect drift. Fix: add a Postgres service job running `alembic upgrade head` from a populated `0021` database plus the two PG test modules.

### P2

- **F-11.** Snapshot storage is unbounded: a new `SourceSnapshot` row is committed on every article GET (`annotations.py:155`) and per job/article, each storing the text about 8–10× (four views, components, canonical bytes), with no content-hash dedup. Add dedup by `(article_id, canonical_sha256)` and a retention rule for unreferenced snapshots.
- **F-12.** PMC-imported full text is snapshotted with `source.type = "metadata_only"` because the import task never sets `fulltext_source` (`tasks/__init__.py:339-352`, `provenance_capture.py:263`). Set `fulltext_source="pmc"` at import.
- **F-13.** One invalid model link nulls the whole evidence graph (`evidence_prediction.py:115-131`), so valid quotes of the same prediction lose their captured-input context and become unassessable. Design-sanctioned; consider keeping valid edges and marking the envelope `partially_invalid`.
- **F-14.** An oversized provider response raises `response_too_large_for_provenance`, which `_do_inference` treats as fatal for the entire job (`provenance_chat.py:153-173`, `inference.py:422`). Record a failed invocation for that article and continue.
- **F-15.** ONNX trace hardcodes `truncation="unknown"` although truncation is determinable from token count (`provenance_scorer.py:223-224`).
- **F-16.** `gold_from_reviews` `skipped` count in blind scope reveals that another member already golded the article (`gold.py:437-500`).
- **F-17.** Benchmark `frozen_sources` holds every article snapshot in memory for the whole run (`benchmark.py:237`).
- **F-18.** Non-string quotes are persisted verbatim and unbounded in `quote_checks_json` (`quote_matcher.py:346-349`, `quote_checks.py:86`); cap serialized size.
- **F-19.** Review source token expires after 12 h with a generic 422, indistinguishable from tampering (`review_source.py:321`, `annotations.py:468`).
- **F-20.** Design docs still describe their slice as unwired: `seip-provenance-contract-v0.1.md` ("not implemented"), `evidence-links-v1.md`, `quote-matcher-v1.md`, `quote-check-persistence-v1.md`, `seip-portable-replay.md` ("does not integrate with RO-Crate export yet"). Also `annotations.py` uses `current_display_text_drift` with an undocumented `view_bound` state where the contract names `current_source_drift`. Status notes were added to these docs in this audit; the field-name mismatch remains.
- **F-21.** The ledger requires `conflict == "none"` for all three actors on all 24 papers (`seip_reference_ledger.py:95-104`), whereas the protocol allows reassigning a conflicted case to another eligible adjudicator.
- **F-22.** `scorer_manifest` never sets `PaperAttempt.example=True` for `prompt_example_paper_ids`, so `selection.excluded_examples` is always 0 (`seip_pilot_manifest.py:586-590`).

## 4. What is solid

- **Binding path.** `capture_article_source` takes `SELECT … FOR UPDATE` (SQLite `BEGIN IMMEDIATE`), commits before any provider call; rendering and scorer text use only the frozen snapshot; `PersistedChatTrace.before_dispatch` refuses to insert if the dispatched messages differ from the rendered origin map. Prepared → unknown → succeeded/failed transitions are enforced in both dialects, and predictions cannot reference non-succeeded invocations (`0024` + triggers). Credentials, headers and provider error bodies stay out of captured artifacts.
- **Matching.** Exact and NFC/whitespace-normalized passes are distinct, no case folding or punctuation stripping, offsets are code points, every normalized hit is re-validated against the original slice, repeated matches stay `ambiguous`, truncation never claims uniqueness, only `origin == "target"` spans are searched, and candidate/examination/size budgets are bounded. The 1 MB benchmarks in `docs/benchmarks/` are consistent with the implementation.
- **Envelope.** Server-derived `uuid5` evidence ids; dangling, duplicate, cross-field and same-revision mutations rejected; DB triggers make checks, graph and checked output immutable, so an old client posting only `evidence_quotes` cannot erase structured evidence. `_review_status` ignores envelope metadata.
- **Assessments and gold.** Append-only assessments with mandatory rationale, exact revision refs, blind gate on own annotation; gold revisions immutable, legacy rows explicitly `legacy_unqualified`, three independent staleness dimensions with `unknown`.
- **Export and replay.** Blind crates lead-only and prediction-free; human entries pseudonymous; portable replay re-runs rendering and matching offline, verifies SHA-256 and canonical JSON, and rejects the adversarial fixture.
- **Scoring arithmetic.** All protocol metrics present with stated denominators; missing/invalid outputs are attempted failures; zero denominators are `NA`; dev/example papers excluded; cluster bootstrap with fixed seed and undefined-replicate count. Three test expectations were re-derived by hand and confirmed.

With the Windows `resource` stub, the full backend suite passes: 832 passed, 16 skipped (all Postgres-only), on Python 3.12.6, SQLite in-memory.

## 5. Recommended order

1. F-0, F-1 (one small PR each, same day).
2. F-3, F-4, F-5 (API/export correctness before anyone runs a pilot export).
3. F-6, F-8 and the ledger schema extension, then F-7 as process now and code later.
4. F-2, F-10 (provenance completeness and CI evidence).
5. F-9 before any offset-driven UI; F-11/F-12 before a large corpus; remaining P2 as backlog.

The [PoC test plan](../plans/2026-10-seip-poc-test-plan.md) states which of these gate each pilot stage.
