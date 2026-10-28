# SEIP, findings, NTS and connector documentation index

The scientific-evidence invariants are in [`AGENTS.md`](../../AGENTS.md); every document listed here is subordinate to them.
A status in these tables is an engineering statement (which code exists on the fork, verified against `backend/app` and `frontend/src`), not a scientific validation: an implemented quotation check or support assessment never establishes that an answer is supported, and no document here reports a pilot result.
Statuses: `implemented` (the slice is wired end to end), `partial` (what is missing is named), `retired` (kept as the record of historical rows), `plan/record` (dated audits, plans and protocols; never updated in place).
Fork migrations are `f0022`…`f0036` in `backend/alembic/versions/`; upstream's `0022`…`0028` share the parent `0021` and the empty revision `f0035` joins the two chains (`f0036`, finding withdrawals, follows it). Last verified 2026-10-27 at commit `3e13113`.

## Contract and design documents

| Document | Status | Implemented by | Notes |
|---|---|---|---|
| [seip-provenance-contract-v0.1.md](../design/seip-provenance-contract-v0.1.md) | implemented | `app/provenance.py`, `provenance_capture.py`, `provenance_chat.py`, `provenance_render.py`, `review_source.py`, `api/v1/annotations.py`; `f0022`, `f0024` | Annotation drift field is `current_display_text_drift` (`legacy_unversioned` / `view_bound`), not the contract's `current_source_drift`. Scorer call kinds are retired (next row). |
| [scorer-provenance-v1.md](../design/scorer-provenance-v1.md) | retired | `app/provenance.py` still reads historical `systemone_*` / `onnx` traces stored by `f0022` | Retired 2026-10-27 with the decision models (upstream `0023`); `provenance_scorer.py` is deleted, no new scorer call is captured. |
| [quote-matcher-v1.md](../design/quote-matcher-v1.md) | implemented | `app/quote_matcher.py`, `scripts/benchmark_quote_matcher.py`, `tests/unit/test_quote_matcher.py` | Pure module, algorithm `article-input-quote-v1.2`. No frontend UTF-16 offset adapter (F-9). |
| [quote-check-persistence-v1.md](../design/quote-check-persistence-v1.md) | partial | `app/quote_checks.py`, `tasks/inference.py`, `api/v1/annotations.py` (`quote_checks`, `quotation_producer`), `QuoteOccurrencePanel.tsx`; `f0023` | Missing: benchmark predictions never checked. |
| [evidence-links-v1.md](../design/evidence-links-v1.md) | implemented | `app/evidence_links.py`, [`evidence-envelope-v1.schema.json`](../schemas/evidence-envelope-v1.schema.json), `tests/unit/test_evidence_links.py` | Pure contract; consumed by the graph, assessments, findings, NTS and the crate. |
| [prediction-evidence-graph-v1.md](../design/prediction-evidence-graph-v1.md) | partial | `app/evidence_prediction.py`, `tasks/inference.py`, `app/schema.py` (prompt asks for `evidence_links`); `f0025` | Missing: benchmark and annotation graphs. |
| [human-support-assessments-v1.md](../design/human-support-assessments-v1.md) | implemented | `api/v1/support_assessments.py`, `SupportAssessmentPanel.tsx`; `f0027` | Append-only, project-scoped pseudonyms; no adjudication or aggregation, by design. |
| [gold-integrity-v1.md](../design/gold-integrity-v1.md) | implemented | `api/v1/gold.py` (`GoldRevision`, three staleness dimensions), gold page `stale` badge; `f0028` | No governed phase unlock or project-wide embargo (F-7). |
| [evidence-crate-v1.md](../design/evidence-crate-v1.md) | implemented | `app/evidence_crate.py`, `app/crate.py`, `api/v1/export.py`; `f0026` | `evidence/portable-replay.json` and `prediction-history.json` in the RO-Crate; blind crates carry none. Offline check: `python -m app.evidence_crate`. |
| [seip-portable-replay.md](../seip-portable-replay.md) | implemented | `app/portable_replay.py`, [schema](../schemas/seip-portable-replay-v1.schema.json), [example](../examples/seip-portable-replay-v1.json) | Offline validator `python -m app.portable_replay`; emission verification is quote-list only. |
| [finding-curation-v1.md](../design/finding-curation-v1.md) | partial | `api/v1/findings.py`, `app/finding_packet.py`, `app/finding_schema.py`, `app/rights.py`; `f0029`, `f0030` | Missing: general findings UI (review form exists only in the NTS queue), LEAD release step, split/merge helpers. |
| [nts-publication-traceability-v1.md](../design/nts-publication-traceability-v1.md) | implemented | `backend/app/nts/`, `api/v1/nts.py`, `tasks/nts.py`, `NtsWorkflow.tsx`, `NtsReviewQueue.tsx`; `f0031`–`f0033` | Optional research module; file-based PoC output committed under `runs/`. Exact-ID ALURES join, no ratio. |

