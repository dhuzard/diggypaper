# Citation discovery v1 — issue #77

Status: **isolated core implemented; API, Celery, UI, persistence and exports not yet connected**. The existing single-hop iCite implementation remains the production default. These are engineering tests, not a validation of scientific retrieval performance.

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

## Follow-on work

1. Expose explicit depth, ranking mode, budget and error semantics in the FastAPI/Celery workflow, with a backward-compatible default and UI preview.
2. Store versioned, project-scoped retrieval-run provenance (seed set, time, provider, parameters, graph edges, scores, coverage and failures) and append to exports without changing frozen SEIP contracts.
3. Benchmark PubMed, current depth-1 iCite, depth-2 path score and optional same-pool semantic/hybrid reranking. Evaluate recall@K, precision@K, unique relevant studies, workload, coverage errors, resource consumption and paired review-level uncertainty; prevent seed/target leakage.

Citation connectivity is not eligibility, methodological quality or evidence validity. The BibliZap evaluation is **not** evidence of performance for this distinct NIH iCite implementation.

BibliZap reference: Bentegeac et al. (2026), *Research Synthesis Methods*, https://doi.org/10.1017/rsm.2026.10079 .
