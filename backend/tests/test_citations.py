"""Chaînage de citations : liens (iCite mocké), classement, endpoint, import et recherche créée."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_tasks_import import _efetch_xml, _make_project

from app import citation_preview, citations
from app.api.v1 import citations as api
from app.citation_graph import CitationBudgetExceeded, CitationProviderError
from app.db.models import (
    Article,
    ImportJob,
    Project,
    ProjectMember,
    ProjectMemberRole,
    ProjectStatus,
    Search,
    SearchArticle,
)
from app.tasks import _import_pubmed

ICITE = {
    "1": {"references": [10, 11], "cited_by": [20]},
    "2": {"references": [10], "cited_by": [20, 21]},
    "3": {"references": [12], "cited_by": [1]},  # 1 est une graine : ignoré
    "99": {"references": [], "cited_by": None},
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


@pytest.fixture
async def citation_project(client, researcher_user, do_login, session):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    pid = uuid.UUID((await client.post("/api/v1/projects", json={"title": "P"})).json()["id"])
    session.add_all(
        [Article(project_id=pid, pmid=p, title=p) for p in ("1", "2", "99")]
        + [Article(project_id=pid, title="sans pmid")]
    )
    (await session.get(Project, pid)).status = ProjectStatus.FROZEN
    await session.commit()
    return pid


@pytest.fixture
def cache(monkeypatch):
    values = {}

    async def put(key, value, *, ex):
        assert ex == 900
        values[key] = value

    async def take(key):
        return values.pop(key, None)

    redis = AsyncMock()
    redis.set.side_effect = put
    redis.getdel.side_effect = take
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=redis)
    monkeypatch.setattr(citation_preview.Redis, "from_url", lambda _: context)
    return values


@pytest.fixture
def discovery_calls(monkeypatch):
    calls = []

    def observe(request):
        calls.append(request.url)
        return handler(request)

    monkeypatch.setattr(
        api, "_make_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(observe))
    )
    return calls


@pytest.fixture
def enqueue(monkeypatch):
    delay = MagicMock(return_value=MagicMock(id="job-1", state="PENDING"))
    monkeypatch.setattr(api.import_pubmed_task, "delay", delay)
    return delay


async def test_depth_one_endpoint(client, citation_project, enqueue, discovery_calls):
    url = f"/api/v1/projects/{citation_project}/citations"
    dry = await client.post(url, json={"dry_run": True})
    assert dry.status_code == 202, dry.text
    d = dry.json()
    assert (d["seeds"], d["selected"], d["candidates"]) == (3, 4, 4)
    assert d["by_overlap"] == {"1": 4, "2": 2, "3": 0, "5": 0}
    assert "preview_token" not in d
    r = await client.post(
        url, json={"direction": "forward", "min_overlap": 2, "max_candidates": 50}
    )
    assert r.status_code == 202 and r.json()["job_id"] == "job-1"
    opts = enqueue.call_args.args[3]
    assert opts["kind"] == "citations" and opts["citations"]["min_overlap"] == 2
    assert opts["citations"]["seeds"] == ["1", "2", "99"]
    assert opts["citations"]["depth"] == 1
    assert (await client.post(url, json={"depth": 3})).status_code == 422
    assert (await client.post(url, json={"selection": {"q": "zzz-nothing"}})).status_code == 422


@pytest.mark.parametrize("minimum,expected", [(3, ["30"]), (1, ["30", "10"]), (50, [])])
async def test_confirmed_depth_two_import(
    minimum, expected, client, citation_project, session, engine, cache, discovery_calls, enqueue
):
    url = f"/api/v1/projects/{citation_project}/citations"
    params = {"depth": 2, "min_overlap": minimum, "max_candidates": 2}
    preview = await client.post(url, json={**params, "dry_run": True})
    assert preview.status_code == 202, preview.text
    data = preview.json()
    assert (data["depth"], data["ranking"], data["candidates"]) == (2, "path_count", 5)
    assert data["ranked_count"] == len(expected)
    assert data["by_score"] == {"1": 5, "2": 3, "3": 1, "5": 1}
    assert data["by_overlap"] == {"1": 4, "2": 2, "3": 0, "5": 0}
    lookup_count = len(discovery_calls)
    payload = {**params, "preview_token": data["preview_token"]}
    # Only the cached server selection can determine the queued candidates.
    response = await client.post(url, json={**payload, "ranked": [{"pmid": "777"}]})
    assert response.status_code == 202, response.text
    assert (await client.post(url, json=payload)).status_code == 409
    assert len(discovery_calls) == lookup_count
    assert not cache
    args = enqueue.call_args.args
    run = args[3]["citations"]["citation_run"]
    assert [r["pmid"] for r in run["ranked"]] == expected
    assert data["preview_token"] not in str(args)
    assert len(run["edge_sha256"]) == 64 and run["seed_pmids"] == ["1", "2", "99"]

    requests = []

    def metadata_only(request):
        requests.append(request.url)
        assert request.url.path.endswith("efetch.fcgi")
        assert request.url.params["id"].split(",") == expected
        return handler(request)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(metadata_only)) as http:
        result = await _import_pubmed(
            args[0], args[1], session_factory=factory, http=http, options=args[3]
        )
    assert len(requests) == bool(expected)
    assert result["imported"] == len(expected)
    job = (await session.scalars(select(ImportJob))).one()
    assert job.result_json["citation_run"] == {**run, "imported_pmids": expected}
    if expected:
        articles = list(await session.scalars(select(Article).where(Article.origin == "citation")))
        assert {a.pmid: a.origin_seeds for a in articles} == {
            pmid: {"30": 0, "10": 2}[pmid] for pmid in expected
        }
        search = await session.get(Search, uuid.UUID(result["search_id"]))
        assert search.parent_spec["citations"]["ranked_scores"] == {
            pmid: {"30": 6, "10": 2}[pmid] for pmid in expected
        }
    else:
        assert result["search_id"] is None


@pytest.mark.parametrize(
    "changed",
    [
        {"depth": 1},
        {"direction": "forward"},
        {"min_overlap": 2},
        {"max_candidates": 299},
        {"selection": {"q": "unchanged-PMIDs-are-also-insufficient"}},
    ],
)
async def test_preview_rejects_changed_request(
    client, citation_project, cache, enqueue, discovery_calls, changed
):
    url = f"/api/v1/projects/{citation_project}/citations"
    preview = await client.post(url, json={"depth": 2, "dry_run": True})
    token = preview.json()["preview_token"]
    response = await client.post(url, json={"depth": 2, "preview_token": token, **changed})
    assert response.status_code == 409 and response.json()["detail"] == "citation_preview_stale"
    enqueue.assert_not_called()


@pytest.mark.parametrize("when", ["before_submit", "after_enqueue"])
async def test_corpus_change_requires_new_preview(
    client, citation_project, cache, enqueue, discovery_calls, session, engine, when
):
    url = f"/api/v1/projects/{citation_project}/citations"
    params = {"depth": 2}
    data = (await client.post(url, json={**params, "dry_run": True})).json()
    params["preview_token"] = data["preview_token"]
    if when == "after_enqueue":
        assert (await client.post(url, json=params)).status_code == 202
    session.add(Article(project_id=citation_project, title="new DOI-only record"))
    await session.commit()
    if when == "before_submit":
        assert (await client.post(url, json=params)).status_code == 409
        enqueue.assert_not_called()
    else:
        args = enqueue.call_args.args
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: pytest.fail("unexpected lookup"))
        ) as http:
            with pytest.raises(ValueError, match="citation_preview_stale"):
                await _import_pubmed(
                    args[0], args[1], session_factory=factory, http=http, options=args[3]
                )
        job = (await session.scalars(select(ImportJob))).one()
        assert job.status.value == "FAILED"
        assert job.result_json["citation_run"] == args[3]["citations"]["citation_run"]
    assert not list(await session.scalars(select(Article).where(Article.origin == "citation")))
    assert not list(await session.scalars(select(Search).where(Search.is_default.is_(False))))


async def test_missing_expired_token_and_cross_project(
    client, citation_project, session, cache, enqueue, discovery_calls
):
    url = f"/api/v1/projects/{citation_project}/citations"
    assert (await client.post(url, json={"depth": 2})).status_code == 409
    data = (await client.post(url, json={"depth": 2, "dry_run": True})).json()
    params = {"depth": 2, "preview_token": data["preview_token"]}
    other = (await client.post("/api/v1/projects", json={"title": "another project"})).json()["id"]
    assert (
        await client.post(f"/api/v1/projects/{other}/citations", json=params)
    ).status_code == 409
    assert len(cache) == 1
    cache.clear()  # Redis TTL expiry
    assert (await client.post(url, json=params)).status_code == 409
    enqueue.assert_not_called()


async def test_actor_bound_and_submit_rechecks_lead(
    client,
    citation_project,
    session,
    researcher_user,
    admin_user,
    do_login,
    cache,
    enqueue,
    discovery_calls,
):
    url = f"/api/v1/projects/{citation_project}/citations"
    data = (await client.post(url, json={"depth": 2, "dry_run": True})).json()
    params = {"depth": 2, "preview_token": data["preview_token"]}
    await do_login("admin@lab.fr", "admin-pass-123")
    assert (await client.post(url, json=params)).status_code == 409
    member = await session.get(ProjectMember, (citation_project, researcher_user.id))
    member.role_in_project = ProjectMemberRole.ANNOTATOR
    await session.commit()
    await do_login("researcher@lab.fr", "researcher-pass-123")
    assert (await client.post(url, json=params)).status_code == 403
    assert (await client.post(url, json={"depth": 2, "dry_run": True})).status_code == 403
    assert len(cache) == 1
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    "error,code",
    [
        (CitationBudgetExceeded("first-hop budget"), 422),
        (CitationProviderError("provider omission"), 502),
    ],
)
async def test_discovery_failure_preserves_corpus(
    client, citation_project, session, cache, enqueue, monkeypatch, error, code
):
    monkeypatch.setattr(api, "discover_citation_paths", AsyncMock(side_effect=error))
    url = f"/api/v1/projects/{citation_project}/citations"
    blocked = await client.post(url, json={"depth": 2, "dry_run": True})
    assert blocked.status_code == code and str(error) in blocked.json()["detail"]
    assert not cache
    enqueue.assert_not_called()
    assert not list(await session.scalars(select(ImportJob)))
    assert not list(await session.scalars(select(Article).where(Article.origin == "citation")))
    assert not list(await session.scalars(select(Search).where(Search.is_default.is_(False))))


async def test_unseeded_sample_is_bound_to_preview(
    client, citation_project, cache, enqueue, discovery_calls
):
    url = f"/api/v1/projects/{citation_project}/citations"
    params = {"depth": 2, "selection": {"sample_n": 3}}
    preview = (await client.post(url, json={**params, "dry_run": True})).json()
    assert preview["selected"] == 3
    assert (
        await client.post(url, json={**params, "preview_token": preview["preview_token"]})
    ).status_code == 202
    enqueue.assert_called_once()