## Protocols and schemas

| Document | Status | Implemented by | Notes |
|---|---|---|---|
| [seip-qualification-v0.1.md](../protocols/seip-qualification-v0.1.md) | plan/record | — | Draft protocol for the 30-paper pilot: no dataset selected, codebook approval pending, no result. |
| [seip-scoring-contract-v0.1.md](../protocols/seip-scoring-contract-v0.1.md) | partial | `app/seip_scoring.py`, `tests/test_seip_scoring.py` | Missing: CLI, ledger adapter, agreement/kappa (F-6, F-8). |
| [seip-pilot-manifest-v1.md](../protocols/seip-pilot-manifest-v1.md) | implemented | `app/seip_pilot_manifest.py`, [schema](../schemas/seip-pilot-manifest-v1.schema.json), [draft example](../examples/seip-pilot-manifest-draft-v1.json) | Offline validator; `scorer_manifest` never flags example papers (F-22). |
| [seip-independent-reference-ledger-v1.md](../protocols/seip-independent-reference-ledger-v1.md) | partial | `app/seip_reference_ledger.py`, [schema](../schemas/seip-independent-reference-v1.schema.json), [draft example](../examples/seip-independent-reference-draft-v1.json) | Missing: Stage B existence/attribution fields, conflict reassignment (F-6, F-21). |
| [evidence-envelope-v1.schema.json](../schemas/evidence-envelope-v1.schema.json) | implemented | `app/evidence_links.py` (`EvidenceEnvelopeV1.model_json_schema()`) | Shape check only; reference and revision checks need `validate_envelope`. |
| [seip-portable-replay-v1.schema.json](../schemas/seip-portable-replay-v1.schema.json) | implemented | `app/portable_replay.py` | Shape check only; semantic replay is the validator's. |
| [seip-pilot-manifest-v1.schema.json](../schemas/seip-pilot-manifest-v1.schema.json) | implemented | `app/seip_pilot_manifest.py` | Draft example has no papers or results. |
| [seip-independent-reference-v1.schema.json](../schemas/seip-independent-reference-v1.schema.json) | implemented | `app/seip_reference_ledger.py` | Draft example has no references. |

## Audits and plans (dated records, not updated in place)

