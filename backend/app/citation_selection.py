"""Apply exact bibliographic exclusions after scoring and before the result cap."""

from dataclasses import dataclass
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.citation_graph import (
    BATCH,
    ICITE_URL,
    CitationBudgetExceeded,
    CitationPathResult,
    CitationProviderError,
    _pmid,
)
from app.db.models import Article
from app.refimport import clean_doi

_EXTRA_DOI_LOOKUP_BUDGET = 1000


@dataclass(frozen=True)
class RankedCitationSelection:
    ranked: list[tuple[str, int]]
    doi_metadata: dict


async def _fetch_dois(pmids: list[str], http: httpx.AsyncClient) -> dict[str, str | None]:
    try:
        response = await http.get(
            ICITE_URL, params={"pmids": ",".join(pmids), "fl": "pmid,doi"}, timeout=30.0
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CitationProviderError("iCite DOI lookup failed; discovery is incomplete") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise CitationProviderError("malformed iCite DOI response")

    requested = set(pmids)
    found: dict[str, str | None] = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            raise CitationProviderError("malformed iCite DOI record")
        pmid = _pmid(item.get("pmid"))
        doi = item.get("doi")
        if pmid not in requested or pmid in found or (doi is not None and not isinstance(doi, str)):
            raise CitationProviderError("invalid iCite DOI record")
        found[pmid] = clean_doi(doi)
    return found


async def select_ranked(
    graph: CitationPathResult,
    known_pmids: set[str],
    db: AsyncSession,
    project_id: UUID,
    http: httpx.AsyncClient,
    *,
    min_score: int,
    limit: int,
) -> RankedCitationSelection:
    """Keep the highest-ranked new records, filling slots after exact DOI exclusions."""
    eligible = graph.ranked(
        known=known_pmids, min_score=min_score, limit=max(1, len(graph.path_counts))
    )
    doi_rows = await db.scalars(
        select(Article.doi).where(Article.project_id == project_id, Article.doi.is_not(None))
    )
    project_dois = {doi for value in doi_rows if (doi := clean_doi(value))}
    kept_dois: dict[str, str] = {}
    ranked: list[tuple[str, int]] = []
    exclusions: list[dict[str, str]] = []
    examined = 0
    without_doi = 0
    metadata_unavailable = 0
    lookup_limit = limit + _EXTRA_DOI_LOOKUP_BUDGET
    to_check = min(len(eligible), lookup_limit)

    for start in range(0, to_check, BATCH):
        batch = eligible[start : min(start + BATCH, to_check)]
        dois = await _fetch_dois([pmid for pmid, _ in batch], http)
        for pmid, score in batch:
            examined += 1
            doi = dois.get(pmid)
            if doi is None:
                if pmid in dois:
                    without_doi += 1
                else:
                    metadata_unavailable += 1
            elif doi in project_dois:
                exclusions.append({"pmid": pmid, "reason": "doi_in_project"})
                continue
            elif doi in kept_dois:
                exclusions.append(
                    {"pmid": pmid, "reason": "doi_already_ranked", "kept_pmid": kept_dois[doi]}
                )
                continue
            else:
                kept_dois[doi] = pmid

            ranked.append((pmid, score))
            if len(ranked) == limit:
                break
        if len(ranked) == limit:
            break

    if len(ranked) < limit and len(eligible) > to_check:
        raise CitationBudgetExceeded("DOI lookup budget exceeded before the result cap was filled")

    return RankedCitationSelection(
        ranked=ranked,
        doi_metadata={
            "policy": "exact-normalized-doi-v1",
            "ranked_candidates_examined": examined,
            "candidates_without_doi": without_doi,
            "doi_metadata_unavailable": metadata_unavailable,
            "doi_exclusions": exclusions,
        },
    )
