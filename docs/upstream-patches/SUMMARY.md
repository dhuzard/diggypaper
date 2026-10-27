# Upstream patches — summary

Base: `upstream/main` = 5557435 (BENETNATH/diggypaper). Each patch is a single commit made on its
own branch from that commit (`u1-resource-guard`, `u2-blind-list-own-review`,
`u3-gold-from-reviews-project`, `u4-fulltext-atomic-replace`, `u5-crate-human-evidence`,
`u6-pubmed-window-test` in the worktree), author `damien huzard <damien.huzard@gmail.com>` (the fork's identity; change with
`git am --reset-author` if another one is wanted), one-line imperative message, no trailers.
No two patches touch the same file.

Environment: venv at `scratchpad/venv` (Python 3.13.17), installed from `backend/` with
`pip install -c constraints.txt -e '.[dev]'`; no dependency added. Tests are the offline SQLite
suite (no PostgreSQL/Redis/Celery broker); no integration run was attempted.

Check commands (run for every patch, from `backend/` on that patch's branch):

    python -m ruff check app tests          -> "All checks passed!"
    python -m ruff format --check app tests -> "102 files already formatted"
    python -m pytest -q <modules>           -> see per patch

Apply check: `scratchpad/verify_am.sh` clones the repo, checks out 5557435 and runs
`git am` for each patch independently, then all six stacked:

    OK  U1-resource-import-guard.patch
    OK  U2-blind-list-own-review.patch
    OK  U3-gold-from-reviews-project-scope.patch
    OK  U4-fulltext-atomic-replace.patch
    OK  U5-crate-human-evidence.patch
    OK  U6-pubmed-window-test-date-independent.patch
    OK  all six stacked: 6 commits

On that stacked checkout: `ruff check` / `ruff format --check` pass; full suite (`python -m pytest -q`):
    438 passed in 201.86s (six patches stacked). With U1-U5 only: 437 passed, 1 failed — the pre-existing date-dependent failure in tests/test_pubmed.py that U6 fixes (it fails on unmodified upstream/main too on 2026-10-27).

Baseline before any change (upstream/main, affected modules only):
`pytest tests/test_fulltext.py tests/test_searches.py tests/test_gold_api.py tests/test_crate_import.py tests/test_team_export.py tests/test_benchmark.py` -> 58 passed.

## U1 — U1-resource-import-guard.patch (fork 7cbfb78)

Files: `backend/app/tasks/benchmark.py` +13/-2, `backend/tests/test_benchmark.py` +19/-0
(32+/2- in all).
Test added: `test_benchmark_imports_without_resource` (reloads the module with
`sys.modules["resource"] = None`, asserts `resource is None` and `_peak_ram_mb() is None`).
Run: `pytest -q tests/test_benchmark.py tests/test_benchmarks_api.py` -> 24 passed.
Not ported: the fork's second test (`test_peak_ram_matches_platform`) and its separate
`tests/unit/` module — upstream keeps tests flat, so the one test was appended to
`tests/test_benchmark.py`.

## U2 — U2-blind-list-own-review.patch (fork ba0d589)

Files: `backend/app/api/v1/projects.py` +17/-15, `backend/tests/test_searches.py` +54/-0
(71+/15-).
Change: `latest_reviews(..., reviewer_id=None)` (upstream had no such parameter; the fork had
added it in an earlier commit, so this patch adds it) and `list_articles` passes `user.id` when
`scope.kind == "blind"`; `mine` is derived from that map (one query dropped).
Test added: `test_blind_list_shows_only_the_callers_own_review` (A reviews, B reviews later;
A and B each see only their own review; C with no review sees nothing).
Run: `pytest -q tests/test_searches.py tests/test_annotations_api.py tests/test_gold_api.py`
-> 46 passed. Checked the new test fails on unmodified `projects.py` (AssertionError, line 433).
Adaptation: upstream annotations need no `source_view_token`, so the helper posts
`{"ai", "confidence_score"}` directly. The fork's assertion `annotated is False` for the third
member was replaced by `prediction_status is None`: upstream's `annotated` flag in
`article_filter` means "any review in this search" (not own-only) and changing it is a separate
concern not in scope here.

## U3 — U3-gold-from-reviews-project-scope.patch (fork 2becddd)

Files: `backend/app/api/v1/gold.py` +20/-2, `backend/tests/test_gold_api.py` +32/-1 (52+/3-).
Change: explicit `payload.article_ids` are checked against `Article.project_id`; any id outside
the project -> 422 `article not in this project` (same message as the manual gold POST).
Test added: `test_from_reviews_rejects_article_ids_of_another_project` (foreign article with a
legacy `search_id NULL` annotation; 422 and the gold list stays empty).
Run: `pytest -q tests/test_gold_api.py tests/test_searches.py` -> 25 passed. Checked the new
test fails on unmodified `gold.py`.
Adaptation: upstream's endpoint has no `reviewer_id` / assessment refs (fork-only), so the
check wraps the existing `latest_reviews` call; the fork test's direct `GoldArticle` DB
assertion was dropped in favour of the API listing (same guarantee, fewer imports).

## U4 — U4-fulltext-atomic-replace.patch (fork 23c0cc5)

Files: `backend/app/fulltext.py` +25/-11, `backend/app/tasks/__init__.py` +2/-1,
`backend/tests/test_fulltext.py` +175/-1 (202+/13-).
Change: `_apply_fulltext()` under `session.begin_nested()` (store the PDF or delete the stale
one; sections/source/url/note; `methods_text` from this text only, no inheritance; `flush`);
`apply_uploaded_pdf` and `complete_article` call it. `_complete_fulltext` reads `pmid` before
the attempt (a savepoint rollback may expire the ORM object; `art.pmid` in the `except` would
otherwise need a lazy load).
Tests added: `test_complete_article_replaces_entire_source` (jats/pdf x methods/no-methods),
`test_uploaded_pdf_replaces_source_without_old_methods`,
`test_source_replacement_failure_rolls_back_even_if_caller_commits` (jats/pdf),
`test_batch_fulltext_continues_after_source_write_failure`, plus the `article_with_pdf`
fixture and two helpers.
Run: `pytest -q tests/test_fulltext.py tests/test_tasks_import.py tests/test_annotations_api.py`
-> 64 passed. On unmodified app code 6 of the 8 new cases fail (the two that pass already held
upstream: PDF-with-methods replacement and upload without old methods).
Not ported (deliberately, to keep it minimal): the fork's SQLite `BEGIN` workaround (the
savepoint tests pass on upstream's aiosqlite fixture without it; PostgreSQL has native
SAVEPOINT), the `refresh(with_for_update=True)` + `protect_upload` re-check after the network
fetch (upstream already refuses to overwrite a `pdf:upload` before fetching; the concurrent
upload-during-retrieval guard is a separate concern) and the three fork tests tied to those
(`..._failed_retrieval_preserves_source`, `..._preserves_manual_upload`,
`..._preserves_upload_committed_during_retrieval`).

