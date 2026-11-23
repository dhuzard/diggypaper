"""Synthetic citation graph tests: all provider calls use httpx.MockTransport."""

import httpx
import pytest

from app.citation_graph import (
    CitationBudgetExceeded,
    CitationProviderError,
    discover_citation_paths,
)

RECORDS = {
    "1": {"references": [3, 3], "cited_by": [4]},
    "2": {"references": [3, 4], "cited_by": [4]},
    "3": {"references": [5, 1], "cited_by": [5]},
    "4": {"references": [5, 2], "cited_by": [5]},
    "5": {"references": [3], "cited_by": []},
}


def responder(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "icite.od.nih.gov"
    pmids = request.url.params["pmids"].split(",")
    return httpx.Response(
        200, json={"data": [{"pmid": int(p), **RECORDS[p]} for p in reversed(pmids)]}
    )


async def search(**kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        return await discover_citation_paths(["2", "1", "1"], client, **kwargs)


async def test_depth_one_preserves_direct_seed_overlap():
    result = await search(depth=1, direction="backward")
    assert result.seeds == ("1", "2")
    assert result.counts_by_level == {1: {"3": 2, "4": 1}}
    assert result.ranked() == [("3", 2), ("4", 1)]
    assert result.ranked(known={"3"}) == [("4", 1)]
    assert result.queried_pmids == ("1", "2")


async def test_convergent_two_hop_paths_and_seed_cycles():
    result = await search(depth=2, direction="backward")
    assert result.queried_pmids == ("1", "2", "3", "4")
    assert result.counts_by_level == {1: {"3": 2, "4": 1}, 2: {"5": 3}}
    assert result.path_counts == {"3": 2, "4": 1, "5": 3}
    assert result.ranked() == [("5", 3), ("3", 2), ("4", 1)]
    assert result.ranked(ranking="seed_overlap") == [("3", 2), ("4", 1)]
    assert not {"1", "2"} & set(result.path_counts)
    assert result.scoring_version == "citation-path-count-v1"


async def test_bidirectional_edges_retained_without_double_counting():
    result = await search(depth=2, direction="both")
    edges = [e for e in result.edges if e.source_pmid == "3" and e.target_pmid == "5"]
    assert {e.relation for e in edges} == {"backward", "forward"}
    assert result.counts_by_level[1] == {"3": 2, "4": 2}
    assert result.counts_by_level[2]["5"] == 4
    assert len(result.edges) == len(set(result.edges))


async def test_forward_only_and_excluded_corpus():
    result = await search(depth=1, direction="forward")
    assert result.path_counts == {"4": 2}
    assert result.ranked(known={"4"}) == []
    assert result.ranked(limit=1) == [("4", 2)]


async def test_expansion_budget_prevents_second_hop_calls():
    calls = []

    def spy(request):
        calls.append(request.url.params["pmids"])
        return responder(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(spy)) as client:
        with pytest.raises(CitationBudgetExceeded, match="budget 1"):
            await discover_citation_paths(["1", "2"], client, depth=2, max_first_hop=1)
    assert calls == ["1,2"]


async def test_edge_budget_is_explicit():
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(CitationBudgetExceeded, match="edge budget"):
            await discover_citation_paths(["1", "2"], client, depth=1, max_edges=1)


async def test_provider_omissions_do_not_mean_zero_citations():
    def missing(_request):
        return httpx.Response(200, json={"data": [{"pmid": 1, **RECORDS["1"]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(missing)) as client:
        with pytest.raises(CitationProviderError, match="omitted requested PMIDs"):
            await discover_citation_paths(["1", "2"], client, depth=1)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_provider_http_errors_fail_closed(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status))
    ) as client:
        with pytest.raises(CitationProviderError, match="request failed"):
            await discover_citation_paths(["1"], client, depth=1)


async def test_invalid_provider_identifiers_and_malformed_payload():
    for payload in (
        {"data": [{"pmid": 1, "references": ["not-pmid"]}]},
        {"data": None},
        {"data": [{"pmid": 1, "references": []}]},  # missing cited_by
        {"data": [{"pmid": 1, **RECORDS["1"]}, {"pmid": 1, **RECORDS["1"]}]},
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _, p=payload: httpx.Response(200, json=p))
        ) as client:
            with pytest.raises(CitationProviderError):
                await discover_citation_paths(["1"], client, depth=1)


async def test_invalid_depth_and_seed_identifiers():
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(ValueError):
            await discover_citation_paths(["1"], client, depth=3)
        with pytest.raises(ValueError):
            await discover_citation_paths([], client)
        with pytest.raises(CitationProviderError):
            await discover_citation_paths(["abc"], client)


async def test_explicit_null_adjacency_accepts_provider_record():
    response = {"data": [{"pmid": 1, "references": None, "cited_by": None}]}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        graph = await discover_citation_paths(["1"], client)
    assert graph.path_counts == {} and graph.queried_pmids == ("1",)


async def test_same_graph_in_different_provider_order_has_identical_ranking_and_edges():
    def shuffled(request):
        pmids = request.url.params["pmids"].split(",")
        data = [
            {"pmid": p, **{key: list(reversed(values)) for key, values in RECORDS[p].items()}}
            for p in pmids
        ]
        return httpx.Response(200, json={"data": data})

    expected = await search()
    async with httpx.AsyncClient(transport=httpx.MockTransport(shuffled)) as client:
        actual = await discover_citation_paths(["1", "2"], client)
    assert actual == expected
    assert actual.ranked() == expected.ranked()