| Document | Status | Implemented by | Notes |
|---|---|---|---|
| [2026-10-scientific-evidence-integrity.md](../audits/2026-10-scientific-evidence-integrity.md) | plan/record | — | Baseline audit at `9e5859c`, CONDITIONAL GO; defines the SEIP workstream (epic #1). |
| [2026-10-17-seip-merge-audit.md](../audits/2026-10-17-seip-merge-audit.md) | plan/record | — | Findings F-0…F-22. Closed: F-0 to F-5, F-12, F-20 (this index). Open: F-6 to F-11, F-13 to F-19, F-21, F-22. |
| [2026-10-history-export-validation.md](../audits/2026-10-history-export-validation.md) | plan/record | — | Validation report of the finding revision-chain export (`app/finding_packet.py`). |
| [2026-10-27-fork-global-audit.md](../audits/2026-10-27-fork-global-audit.md) | plan/record | — | Fork versus upstream; proposes U1–U6 and I1–I8. This index is I6. |
| [2026-10-seip-autonomous-implementation.md](../plans/2026-10-seip-autonomous-implementation.md) | plan/record | — | Wave plan for epic #1; waves 0–5 landed, wave 6 partial (scoring row above). |
| [2026-10-seip-poc-test-plan.md](../plans/2026-10-seip-poc-test-plan.md) | plan/record | — | Phases 0–4 of the pilot; no Phase 1 evidence table archived yet. |

## Contracts (frozen schemas under `docs/contracts/`)

| Document | Status | Implemented by | Notes |
|---|---|---|---|
| [source-evidence-packet/0.1.0](../contracts/source-evidence-packet/v0.1.0/README.md) | implemented (frozen) | producer `app/finding_packet.py` via `GET /projects/{id}/findings/export`; validator `validator/sep_validate.py` | Additive 0.1.x only. Quotes withheld unless an `allowed` rights declaration; `GIT_COMMIT` required. |
| [finding-credit-companion/0.1.0](../contracts/finding-credit-companion/v0.1.0/README.md) | partial (draft) | builder `app/finding_credit.py`; validator `validator/fcc_validate.py` | Missing: not wired into any export route (waits on ORAtlas #202). |
| [research-project-descriptor/0.1.0](../contracts/research-project-descriptor/v0.1.0/README.md) | partial (draft) | builder `app/project_descriptor.py`, issued by `api/v1/connector.py` under a live grant (`app/connector_grants.py`, `api/v1/connector_grants.py`); `f0034` | Missing: artifact references, authorized export. |

## Where the code lives

| Area | Backend modules | Migrations | Tests |
|---|---|---|---|
| Source and invocation capture | `provenance*.py`, `review_source.py`, `pseudonym.py` | `f0022`, `f0024` | `test_provenance*.py`, `test_review_binding_postgres.py`, `test_reviewer_pseudonym_policy.py` |
| Quotation checks and evidence graph | `quote_matcher.py`, `quote_checks.py`, `evidence_links.py`, `evidence_prediction.py` | `f0023`, `f0025`, `f0026` | `unit/test_quote_matcher.py`, `unit/test_evidence_links.py`, `test_inference_task.py` |
| Human assessment and gold | `api/v1/support_assessments.py`, `api/v1/gold.py` | `f0027`, `f0028` | `test_support_assessments_api.py`, `test_gold_api.py` |
| Export and replay | `evidence_crate.py`, `crate.py`, `portable_replay.py` | — | `test_crate_human_evidence.py`, `test_team_export.py` |
| Qualification pilot | `seip_scoring.py`, `seip_pilot_manifest.py`, `seip_reference_ledger.py` | — | `test_seip_scoring.py` |
| Finding curation | `api/v1/findings.py`, `finding_packet.py`, `finding_schema.py`, `rights.py`, `finding_credit.py` | `f0029`, `f0030`, `f0036` | `test_findings_api.py`, `test_finding_withdrawals.py`, `test_finding_packet.py`, `test_article_rights_api.py`, `test_finding_curation_postgres.py`, `test_source_evidence_packet.py`, `test_finding_credit*.py` |
| NTS | `nts/`, `api/v1/nts.py`, `tasks/nts.py` | `f0031`–`f0033` | `test_nts_*.py` |
| ORAtlas connector | `project_descriptor.py`, `connector_grants.py`, `connector_policy.py`, `api/v1/connector*.py` | `f0034` | `test_connector_*.py`, `test_research_project_descriptor.py` |

Frontend: `SupportAssessmentPanel.tsx`, `QuoteOccurrencePanel.tsx` and `EvidenceHighlighter.tsx` are mounted on the annotate page; `NtsWorkflow.tsx` and `NtsReviewQueue.tsx` on the project NTS tab; the gold page shows revision staleness. There is no findings page outside the NTS queue and no UTF-16 offset helper.
