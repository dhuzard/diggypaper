# Citation discovery v1 — issue #77

Status: **depth-2 engine wired to FastAPI, Celery import and UI as an explicit opt-in**. Depth 1 retains the existing single-hop iCite implementation and remains the default. Full graph-run persistence, per-edge evidence and export are still pending. Synthetic tests are engineering checks, not validation of scientific retrieval performance.

## Audited baseline

The existing `backend/app/citations.py` fetches reference/citer neighbors for the selected seed PMIDs, and ranks candidates by the number of distinct directly connected seeds. The API supports direction, overlap threshold, candidate cap and dry-run; imported items retain source origin and direct seed overlap. It does not currently count multi-level citation paths. iCite is PMID-based, so DOI-only studies and coverage gaps remain problematic.

## New opt-in engine

`backend/app/citation_graph.py::discover_citation_paths` supports depth 1 or 2 and backward, forward or both directions.

- At depth 1 a candidate receives one occurrence for each **distinct original seed** that directly reaches it.
- At depth 2 it receives one occurrence for each **distinct (seed, intermediary, candidate)** route. Sum both levels for `path_count` (version `citation-path-count-v1`), or use direct `seed_overlap` as a separate ranking mode.
- Duplicate edges and dual-direction appearances count once per source/target pair per hop; both relation labels remain in the returned evidence edges.
- Original seeds are excluded as intermediaries and output candidates, and self-links are ignored. Stable tie-break: ascending **lexical PMID**, not Lens ID.
- Hard bounds for first-hop expansion and edge count; budget exhaustion raises an error rather than silently truncating.
- iCite HTTP failures, malformed payloads and missing requested PMID records fail closed. This does not imply complete coverage for successfully returned records.
- Results retain queried PMIDs, provider, scoring version, per-depth scores and observed edges; **they are not yet persisted**.

## Opt-in integration and limitations

- `POST /api/v1/projects/{project_id}/citations` accepts optional `depth: 1|2` (default: `1`). Depth 1 follows the existing iCite code path, unchanged. Depth 2 requests the new strict graph engine.
- Dry-run for depth 2 adds `by_score`, `depth`, `ranking`, `scoring_version`, `queried_pmids`, and `edges`. The existing `by_overlap` always means **direct seed overlap**, never path counts. `min_overlap` means a direct-seed threshold at depth 1 and a path-count threshold at depth 2 (legacy field name).
- A two-level dry-run rejects expansion exceeding 500 first-hop PMIDs or 100,000 edges with HTTP 422. Missing provider records and HTTP failures return 502, never an apparently complete zero-result graph. Imports have the same hard budgets, and a failing Celery job does not add a partial corpus.
- For a depth-2 import, new article `origin_seeds` is **direct** overlap, possibly zero for an indirect-only candidate; selected `path_count` scores and algorithm/version are recorded in the created search's project-scoped `parent_spec.citations` and summary counts in the import job. Raw graph edges and complete request/response manifests are **not persisted**.
- The UI enables depth 2 only when the researcher selects it, makes the changed scoring semantics explicit, and requires a fresh dry-run before enabling import. Estimate and import are separate iCite lookups: preview results may differ from eventual import if the provider changes between calls.
- Provider completeness refers only to receiving all **requested** iCite records. Bibliographic coverage remains unknown for papers outside iCite, and the method has not been evaluated against an independent systematic-review reference set.

## Follow-on work

1. Evaluate the connected depth-2 workflow against a live permitted iCite corpus and finalize operational error/progress feedback; configurable ranking/budgets remain future work.
2. Store versioned, project-scoped retrieval-run provenance (seed set, time, provider, parameters, graph edges, scores, coverage and failures) and append to exports without changing frozen SEIP contracts.
3. Benchmark PubMed, current depth-1 iCite, depth-2 path score and optional same-pool semantic/hybrid reranking. Evaluate recall@K, precision@K, unique relevant studies, workload, coverage errors, resource consumption and paired review-level uncertainty; prevent seed/target leakage.

Citation connectivity is not eligibility, methodological quality or evidence validity. The BibliZap evaluation is **not** evidence of performance for this distinct NIH iCite implementation.

BibliZap reference: Bentegeac et al. (2026), *Research Synthesis Methods*, https://doi.org/10.1017/rsm.2026.10079 .
