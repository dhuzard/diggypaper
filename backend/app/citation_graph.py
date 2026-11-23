"""Bounded deterministic citation-path discovery using NIH iCite.

Opt-in depth-2 engine for issue #77. This measures graph connectivity, NOT
scientific relevance or evidence quality; depth-1 API behavior is unchanged.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

import httpx

ICITE_URL = "https://icite.od.nih.gov/api/pubs"
SCORING_VERSION = "citation-path-count-v1"
Direction = Literal["backward", "forward", "both"]
Ranking = Literal["path_count", "seed_overlap"]
BATCH = 100
MAX_SEEDS = 2000


class CitationDiscoveryError(RuntimeError):
    """Discovery cannot produce a complete interpretable result."""


class CitationProviderError(CitationDiscoveryError):
    """Provider failed or returned incomplete or malformed data."""


class CitationBudgetExceeded(CitationDiscoveryError):
    """The requested traversal exceeds a hard resource budget."""


@dataclass(frozen=True, order=True)
class CitationEdge:
    source_pmid: str
    target_pmid: str
    relation: Literal["backward", "forward"]


@dataclass(frozen=True)
class CitationPathResult:
    seeds: tuple[str, ...]
    direction: Direction
    depth: int
    queried_pmids: tuple[str, ...]
    edges: tuple[CitationEdge, ...]
    counts_by_level: dict[int, dict[str, int]]
    direct_seed_overlap: dict[str, int]
    provider: str = "NIH iCite"
    scoring_version: str = SCORING_VERSION

    @property
    def path_counts(self) -> dict[str, int]:
        scores: Counter[str] = Counter()
        for level in self.counts_by_level.values():
            scores.update(level)
        return dict(sorted(scores.items()))

    def ranked(
        self,
        *,
        known: set[str] | None = None,
        min_score: int = 1,
        limit: int = 500,
        ranking: Ranking = "path_count",
    ) -> list[tuple[str, int]]:
        """Stable order: descending score then ascending lexical PMID."""
        if min_score < 1 or limit < 1:
            raise ValueError("min_score and limit must be positive")
        if ranking not in ("path_count", "seed_overlap"):
            raise ValueError("unsupported ranking")
        scores = self.path_counts if ranking == "path_count" else self.direct_seed_overlap
        excluded = set(self.seeds) | (known or set())
        return sorted(
            ((p, n) for p, n in scores.items() if p not in excluded and n >= min_score),
            key=lambda item: (-item[1], item[0]),
        )[:limit]


def _pmid(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CitationProviderError("invalid PMID type in citation graph")
    s = str(value).strip()
    if not s.isascii() or not s.isdecimal() or not s.strip("0"):
        raise CitationProviderError("invalid PMID in citation graph")
    return s.lstrip("0")


async def _fetch_edges(
    pmids: tuple[str, ...], http: httpx.AsyncClient, direction: Direction, edge_budget: int
) -> dict[str, set[CitationEdge]]:
    """Strict, batched retrieval; omissions must never masquerade as zero edges."""
    result: dict[str, set[CitationEdge]] = {p: set() for p in pmids}
    remaining = edge_budget
    for i in range(0, len(pmids), BATCH):
        batch = pmids[i : i + BATCH]
        try:
            response = await http.get(ICITE_URL, params={"pmids": ",".join(batch)}, timeout=30.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CitationProviderError("iCite request failed; discovery is incomplete") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise CitationProviderError("malformed iCite response; discovery is incomplete")
        returned: set[str] = set()
        for item in payload["data"]:
            if not isinstance(item, dict):
                raise CitationProviderError("malformed iCite publication")
            source = _pmid(item.get("pmid"))
            if source not in result or source not in batch:
                raise CitationProviderError("iCite returned an unrequested publication")
            if source in returned:
                raise CitationProviderError("iCite returned a duplicate publication")
            returned.add(source)
            for relation, key in (("backward", "references"), ("forward", "cited_by")):
                if direction not in (relation, "both"):
                    continue
                if key not in item:
                    raise CitationProviderError("missing iCite citation list")
                candidates = item[key]
                if candidates is None:  # iCite uses explicit null for unobserved adjacency.
                    continue
                if not isinstance(candidates, list):
                    raise CitationProviderError("invalid iCite citation list")
                for candidate in candidates:
                    target = _pmid(candidate)
                    edge = CitationEdge(source, target, relation)
                    if target != source and edge not in result[source]:
                        if remaining == 0:
                            raise CitationBudgetExceeded("citation edge budget exceeded")
                        result[source].add(edge)
                        remaining -= 1
        if returned != set(batch):
            raise CitationProviderError("iCite omitted requested PMIDs; discovery is incomplete")
    return result


async def discover_citation_paths(
    seeds: list[str],
    http: httpx.AsyncClient,
    *,
    depth: Literal[1, 2] = 2,
    direction: Direction = "both",
    max_first_hop: int = 500,
    max_edges: int = 100_000,
) -> CitationPathResult:
    """Count distinct seed->candidate and seed->intermediate->candidate paths.

    Both direction labels are retained as edges but a source/target pair counts
    only once per hop. Original seeds are never output or intermediate nodes.
    An incomplete request raises instead of returning silently truncated data.
    """
    if depth not in (1, 2) or direction not in ("backward", "forward", "both"):
        raise ValueError("unsupported depth or direction")
    if max_first_hop < 1 or max_edges < 1:
        raise ValueError("resource budgets must be positive")
    original = tuple(sorted({_pmid(s) for s in seeds}))
    if not original:
        raise ValueError("at least one seed PMID is required")
    if len(original) > MAX_SEEDS:
        raise CitationBudgetExceeded("too many seed PMIDs")

    first_edges = await _fetch_edges(original, http, direction, max_edges)
    seed_set = set(original)
    neighbors = {
        source: {edge.target_pmid for edge in edges if edge.target_pmid not in seed_set}
        for source, edges in first_edges.items()
    }
    first_hop = set().union(*neighbors.values())
    edge_count = sum(len(edges) for edges in first_edges.values())
    if depth == 2 and len(first_hop) > max_first_hop:
        raise CitationBudgetExceeded(
            f"depth-2 expansion would query {len(first_hop)} first-hop papers "
            f"(budget {max_first_hop}); choose fewer seeds"
        )

    by_source = dict(first_edges)
    if depth == 2 and first_hop:
        second_edges = await _fetch_edges(
            tuple(sorted(first_hop)), http, direction, max_edges - edge_count
        )
        by_source.update(second_edges)

    level1: Counter[str] = Counter()
    level2: Counter[str] = Counter()
    for adjacent in neighbors.values():
        level1.update(adjacent)
    if depth == 2:
        for intermediate, seed_count in level1.items():
            # Each distinct seed reaching the intermediary contributes one path per target.
            for target in {edge.target_pmid for edge in by_source[intermediate]} - seed_set:
                level2[target] += seed_count

    counts = {1: dict(sorted(level1.items()))}
    if depth == 2:
        counts[2] = dict(sorted(level2.items()))
    all_edges = tuple(sorted({e for edges in by_source.values() for e in edges}))
    return CitationPathResult(
        seeds=original,
        direction=direction,
        depth=depth,
        queried_pmids=tuple(sorted(by_source)),
        edges=all_edges,
        counts_by_level=counts,
        direct_seed_overlap=counts[1].copy(),
    )
