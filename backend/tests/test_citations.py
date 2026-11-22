"""Chaînage de citations : liens (iCite mocké), classement, endpoint, import et recherche créée."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_tasks_import import _efetch_xml, _make_project

from app import citations
from app.citation_graph import CitationBudgetExceeded, CitationPathResult, CitationProviderError
from app.db.models import Article, Search, SearchArticle
from app.tasks import _import_pubmed

ICITE = {
    "1": {"references": [10, 11], "cited_by": [20]},
    "2": {"references": [10], "cited_by": [20, 21]},
    "3": {"references": [12], "cited_by": [1]},  # 1 est une graine : ignoré
    # Second-hop references used only in the opt-in integration test.
    "10": {"references": [30], "cited_by": []},
    "11": {"references": [30], "cited_by": []},
    "20": {"references": [30], "cited_by": []},
    "21": {"references": [30], "cited_by": []},
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "icite.od.nih.gov":
        assert "legacy" not in request.url.params  # sinon les champs changent de nom (vu en réel)
        pmids = request.url.params["pmids"].split(",")
        data = [{"pmid": int(p), **ICITE[p]} for p in pmids if p in ICITE]
        return httpx.Response(200, json={"data": data})
    if request.url.path.endswith("efetch.fcgi"):
        ids = request.url.params["id"].split(",")
        return httpx.Response(200, text=_efetch_xml(ids, with_pmcid=False))
    raise AssertionError(f"appel inattendu : {request.url}")


async def test_links_rank_and_histogram():
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        both = await citations.fetch_links(["1", "2", "3"], http)
        fwd = await citations.fetch_links(["1", "2", "3"], http, "forward")
        bwd = await citations.fetch_links(["1", "2", "3"], http, "backward")
    assert both["10"] == {"1", "2"} and both["20"] == {"1", "2"} and "1" not in both
    assert set(fwd) == {"20", "21"} and set(bwd) == {"10", "11", "12"}
    assert citations.rank(both, {"12"}, 1, 10) == [("10", 2), ("20", 2), ("11", 1), ("21", 1)]
    assert citations.rank(both, set(), 2, 10) == [("10", 2), ("20", 2)]
    assert citations.rank(both, set(), 1, 1) == [("10", 2)]  # plafond
    assert citations.overlap_histogram(both, {"10"}) == {"1": 4, "2": 1, "3": 0, "5": 0}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _noop(_delay: float) -> None:
        return None

    monkeypatch.setattr("app.pubmed._sleep", _noop)


async def test_import_citations_adds_candidates_and_creates_search(engine):
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        s.add_all(
            [
                Article(project_id=project.id, pmid="1", title="seed 1"),
                Article(project_id=project.id, pmid="2", title="seed 2"),
                Article(project_id=project.id, pmid="10", title="déjà là"),
            ]
        )
        await s.commit()
    opts = {
        "kind": "citations",
        "citations": {
            "seeds": ["1", "2"],
            "direction": "both",
            "min_overlap": 1,
            "max_candidates": 3,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(
            str(project.id), "citations", session_factory=factory, http=http, options=opts
        )
    # candidats : 11 (1 graine), 20 (2), 21 (1) hors corpus ; 10 est déjà dans le corpus
    assert result["imported"] == 3 and result["candidates"] == 4 and result["search_id"]
    async with factory() as s:
        new = (await s.execute(select(Article).where(Article.origin == "citation"))).scalars().all()
        by_pmid = {a.pmid: a for a in new}
        assert set(by_pmid) == {"20", "11", "21"} and by_pmid["20"].origin_seeds == 2
        search = await s.get(Search, __import__("uuid").UUID(result["search_id"]))
        lot = (
            (
                await s.execute(
                    select(SearchArticle.article_id).where(SearchArticle.search_id == search.id)
                )
            )
            .scalars()
            .all()
        )
        assert set(lot) == {a.id for a in new}
        assert search.parent_spec["citations"]["seeds"] == 2 and not search.lot_all


async def test_import_citations_depth_two_uses_path_rank_not_seed_overlap(engine):
    project = await _make_project(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        s.add_all(
            [
                Article(project_id=project.id, pmid="1", title="seed 1"),
                Article(project_id=project.id, pmid="2", title="seed 2"),
            ]
        )
        await s.commit()
    opts = {
        "kind": "citations",
        "citations": {
            "seeds": ["1", "2"],
            "direction": "both",
            "depth": 2,
            "min_overlap": 3,
            "max_candidates": 1,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await _import_pubmed(
            str(project.id), "citations", session_factory=factory, http=http, options=opts
        )
    assert result["imported"] == 1
    assert result["candidates"] == 5  # 4 direct neighbors and PMID 30, outside corpus
    async with factory() as s:
        new = (await s.execute(select(Article).where(Article.origin == "citation"))).scalars().all()
        assert len(new) == 1 and new[0].pmid == "30"
        assert new[0].origin_seeds == 0  # indirect-only paper, NOT the path score
        search = await s.get(Search, __import__("uuid").UUID(result["search_id"]))
        spec = search.parent_spec["citations"]
        assert spec["depth"] == 2
        assert spec["ranking"] == "path_count"
        assert spec["scoring_version"] == "citation-path-count-v1"
        assert spec["ranked_scores"] == {"30": 6}


async def test_citations_endpoint(client, researcher_user, do_login, session, monkeypatch):
    from unittest.mock import MagicMock

    import app.tasks as tasks
    from app.api.v1 import citations as api

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = (await client.post("/api/v1/projects", json={"title": "P"})).json()["id"]
    import uuid

    session.add_all(
        [Article(project_id=uuid.UUID(pid), pmid=p, title=p) for p in ("1", "2", "99")]
        + [Article(project_id=uuid.UUID(pid), title="sans pmid")]
    )
    from app.db.models import Project, ProjectStatus

    (await session.get(Project, uuid.UUID(pid))).status = ProjectStatus.FROZEN
    await session.commit()
    url = f"/api/v1/projects/{pid}/citations"

    async def fake_links(seeds, http, direction="both", progress=None):
        assert sorted(seeds) == ["1", "2", "99"] and direction == "both"
        return {"10": {"1", "2"}, "11": {"1"}, "99": {"1"}}

    monkeypatch.setattr(api.citations, "fetch_links", fake_links)
    dry = await client.post(url, json={"dry_run": True})
    assert dry.status_code == 202, dry.text
    d = dry.json()
    assert (d["seeds"], d["selected"], d["candidates"]) == (3, 4, 2)
    assert d["by_overlap"] == {"1": 2, "2": 1, "3": 0, "5": 0}
    delay = MagicMock(return_value=MagicMock(id="job-1", state="PENDING"))
    monkeypatch.setattr(tasks.import_pubmed_task, "delay", delay)
    r = await client.post(
        url, json={"direction": "forward", "min_overlap": 2, "max_candidates": 50}
    )
    assert r.status_code == 202 and r.json()["job_id"] == "job-1"
    opts = delay.call_args.args[3]
    assert opts["kind"] == "citations" and opts["citations"]["min_overlap"] == 2
    assert sorted(opts["citations"]["seeds"]) == ["1", "2", "99"]
    assert opts["citations"]["depth"] == 1  # legacy is still the default
    assert (await client.post(url, json={"depth": 3})).status_code == 422

    async def fake_graph(seeds, http, *, depth, direction):
        assert sorted(seeds) == ["1", "2", "99"]
        assert depth == 2 and direction == "both"
        return CitationPathResult(
            seeds=("1", "2", "99"),
            direction="both",
            depth=2,
            queried_pmids=("1", "2", "10", "99"),
            edges=(),
            counts_by_level={1: {"10": 2, "11": 1}, 2: {"10": 1, "12": 2}},
            direct_seed_overlap={"10": 2, "11": 1},
        )

    monkeypatch.setattr(api, "discover_citation_paths", fake_graph)
    preview2 = await client.post(url, json={"depth": 2, "dry_run": True})
    assert preview2.status_code == 202, preview2.text
    d2 = preview2.json()
    assert (d2["depth"], d2["ranking"], d2["candidates"]) == (2, "path_count", 3)
    assert d2["by_score"] == {"1": 3, "2": 2, "3": 1, "5": 0}
    assert d2["by_overlap"] == {"1": 2, "2": 1, "3": 0, "5": 0}
    r2 = await client.post(url, json={"depth": 2, "min_overlap": 2})
    assert r2.status_code == 202, r2.text
    assert delay.call_args.args[3]["citations"]["depth"] == 2

    async def exceeds_budget(seeds, http, *, depth, direction):
        raise CitationBudgetExceeded("too many first-hop papers")

    monkeypatch.setattr(api, "discover_citation_paths", exceeds_budget)
    blocked = await client.post(url, json={"depth": 2, "dry_run": True})
    assert blocked.status_code == 422 and "first-hop" in blocked.json()["detail"]

    async def provider_failed(seeds, http, *, depth, direction):
        raise CitationProviderError("iCite omitted requested PMIDs")

    monkeypatch.setattr(api, "discover_citation_paths", provider_failed)
    unavailable = await client.post(url, json={"depth": 2, "dry_run": True})
    assert unavailable.status_code == 502
    assert (await client.post(url, json={"selection": {"q": "zzz-nothing"}})).status_code == 422
