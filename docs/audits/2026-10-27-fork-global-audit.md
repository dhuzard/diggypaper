# Global fork audit: `dhuzard/diggypaper` versus upstream

Audit date: 2026-10-27. Scope: everything on `main` (`41931dc`, merge of PR #68) since the fork
point `9e5859c` (BENETNATH, 2026-10-15), compared with upstream `BENETNATH/diggypaper` `main`
(`5557435`, 2026-10-16). Source inspection plus the checks listed in §2. No scientific result.

This audit answers three questions: what the fork changed, what should become pull requests
(toward upstream and inside the fork), and where the product should go next. It assumes the
upstream maintainer's stated preference for small, non-over-engineered changes.

## 1. Size and shape of the divergence

| Side | Commits since fork point | Files | Lines |
|---|---|---|---|
| Fork (`dhuzard/main`) | 179 (50 PRs) | 613 (562 added, 51 modified) | +72 349 / −191 |
| Upstream (`BENETNATH/main`) | 22 | 122 | +8 039 / −2 605 |

Fork additions by area (lines added):

| Area | Lines | Notes |
|---|---|---|
| `docs/contracts/` | 25 929 | 373 files: three frozen contracts with JSON Schemas, validators and 363 generated JSON/JSONL fixtures (2.5 MB) |
| `backend/tests/` | 16 461 | 59 new test modules |
| `backend/app/` | 16 267 | 51 new modules (13 804 lines) + 2 587 lines in 12 shared files |
| `runs/` | 6 192 | committed outputs and logs of one NTS proof-of-concept run |
| `frontend/src/` | 3 078 | 4 components, 1 page, `api.ts` +377, i18n +728 |
| `backend/alembic/` | 1 688 | 13 migrations `0022` → `0034` |
| `docs/` (other) | 2 464 | 3 audits, 11 design docs, 4 protocols, 4 schemas, 2 plans, benchmarks |

Schema footprint: 19 new tables, `db/models.py` grew from 791 to 2 065 lines. Five new
API routers (`support_assessments`, `findings`, `nts`, `connector_grants`, `connector`).

### 1.1 What the fork built (by workstream)

1. **SEIP provenance chain** (epic #1, migrations `0022`–`0028`, PRs #12–#39, #41–#45, #61).
   Immutable source snapshots and captured model invocations, prediction-to-invocation guard,
   review bound to the displayed text, deterministic quote presence check, versioned
   answer-to-evidence graph, benchmark result binding, human support assessments, gold
   revisions with staleness, evidence companion in the RO-Crate, offline portable replay,
   pilot manifest / reference ledger / scorer. About 7 500 backend lines, two frontend panels.
2. **Finding curation** (#46, `0029`–`0030`, PR #49–#52): many findings per article with
   immutable revisions, independent review, rights declarations, and export as the frozen
   `source-evidence-packet/0.1.0` contract. About 1 900 backend lines.
3. **NTS publication traceability** (#11, `0031`–`0033`, PRs #54–#58, #63–#66): optional
   research module (`backend/app/nts/`, 4 300 lines) with a CLI, a deterministic "extractor"
   inference backend, ALURES join, a project tab, a review queue and committed PoC outputs.
4. **ORAtlas connector** (#53, #55, `0034`, PRs #67–#68): LEAD-controlled connector grants with
   audit, a privacy-minimal project descriptor, and the `finding-credit-companion/0.1.0`
   contract. About 1 100 backend lines.
5. **Bug fixes found by the fork's own audits**: atomic full-text replacement, human evidence
   kept in RO-Crate, Windows import guard (F-0), blind list leak (F-1), gold cross-project ids
   (F-3), one reviewer pseudonym policy (F-4), explicit `not_produced` state (F-5), benchmark
   scorer capture (F-2), PMC source type at import (F-12).
6. **Governance docs**: `AGENTS.md`, two audits, a PoC test plan, an agent implementation plan.

### 1.2 What upstream built in the same week

RO-Crate **import**; PubMed **query assistant**; corpus from a **reference list** (DOI/PMID,
BibTeX, RIS, CSL, Zotero CSV); **citation enrichment** (iCite); **scheduled successive
searches**; storage alerts and **project retention**; quick search index (`pg_trgm`); LaTeX
stripped from PMC full text (data migration `0026`); the Prompts tab removed; hover cards and
toolbar reworks on the results list; and, decisively, **the decision models (Laya, Kev, ONNX,
SystemOne) were removed** (`5845f0f`, migration `0023`).

## 2. Checks run for this audit

| Check | Result |
|---|---|
| `ruff check app tests`, `ruff format --check` | pass |
| Frontend `tsc --noEmit`, `jest` | pass, 66 tests in 8 suites |
| Backend `pytest` (SQLite, Python 3.12, Linux) | pass, 1 369 passed, 20 skipped, 3 min 20 s |
| PostgreSQL-only tests (`SEIP_TEST_POSTGRES_URL`) | **not run**, no database in this environment; still not in CI (#62) |
| Simulated merge of upstream into the fork (`git merge-tree`) | **20 conflicted files**, see §3 |

### 2.1 Backend test run

`pip install -c constraints.txt -e '.[dev]'` in a fresh virtualenv, then `python -m pytest -q`:
1 369 passed, 20 skipped (all PostgreSQL-only), 200 s. The 2026-10-17 audit recorded 832
passed, so the fork added about 540 tests in ten days, almost all for SEIP, findings, NTS and
the connector.

## 3. The structural finding: the fork and upstream have crossed

This is the one item that changes every other recommendation.

**3.1 Migration numbers collide.** Both sides created `0022`–`0028` with different content
(fork: provenance storage → gold revisions; upstream: search codes → quick search index).
Alembic revision ids differ, so a naive merge produces two heads and `alembic upgrade head`
refuses to run. Any fork database already at `0034` cannot take the upstream chain without a
hand-written merge revision, and upstream's data migrations (`0023` deactivates decision-model
registry rows, `0026` rewrites full text) must be reasoned about against SEIP snapshots.
Snapshots are separate immutable rows, so `0026` does not violate the SEIP invariant, but it
will make every pre-`0026` prediction report `current_display_text_drift` afterwards. That is
honest, and should be documented, not hidden.

**3.2 Upstream deleted what the fork instruments.** `inference/systemone.py`,
`inference/onnx_driver.py`, `inference/scoring.py` and `test_systemone.py` are gone upstream;
the fork modified all four and built on them: `provenance_scorer.py` (295 lines),
the scorer branch of `provenance.py`, migration `0022` (scorer trace columns), migration
`0031` (the NTS "deterministic backend" registers itself as an inference backend next to the
scorer kinds), PR #61 (benchmark scorer capture), and three test modules. All of this is dead
weight once the fork follows upstream, and it was never part of the upstream maintainer's
direction.

**3.3 Twenty files conflict in a simulated merge**, including `tasks/inference.py`,
`tasks/benchmark.py`, `api/v1/projects.py`, `api/v1/export.py`, `api/v1/models.py`,
`core/config.py`, `main.py`, `docker-compose.yml`, the articles page, the project layout and
all four i18n files. Thirteen more files auto-merge but need reading (`db/models.py`,
`fulltext.py`, `annotate/page.tsx`, `lib/api.ts`).

**3.4 Upstream features that touch SEIP semantics** and will need a decision when syncing:

- RO-Crate import recreates predictions and reviews from a zip. In the fork those rows must be
  `legacy_unversioned` (no invocation, no snapshot) or carry the crate's `evidence/` records if
  present; never fabricated snapshots.
- Scheduled searches and citation-enrichment searches run through `tasks/inference.py`, which
  the fork wraps with capture; the wrap must stay a single call site.
- The query assistant and the reference import create articles without `fulltext_source`;
  the F-12 fix needs to cover those writers too.

**Recommendation (P0):** stop adding features until the fork is rebased onto upstream. Every
week of delay widens the 20-file conflict set, and the next upstream UI rework will hit
`annotate/page.tsx` where both SEIP panels are mounted.

## 4. Status of the fork's own audit findings

Closed since the 2026-10-17 merge audit: F-0, F-1, F-2, F-3, F-4, F-5, F-12.

Still open: F-6 (no scorer CLI / ledger adapter), F-7 (no embargo enforcement), F-8 (no
agreement / kappa), F-9 (no UTF-16 offset helper in the frontend), F-10 (no PostgreSQL in CI,
now issue #62), F-11 (a `SourceSnapshot` row per article GET, no dedup), F-13 to F-22.

Of these, F-11 is the one with an operational cost today: snapshot storage grows with every
page view, roughly 8–10× the text size per row. F-14 (one oversized provider response fails the
whole job) is the one a user will hit first.

## 5. Suggested pull requests toward upstream

Rule applied: a change goes upstream only if it fixes a defect that exists in upstream's current
code, needs no new table, and reads as one screen of diff. All five below were verified against
`upstream/main` on 2026-10-27.

| # | PR | Why upstream needs it | Size |
|---|---|---|---|
| U1 | Guard the Unix-only `resource` import in `tasks/benchmark.py` | still at line 14 upstream; the worker and four test modules fail to import on Windows | 1 file, ~6 lines |
| U2 | Blind articles list shows only the caller's own review | upstream `projects.py:776` still calls `latest_reviews` without a reviewer; another annotator's answers leak in blind searches, contradicting the README | 1 file + 1 test |
| U3 | `gold_from_reviews` filters `article_ids` by project | upstream `gold.py:215` passes caller-supplied ids straight through; a member of project A can gold a legacy annotation of project B | 1 file + 1 test |
| U4 | Atomic full-text replacement in `fulltext.py` | upstream has no savepoint; a JATS adoption can leave a stale PDF and absent methods inherit the previous version | 1 file + tests (fork commit `23c0cc5`) |
| U5 | Keep human evidence in the RO-Crate article JSON (`annotation_id`, `created_at`, quotes, project-scoped pseudonymous `reviewer_ref`) | upstream `crate.py` exports none of these; the RO-Crate import upstream just added would benefit from them directly | 2 files + test (fork commit `0543510`, F-4 policy) |

One optional product-level proposal, worth a short discussion before any code: **U6, a quote
presence badge**. Upstream already highlights model quotes on the review page; a "quote not
found in the text sent to the model" counter next to the certainty dot is the single most
user-visible SEIP idea and can ship without any schema change by computing the check at
read time with the pure `quote_matcher.py` (509 lines, no dependencies). Propose it as an
issue first; only build it if the maintainer wants it.

Do not propose upstream: the 19 SEIP/finding/NTS/connector tables, the three contract suites
and their 2.5 MB of fixtures, `runs/`, the NTS module, the ORAtlas connector, `AGENTS.md`.
These are fork product decisions, not defects.

## 6. Suggested pull requests inside the fork (next 4–6 weeks, in order)

| # | PR | Scope boundary |
|---|---|---|
| I1 | **Rebase onto upstream `5557435`** | One PR. Renumber fork migrations `0022`–`0034` to `0029`–`0041` with a merge revision for databases already migrated; delete `provenance_scorer.py`, the scorer columns' writers and the scorer tests; keep the capture hook in `tasks/inference.py` to one call; re-run the full suite. Expect two to three days. |
| I2 | **#62 PostgreSQL lane in CI** | One job: Postgres 16 service, `alembic upgrade head` from a populated `0021` fixture, run the three `*_postgres.py` modules. No new abstractions. |
| I3 | **Snapshot dedup and retention (F-11)** | Reuse a `SourceSnapshot` when `(article_id, canonical_sha256)` already exists; a daily task deletes snapshots referenced by nothing. Upstream now has `maintenance.py` and celery beat, so hang it there after I1. |
| I4 | **Oversized provider response is a per-article failure (F-14)** | Record a `failed` invocation and continue the job. Small. |
| I5 | **Repository hygiene** | Move `runs/` out of the source tree (release asset or separate repo) and regenerate contract fixtures in tests instead of committing 363 of them; keep two or three canonical examples per contract. Cuts 32 000 lines from the diff without changing behaviour. |
| I6 | **Docs index** | One `docs/seip/README.md` listing each design doc with its status (`implemented`, `partial`, `not wired`) and the owning module; retire the "not implemented" notes that are now wrong (F-20). |
| I7 | **UTF-16 offset helper (F-9)** | Only when the first offset-driven highlight is built. Ten lines plus an astral-character test. |
| I8 | **Pilot scorer CLI and ledger adapter (F-6, F-8)** | Only if the 30-paper pilot is actually scheduled with named reviewers. Otherwise leave it. |

## 7. Product evolution and where to stop

**7.1 What is already over-built relative to its proven use.** The fork now carries four
parallel export formats of the same evidence (RO-Crate `evidence/`, `source-evidence-packet`,
`finding-credit-companion`, `research-project-descriptor`), two review models (annotation
support assessments and finding reviews), a research module (NTS) with its own CLI, companion
schema and committed run outputs, and a connector layer for a consumer (ORAtlas) whose own
contract is still open. None of this has been exercised by a second human reviewer on real
papers: the qualification pilot (issue #7) has not run, and Phase 1 of the PoC test plan has
no archived evidence table.

**7.2 Recommended posture: freeze contracts, run the pilot, then prune.**

1. Freeze the three contracts at 0.1.0 and the NTS module at its current state. No new
   schema versions, no new tables, until the pilot has produced its first scored report.
2. Run Phase 0 and Phase 1 of the PoC test plan on the rebased fork. This costs about a week
   and is the only thing that turns 72 000 lines into evidence that SEIP is useful.
3. After the pilot, prune by evidence: keep what reviewers used (expected: quote presence
   check, review-source binding, support assessments, evidence crate) and demote what they did
   not (candidates: gold revisions UI, finding-credit companion, project descriptor) to
   "kept but not extended".

**7.3 Future directions worth an issue, not a branch, today.**

- **Quote presence badge upstream (U6)**: the smallest possible SEIP feature for every user.
- **Review-source binding upstream**: a token on the article page, checked on review POST, is
  about 80 lines and prevents a real class of error (reviewing a text that was replaced
  mid-review). Propose after U1–U5 land and only if the maintainer reacts well to U6.
- **SEIP as optional modules with a thin hook surface.** Keep `db/models.py` for upstream
  tables and move the 19 fork tables into `app/db/seip_models.py` and friends; keep exactly
  one capture call in `tasks/inference.py` and one in `tasks/benchmark.py`. This is what
  makes every future upstream sync a one-day job instead of a one-week job.
- **ORAtlas**: keep the connector read-only and grant-based as it is; do not build OAuth or a
  hosted directory. The consumer side should prove it reads the descriptor before anything
  more is added here.
- **NTS**: decide whether it is a Diggypaper feature or a research artefact. If the latter,
  move `backend/app/nts/`, its migrations and `runs/` to a companion repository that imports
  Diggypaper's pure modules (`quote_matcher`, `finding_packet`). The project tab and the
  extractor backend would go with it.

**7.4 Maintenance cost to keep in view.** The fork adds nine times more lines than upstream
changed in the same period and touches 12 shared files. Each upstream sync will cost days
unless the hook surface shrinks (§7.3). Upstreaming U1–U5 reduces the shared diff a little;
I1 and the module split reduce it a lot.

## 8. Recommended order

1. U1–U5 as five small PRs to upstream, this week; they stand on their own and buy goodwill.
2. I1 rebase, then I2 CI lane, then I5 hygiene, on the fork.
3. Pilot Phase 0–1 on the rebased fork; archive the evidence table.
4. I3, I4, I6 as backlog; I7, I8 only when their trigger condition is met.
5. Decide the NTS home and the pruning list from the pilot's results, not before.

## 9. Executed on 2026-10-27 (same day, branch `ccr-ec7fc4cd-h2l2tn`)

| Item | Commit(s) | Outcome |
|---|---|---|
| U1–U5 (+ U6, a calendar-dependent upstream test) | `16e6e9a` | Patches, PR bodies and verification record in `docs/upstream-patches/`; each applies with `git am` on upstream `5557435`; the stacked checkout passes upstream's suite. PRs still have to be opened from a clone (see that README). |
| I1 merge upstream | `3e13113` | Upstream `5557435` merged; scorer provenance removed with the decision models; fork migrations renamed `f0022`…`f0034` plus merge revision `f0035`; four upgrade/downgrade paths verified on PostgreSQL 16; README documents the one-off `alembic stamp --purge` for databases migrated by the fork. |
| I2 PostgreSQL CI lane (#62) | `d58382d` | `backend-postgres` job: `alembic upgrade head` / `downgrade 0021` on a `postgres:16` service, then the four PostgreSQL-only modules. Not yet observed on GitHub Actions. |
| I3 snapshot dedup and retention (F-11) | `39950ef`, `056f05d` | Identical revision reused within 12 h; unreferenced snapshots deleted after 24 h by the existing 6-hourly maintenance task. No migration. |
| I4 oversized response (F-14) | `381d3b5` | One failed invocation for that article; the job and the benchmark run continue. |
| I5 hygiene | `38aa282`, `f261e43` | Draft-contract negative fixtures and regenerable NTS run outputs no longer committed (−20 800 lines); the frozen `source-evidence-packet/0.1.0` keeps its fixtures; `runs/` keeps the report, manifest, frozen inputs and logs. |
| I6 docs index (F-20) | `7ca5c9a` | `docs/seip/README.md` with per-document status; stale "not wired" notes replaced. |

Final checks on the combined tree: ruff check and format; backend `pytest` with `SEIP_TEST_POSTGRES_URL` set,
1 407 passed, 2 skipped (local PostgreSQL 16); frontend `tsc`, `lint`, `jest` 79 passed.
Not done: the pilot (Phase 0–1 needs papers, reviewers and a deployment), I7 and I8 (their trigger
conditions are not met), and the NTS home decision (§7.3), which is the owner's call.