## U5 — U5-crate-human-evidence.patch (fork 0543510 + b2dd65f)

Files: `backend/app/crate.py` +21/-0, `backend/app/api/v1/export.py` +13/-3,
`backend/tests/test_crate_import.py` +75/-0 (109+/3-).
Change: `crate.reviewer_reference(project_id, annotator_hash)` (the fork's `app/pseudonym.py`
helper, 7 lines, put in `crate.py` rather than a new module); `_load` derives
`h.reviewer_ref` from `annotator_hash` or, for a live account, `sha256(anon_salt + email)` (the
digest `cleanup.delete_user` / `/me/anonymize` already persist); `article_record` adds
`annotation_id`, `created_at`, `reviewer_ref`, `evidence_quotes`; crate README paragraph.
`crate_import.py` untouched: it only reads `answers` / `notes` from `human` entries, so the
extra fields are ignored (existing round-trip test still passes on the real export).
Test added: `test_crate_keeps_human_evidence_under_a_project_scoped_pseudonym` (ids, dates,
quotes incl. unicode/whitespace/duplicates, same `reviewer-...` for the same reviewer, null for
an annotation without identity, no e-mail / user id in any JSON or Markdown file, README mentions
`reviewer_ref`, different reference in another project, reference unchanged after
`delete_user`).
Run: `pytest -q tests/test_crate_import.py tests/test_team_export.py` -> 18 passed. Checked the
new test fails on unmodified app code (KeyError on `annotation_id`).
Not ported: the fork's blind-scope changes to `export.py` bundled in 0543510 (lead-only
download of blind crates, `inf_job` hidden in blind scope) and b2dd65f's propagation of the
pseudonym to support assessments / gold APIs and the evidence crate (fork-only features) —
separate concerns. Note the "no e-mail in the crate" check excludes `results.xlsx`, which by
upstream design still carries reviewer names/e-mails (column "Relu par") and the exporting
account's e-mail; the README paragraph says so.

## U6 — U6-pubmed-window-test-date-independent.patch (no fork commit; test-only)

Files: `backend/tests/test_pubmed.py` +3/-1.
Change: in `test_fetch_all_pmids_splits_queries_over_the_ncbi_window`, the fake `fetch_pmids`
returns each window's ids newest first (`sorted(..., reverse=True)`), like esearch's default
order, so `got[0]` / `capped[0]` no longer depend on how the date bisection (which uses
`date.today()`) cuts the leaf windows. No implementation change, no new test (the existing test
is made deterministic).
Run: `pytest -q tests/test_pubmed.py` -> 23 passed. On unmodified upstream/main the module gave
`1 failed, 22 passed` on 2026-10-27 (`AssertionError` at line 442, `'1035' == '1039'`).
