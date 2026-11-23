# Citation discovery v1 — issue #77

Depth 2 is experimental and opt-in through FastAPI and the import page. Depth 1 remains the default single-hop iCite implementation. Each accepted depth-2 preview binds the ranked import queue and provides a downloadable search log. Scientific retrieval performance requires an independent benchmark.

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
- The graph result contains queried PMIDs, provider, scoring version, per-depth scores and observed edges. The search log stores a digest of the sorted edges and the ordered selection. Full edge and provider response archival is future work.

## Opt-in integration and limitations

- `POST /api/v1/projects/{project_id}/citations` accepts optional `depth: 1|2` (default: `1`). Depth 1 follows the existing iCite code path, unchanged. Depth 2 requests the new strict graph engine.
- Dry-run for depth 2 adds `by_score`, `depth`, `ranking`, `scoring_version`, `queried_pmids`, and `edges`. The existing `by_overlap` always means **direct seed overlap**, never path counts. `min_overlap` means a direct-seed threshold at depth 1 and a path-count threshold at depth 2 (legacy field name).
- A two-level dry-run rejects expansion past 500 first-hop PMIDs or 100,000 edges with HTTP 422. Traversal omissions and provider errors return 502. DOI metadata is fetched in ranked order, after the existing PMID exclusion. Exact normalized DOI matches against the project or a higher-ranked record are excluded before filling the cap. An unavailable DOI remains eligible and is counted separately from an explicit record with no usable DOI. Reaching the additional lookup budget before filling the cap returns 422.
- The preview includes `ranked_count` after DOI exclusions and issues a short-lived, single-use `preview_token`. The score histograms still describe the PMID candidate pool before DOI exclusions. The depth-2 submit supplies the token with the same project, user and parameters. A stale token or changed inputs require a fresh preview. Redis retains the snapshot across API instances. Celery uses the frozen ranking, then resolves and imports those records. The import outcome can contain duplicates or unavailable PubMed records, which can reduce the number actually added.
- New articles keep `origin_seeds` as the count of directly connected seeds, including zero for an indirect-only candidate. The import tracks path scores and scoring version independently in the created search's `parent_spec.citations`.
- Project leads and administrators can download `GET /api/v1/projects/{project_id}/jobs/{job_id}/citation-run.json` from the import history. The versioned log records the captured seed PMIDs, direction, depth, provider, date, scoring version, exclusion policy and ordered queue, alongside graph counts and the PMIDs actually imported. The general job list exposes only a download flag to authorized users. Older jobs have no captured run to download.
- Receiving all requested traversal records establishes response coverage for that request. The underlying bibliographic coverage and retrieval benefit are measured separately.

## Follow-on work

1. Benchmark PubMed, current depth-1 iCite and depth-2 path score. Evaluate recall@K, precision@K, unique eligible studies, workload, coverage errors, resource consumption and paired review-level uncertainty. Draw seeds from the frozen PubMed baseline.
2. Evaluate archiving complete graph edges and provider data for offline graph replay, and add retrieval-run export to applicable RO-Crates. The job log covers replay of the submitted queue; graph replay requires the underlying provider data.

Citation connectivity is not eligibility, methodological quality or evidence validity. The BibliZap evaluation is **not** evidence of performance for this distinct NIH iCite implementation.

BibliZap reference: Bentegeac et al. (2026), *Research Synthesis Methods*, https://doi.org/10.1017/rsm.2026.10079 .
