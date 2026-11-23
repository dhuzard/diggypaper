import httpx
import pytest

from app import citation_selection
from app.citation_graph import CitationBudgetExceeded, CitationPathResult, CitationProviderError
from app.db.models import Article, Project


def _graph(scores):
    return CitationPathResult(
        seeds=("1",),
        direction="both",
        depth=2,
        queried_pmids=("1",),
        edges=(),
        counts_by_level={2: scores},
        direct_seed_overlap={},
    )


async def test_ranked_selection_skips_duplicate_dois_before_cap(
    session, researcher_user, monkeypatch
):
    project = Project(title="Review", created_by=researcher_user.id)
    other = Project(title="Other review", created_by=researcher_user.id)
    session.add_all([project, other])
    await session.flush()
    session.add_all(
        [
            Article(project_id=project.id, title="DOI-only record", doi="10.1111/known"),
            Article(project_id=other.id, title="Other project's record", doi="10.1111/other"),
        ]
    )
    await session.commit()
    monkeypatch.setattr(citation_selection, "BATCH", 3)
    requests: list[list[str]] = []
    metadata = {
        "2": "https://doi.org/10.1111/KNOWN",
        "3": "10.1111/Candidate",
        "4": "10.1111/candidate",
        "5": None,
        "7": "10.1111/other",
    }

    def handler(request):
        assert request.url.params["fl"] == "pmid,doi"
        ids = request.url.params["pmids"].split(",")
        requests.append(ids)
        return httpx.Response(
            200, json={"data": [{"pmid": int(p), "doi": metadata[p]} for p in ids if p in metadata]}
        )

    scores = {str(pmid): 20 - pmid for pmid in range(2, 9)} | {"9": 30}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        selection = await citation_selection.select_ranked(
            _graph(scores), {"9"}, session, project.id, client, min_score=1, limit=4
        )
    assert requests == [["2", "3", "4"], ["5", "6", "7"]]
    assert selection.ranked == [("3", 17), ("5", 15), ("6", 14), ("7", 13)]
    assert selection.doi_metadata == {
        "policy": "exact-normalized-doi-v1",
        "ranked_candidates_examined": 6,
        "candidates_without_doi": 1,
        "doi_metadata_unavailable": 1,
        "doi_exclusions": [
            {"pmid": "2", "reason": "doi_in_project"},
            {"pmid": "4", "reason": "doi_already_ranked", "kept_pmid": "3"},
        ],
    }


async def test_ranked_selection_lookup_budget_cannot_return_partial_results(
    session, researcher_user, monkeypatch
):
    project = Project(title="Review", created_by=researcher_user.id)
    session.add(project)
    await session.flush()
    session.add(Article(project_id=project.id, title="Already in corpus", doi="10.1111/known"))
    await session.commit()
    monkeypatch.setattr(citation_selection, "_EXTRA_DOI_LOOKUP_BUDGET", 1)

    def handler(request):
        ids = request.url.params["pmids"].split(",")
        assert ids == ["2", "3"]
        return httpx.Response(
            200, json={"data": [{"pmid": p, "doi": "10.1111/known"} for p in ids]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CitationBudgetExceeded, match="DOI lookup budget"):
            await citation_selection.select_ranked(
                _graph({"2": 1, "3": 1, "4": 1}),
                set(),
                session,
                project.id,
                client,
                min_score=1,
                limit=1,
            )


@pytest.mark.parametrize("status", [429, 502])
async def test_doi_request_failure_is_explicit(status, session, researcher_user):
    project = Project(title="Review", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    transport = httpx.MockTransport(lambda _: httpx.Response(status))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(CitationProviderError, match="DOI lookup failed"):
            await citation_selection.select_ranked(
                _graph({"2": 1}), set(), session, project.id, client, min_score=1, limit=1
            )
