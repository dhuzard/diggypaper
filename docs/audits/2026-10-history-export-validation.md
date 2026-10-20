# Immutable finding-history export: validation report

Base `1522e9b`, branch `pico/history-export-5d9d8058`. Scope: `app/finding_packet.py`,
`app/api/v1/findings.py`, the four listed test files, `docs/design/finding-curation-v1.md`.
The frozen SEP schemas/validator under `docs/contracts/` are untouched.

## Defect

`_evaluate` rejected every non-latest revision (`not-latest`), so an approved revision 2
exported alone and the frozen validator reported `revision-gap` (`/tmp/scientific-evidence-preflight/reproduce_revision_gap.py`,
case 2). The API also chose one snapshot from the latest revisions' evidence and silently
dropped evidence on any other snapshot.

## Behaviour after the repair

- Chain export 1..n per stable finding id; historical content/evidence/judgments preserved,
  states derived from each revision's own reviews (see design doc); latest must pass the
  existing approval gate, otherwise the whole chain is withheld with a reason per revision.
- Fail closed for the whole chain: gap, duplicate, missing/foreign/mixed snapshot, unmatched or
  unrepresentable evidence, unrepresentable judgment, self-review; across chains: several
  snapshots on one paper, conflicting shared passage.
- Rights unchanged: latest declaration at export; none/metadata-only/disallowed licence withholds
  quotes (real hash kept); `restricted` omits the chain.
- API passes every snapshot cited by any revision (`papers[].snapshots`); the builder decides.
  `report.exported` counts revision records, `report.exported_findings` counts chains.

## Representational limits / owner choices

1. No approve/reject field in SEP 0.1.0: a rejected historical revision is `proposed` +
   `human-reviewed`; only its judgments remain. An approved superseded revision is
   `approved-for-export` (its own state); consumers must read the highest revision.
2. One snapshot per paper: a paper whose findings sit on different snapshots loses all of them
   from the packet until the owner decides how to reconcile (conservative choice made here).
3. `alembic/versions/0032_nts_tables.py` fails `ruff format --check .` on the base commit
   (out of scope, not touched).

## Commands (cwd `backend`, env `/tmp/diggypaper-history-venv-5d9d8058`)

Env: `uv venv --python 3.12 <venv>`; `uv pip install --python <venv>/bin/python -c constraints.txt -e '.[dev]'`.
`pytest` run as `python -m pytest` (bare `pytest` cannot import `tests`; `numpy`/`onnxruntime` are not in the dev extra).

| Command | Exit | Result |
| --- | --- | --- |
| `python -m pytest tests/test_finding_packet.py tests/test_findings_api.py tests/test_article_rights_api.py tests/test_finding_credit.py -q -rs` | 0 | 117 passed, 1 skipped (CRT importer needs `CRT_CHECKOUT`), 0 errors |
| `python -m pytest -q -rs` (full backend) | 0 | 1191 passed, 18 skipped (disposable PostgreSQL not available, `CRT_CHECKOUT` unset, `numpy`/`onnxruntime` not installed; see `-rs`), 0 errors |
| `ruff check app tests` | 0 | all checks passed |
| `ruff format --check app tests` | 0 | formatted |
| `ruff format --check .` | 1 | only `alembic/versions/0032_nts_tables.py` (pre-existing, out of scope) |
| `PYTHONPATH=backend python -B reproduce_revision_gap.py` (repo root) | 0 | both cases `valid: true`, emitted revisions `[1]` and `[1, 2]` |

Against the base `finding_packet.py`/`findings.py`, 25 of the new/updated tests fail (checked by
temporarily restoring the base files), so they exercise the defect.

## New regression tests

Builder (`test_finding_packet.py`): approved rev1; rev1+approved rev2; unreviewed history; 3
revisions with distinct evidence; revision-local assessments and no approval inheritance;
latest rejected/unreviewed/superseded-flag; gap and duplicate; missing/unrepresentable evidence,
judgment, self-review; missing/foreign/mixed snapshot; multi-chain snapshot and passage
conflicts; rights (none, metadata-only, disallowed licences, bogus value, restricted, allowed);
determinism incl. shuffled input and repeated frozen validation; privacy (no notes/raw ids).
API (`test_findings_api.py`): real two-revision export, unreviewed history, unapproved latest,
history across two snapshots, two findings on different snapshots. Rights API: chain under
none -> allowed -> restricted. Credit: companion over a two-revision packet binds revision 2 only.
