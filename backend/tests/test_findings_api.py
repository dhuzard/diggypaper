"""Finding curation: immutable revisions, exact evidence links, independent review, export."""

import hashlib
import importlib.util
import io
import json
import sys
import uuid
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, text, update
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.db.models import (
    Article,
    ArticlePrediction,
    FindingRevision,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Search,
    SourceSnapshot,
    User,
    UserRole,
)
from app.evidence_prediction import prediction_envelope
from app.pseudonym import anonymized_actor_ref, reviewer_reference

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "docs/contracts/source-evidence-packet/v0.1.0/validator/sep_validate.py"
PASSWORD = "reviewer-pass-123"
LEAD = ("researcher@lab.fr", "researcher-pass-123")
REV1 = ("rev1@lab.fr", PASSWORD)
REV2 = ("rev2@lab.fr", PASSWORD)
OUTSIDER = ("outsider@lab.fr", PASSWORD)


def _load_validator():
    spec = importlib.util.spec_from_file_location("sep_validate_findings", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sep_validate_findings"] = module
    spec.loader.exec_module(module)
    return module


def _content(**overrides):
    base = {
        "statement": "Treatment reduced weight at day 14.",
        "origin": "source-explicit",
        "context": {
            "population": "adult male mice",
            "exposure": "drug X 10 mg/kg",
            "comparator": "vehicle",
            "outcome": "body weight",
            "time": "day 14",
            "experimental_unit": "animal",
        },
        "numeric_results": [
            {
                "name": "mean difference",
                "value": -1.5,
                "unit": "g",
                "n": {"value": 12, "definition": "group"},
            }
        ],
    }
    return {**base, **overrides}


async def _user(session, email, role=UserRole.RESEARCHER):
    user = User(
        email=email,
        hashed_password=hash_password(PASSWORD),
        role=role,
        data_consent_given=True,
        can_create_projects=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _world(session, researcher_user):
    """Project (researcher LEAD; rev1/rev2 ANNOTATOR members; outsider not a member)."""
    researcher_user.data_consent_given = True
    users = {"lead": researcher_user}
    for key, (email, _) in (("rev1", REV1), ("rev2", REV2), ("outsider", OUTSIDER)):
        users[key] = await _user(session, email)
    project = Project(title="Findings", created_by=researcher_user.id)
    session.add(project)
    await session.flush()
    for key, role in (
        ("lead", ProjectMemberRole.LEAD),
        ("rev1", ProjectMemberRole.ANNOTATOR),
        ("rev2", ProjectMemberRole.ANNOTATOR),
    ):
        session.add(
            ProjectMember(project_id=project.id, user_id=users[key].id, role_in_project=role)
        )
    article = Article(
        project_id=project.id,
        title="Mice study",
        doi="10.1000/mice",
        authors=["A. Author"],
        pub_types=["Journal Article"],
    )
    session.add(article)
    await session.commit()
    return project, article, users


async def _article(session, project, **kw):
    article = Article(project_id=project.id, title="Other", **kw)
    session.add(article)
    await session.commit()
    return article


async def _prediction(
    session,
    project,
    article,
    *,
    search_id=None,
    quotes=("Mice were randomized.", "Weight fell by 1.5 g."),
    status="exact",
    snapshot=False,
):
    """Prediction with one evidence passage per quote; returns (prediction, links)."""
    prediction_id, invocation_id = uuid.uuid4(), uuid.uuid4()
    snapshot_id = uuid.uuid4()
    decision = {
        "use_ai_behavior": True,
        "animal_sex": "BOTH_SEXES",
        "evidence_quotes": list(quotes),
        "evidence_links": [
            {"field_key": "use_ai_behavior" if i == 0 else "animal_sex", "quote_index": i}
            for i in range(len(quotes))
        ],
    }
    items = []
    for i, quote in enumerate(quotes):
        result = {"quote": quote, "input_ref": str(invocation_id), "status": status}
        if status in ("exact", "normalized"):
            result["candidates"] = [
                {"view_ref": str(uuid.uuid4()), "view_start": 10 * i, "view_end": 10 * i + 5}
            ]
        items.append({"quote_index": i, "match_ref": str(uuid.uuid4()), "result": result})
    checks = {
        "schema_version": "quote-checks-v1",
        "source_snapshot_ref": str(snapshot_id),
        "input_ref": str(invocation_id),
        "items": items,
    }
    graph = prediction_envelope(
        prediction_id=prediction_id,
        project_id=project.id,
        article_id=article.id,
        project=SimpleNamespace(question=None, fields=None),
        decision=decision,
        invocation_id=invocation_id,
        source_snapshot_id=snapshot_id,
        quote_checks=checks,
    )
    assert graph["envelope"]["state"] == "structured"
    values = {}
    if snapshot:
        payload = {"k": "v"}
        canonical = json.dumps(payload).encode()
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        # Fixture shortcut: no Invocation row is built, so skip its binding trigger.
        names = await session.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='article_predictions' AND sql LIKE '%BEFORE INSERT%'"
            )
        )
        for (name,) in names.all():
            await session.execute(text(f'DROP TRIGGER "{name}"'))
        # Core insert: the ORM validates real provenance payloads, which is out of scope here.
        await session.execute(
            insert(SourceSnapshot.__table__).values(
                id=snapshot_id,
                project_id=project.id,
                article_id=article.id,
                captured_at=datetime(2026, 1, 2, tzinfo=UTC),
                schema_version=1,
                snapshot_sha256=hashlib.sha256(canonical).hexdigest(),
                payload=payload,
                canonical_bytes=canonical,
                raw_availability="not_captured",
            )
        )
        values = {
            "source_snapshot_id": snapshot_id,
            "provenance_project_id": project.id,
            "invocation_id": invocation_id,
            "invocation_group_id": uuid.uuid4(),
            "quote_checks_json": checks,
        }
    session.add(
        ArticlePrediction(
            id=prediction_id,
            article_id=article.id,
            search_id=search_id,
            model_used="test",
            decision_json=decision,
            evidence_quotes=decision["evidence_quotes"],
            evidence_graph_json=graph,
            **values,
        )
    )
    await session.commit()
    if snapshot:
        await session.execute(text("PRAGMA foreign_keys=ON"))
    prediction = await session.get(ArticlePrediction, prediction_id)
    envelope = graph["envelope"]
    quote_of = {e["evidence_ref"]: e["quote"] for e in envelope["evidence"]}
    links = sorted(
        (
            {
                "prediction_id": str(prediction_id),
                "answer_ref": link["answer_ref"],
                "evidence_ref": link["evidence_ref"],
            }
            for link in envelope["links"]
        ),
        key=lambda x: quotes.index(quote_of[x["evidence_ref"]]),
    )
    return prediction, links


def _base(project, article):
    return f"/api/v1/projects/{project.id}/articles/{article.id}/findings"


async def _login(do_login, who):
    response = await do_login(*who, consent=True)
    assert response.status_code == 200, response.text


async def _propose(client, project, article, links, content=None):
    response = await client.post(
        _base(project, article), json={"content": content or _content(), "evidence": links}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _review(links, decision="approve", status="supports", **kw):
    judgments = [
        {"evidence_ref": link["evidence_ref"], "status": status, "rationale": "Checked context."}
        for link in links
    ]
    return {"decision": decision, "rationale": "Reviewed.", "judgments": judgments, **kw}


def _rev_url(project, article, finding_id, revision):
    return f"{_base(project, article)}/{finding_id}/revisions/{revision}/reviews"


async def _list(client, project, article):
    response = await client.get(_base(project, article))
    assert response.status_code == 200, response.text
    return response.json()["findings"]


def _revisions(findings, finding_id):
    return next(f for f in findings if f["id"] == finding_id)["revisions"]


# --- proposing ---------------------------------------------------------------------------


async def test_two_findings_on_one_article_stay_distinct_and_null_results_retained(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    prediction, links = await _prediction(session, project, article)
    await _login(do_login, LEAD)
    nonsig = _content(
        numeric_results=[
            {
                "name": "p-value",
                "value": 0.0,
                "unit": "p",
                "n": {"value": 12, "definition": "group"},
            },
            {
                "name": "difference",
                "value": -0.0,
                "unit": "g",
                "n": {"value": 0, "definition": "unknown"},
            },
        ]
    )
    nonsig["statement"] = "No significant change in food intake at week 8."
    nonsig["context"] = {**_content()["context"], "outcome": "food intake", "time": "week 8"}
    first = await _propose(client, project, article, links[:1])
    second = await _propose(client, project, article, links[1:], nonsig)
    assert first["finding_id"] != second["finding_id"]
    assert first["revision"] == second["revision"] == 1
    listed = await _list(client, project, article)
    assert len(listed) == 2
    contents = {f["revisions"][0]["content"]["context"]["outcome"] for f in listed}
    times = {f["revisions"][0]["content"]["context"]["time"] for f in listed}
    assert contents == {"body weight", "food intake"} and times == {"day 14", "week 8"}
    stored = _revisions(listed, second["finding_id"])[0]["content"]["numeric_results"]
    # Zero and negative results are kept verbatim, never dropped or coerced.
    assert [r["value"] for r in stored] == [0.0, -0.0] and len(stored) == 2
    assert stored[1]["n"] == {"value": 0, "definition": "unknown"}
    assert _revisions(listed, first["finding_id"])[0]["state"] == "proposed"
    assert prediction.id


async def test_shared_passage_across_findings_and_second_article_not_merged(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    other = await _article(session, project, doi="10.1000/other")
    _, links = await _prediction(session, project, article)
    _, other_links = await _prediction(session, project, other)
    await _login(do_login, LEAD)
    a = await _propose(client, project, article, links[:1])
    b = await _propose(client, project, article, links[:1], _content(statement="Another claim."))
    c = await _propose(client, project, other, other_links[:1])
    listed = await _list(client, project, article)
    assert {f["id"] for f in listed} == {a["finding_id"], b["finding_id"]}
    refs = [f["revisions"][0]["evidence"][0]["evidence_ref"] for f in listed]
    assert refs[0] == refs[1] == links[0]["evidence_ref"]
    other_listed = await _list(client, project, other)
    assert [f["id"] for f in other_listed] == [c["finding_id"]]
    # Evidence of one article cannot be attached to another article's finding.
    response = await client.post(
        _base(project, other), json={"content": _content(), "evidence": links[:1]}
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "fault", ["unknown_answer", "unknown_evidence", "unknown_prediction", "duplicate", "empty"]
)
async def test_unknown_forged_or_empty_evidence_rejected(
    client, session, researcher_user, do_login, fault
):
    project, article, _ = await _world(session, researcher_user)
    _, links = await _prediction(session, project, article)
    await _login(do_login, LEAD)
    link = dict(links[0])
    evidence = [link]
    if fault == "unknown_answer":
        link["answer_ref"] = str(uuid.uuid4())
    elif fault == "unknown_evidence":
        link["evidence_ref"] = str(uuid.uuid4())
    elif fault == "unknown_prediction":
        link["prediction_id"] = str(uuid.uuid4())
    elif fault == "duplicate":
        evidence = [link, dict(link)]
    else:
        evidence = []
    response = await client.post(
        _base(project, article), json={"content": _content(), "evidence": evidence}
    )
    assert response.status_code in (409, 422), response.text
    assert await _list(client, project, article) == []


async def test_legacy_or_forged_prediction_graph_cannot_back_a_finding(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    prediction, links = await _prediction(session, project, article)
    await _login(do_login, LEAD)
    graph = deepcopy(prediction.evidence_graph_json)
    graph["envelope"]["evidence"][0].update(
        origin="legacy_unversioned", source_binding_ref=None, quote_match_ref=None
    )
    prediction.evidence_graph_json = graph
    await session.commit()
    response = await client.post(
        _base(project, article), json={"content": _content(), "evidence": links[:1]}
    )
    assert response.status_code == 409, response.text
    legacy = ArticlePrediction(article_id=article.id, model_used="legacy")
    session.add(legacy)
    await session.commit()
    body = {
        "content": _content(),
        "evidence": [{**links[0], "prediction_id": str(legacy.id)}],
    }
    assert (await client.post(_base(project, article), json=body)).status_code == 409
    assert await _list(client, project, article) == []


async def test_evidence_from_another_project_rejected(client, session, researcher_user, do_login):
    project, article, _ = await _world(session, researcher_user)
    foreign = Project(title="Foreign", created_by=researcher_user.id)
    session.add(foreign)
    await session.flush()
    session.add(
        ProjectMember(
            project_id=foreign.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    foreign_article = await _article(session, foreign)
    _, foreign_links = await _prediction(session, foreign, foreign_article)
    await _login(do_login, LEAD)
    response = await client.post(
        _base(project, article), json={"content": _content(), "evidence": foreign_links[:1]}
    )
    assert response.status_code in (409, 422), response.text
    assert await _list(client, project, article) == []
    # The foreign article is not addressable through this project either.
    response = await client.get(
        f"/api/v1/projects/{project.id}/articles/{foreign_article.id}/findings"
    )
    assert response.status_code == 404


async def test_non_member_and_cross_project_access_denied(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    _, links = await _prediction(session, project, article)
    await _login(do_login, LEAD)
    created = await _propose(client, project, article, links[:1])
    await _login(do_login, OUTSIDER)
    assert (await client.get(_base(project, article))).status_code in (403, 404)
    response = await client.post(
        _base(project, article), json={"content": _content(), "evidence": links[:1]}
    )
    assert response.status_code in (403, 404)
    response = await client.post(
        _rev_url(project, article, created["finding_id"], 1), json=_review(links[:1])
    )
    assert response.status_code in (403, 404)
    assert (await client.get(f"/api/v1/projects/{project.id}/findings/export")).status_code == 403


async def test_blind_search_prediction_cannot_back_a_finding(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    search = Search(project_id=project.id, name="Blind", kind="blind", lot_all=True)
    session.add(search)
    await session.commit()
    _, links = await _prediction(session, project, article, search_id=search.id)
    await _login(do_login, LEAD)
    response = await client.post(
        _base(project, article), json={"content": _content(), "evidence": links[:1]}
    )
    assert response.status_code == 409, response.text
    assert await _list(client, project, article) == []


async def test_invalid_content_rejected(client, session, researcher_user, do_login):
    project, article, _ = await _world(session, researcher_user)
    _, links = await _prediction(session, project, article)
    await _login(do_login, LEAD)
    bad = _content()
    del bad["context"]["time"]
    response = await client.post(
        _base(project, article), json={"content": bad, "evidence": links[:1]}
    )
    assert response.status_code == 422


# --- reviewing ---------------------------------------------------------------------------


async def test_review_rules(client, session, researcher_user, do_login):
    project, article, _ = await _world(session, researcher_user)
    _, links = await _prediction(session, project, article)
    await _login(do_login, LEAD)
    created = await _propose(client, project, article, links)
    fid = created["finding_id"]
    url = _rev_url(project, article, fid, 1)
    # The proposer cannot review their own revision.
    assert (await client.post(url, json=_review(links))).status_code == 403
    await _login(do_login, REV1)
    # Approve without any supports judgment.
    no_support = _review(links, status="insufficient")
    assert (await client.post(url, json=no_support)).status_code == 422
    # Judgments must match the evidence set exactly.
    assert (await client.post(url, json=_review(links[:1]))).status_code == 422
    extra = _review(links)
    extra["judgments"].append(
        {"evidence_ref": str(uuid.uuid4()), "status": "supports", "rationale": "x"}
    )
    assert (await client.post(url, json=extra)).status_code == 422
    dup = _review(links)
    dup["judgments"][1] = dict(dup["judgments"][0])
    assert (await client.post(url, json=dup)).status_code == 422
    blank = _review(links)
    blank["judgments"][0]["rationale"] = " "
    assert (await client.post(url, json=blank)).status_code == 422
    assert (await client.post(url, json=_review(links, rationale=" "))).status_code == 422
    assert (await client.post(url, json=_review(links, decision="maybe"))).status_code == 422
    assert (await client.post(url, json=_review([]))).status_code == 422
    # Nothing was stored by any rejected attempt.
    assert _revisions(await _list(client, project, article), fid)[0]["reviews"] == []
    ok = await client.post(url, json=_review(links))
    assert ok.status_code == 201, ok.text
    assert ok.json()["protocol_version"] == "seip-support-v1"
    # One review per reviewer per revision.
    assert (await client.post(url, json=_review(links))).status_code == 409
    revision = _revisions(await _list(client, project, article), fid)[0]
    assert revision["state"] == "approved" and len(revision["reviews"]) == 1
    # A later rejection by another reviewer wins over the approval.
    await _login(do_login, REV2)
    rejected = await client.post(url, json=_review(links, decision="reject", status="unclear"))
    assert rejected.status_code == 201, rejected.text
    revision = _revisions(await _list(client, project, article), fid)[0]
    assert revision["state"] == "rejected"
    # Unknown revision.
    assert (
        await client.post(_rev_url(project, article, fid, 9), json=_review(links))
    ).status_code == 404


async def test_revision_supersedes_and_approvals_do_not_carry(
    client, session, researcher_user, do_login
):
    project, article, _ = await _world(session, researcher_user)
    _, links = await _prediction(session, project, article)
    await _login(do_login, LEAD)
    created = await _propose(client, project, article, links)
    fid = created["finding_id"]
    await _login(do_login, REV1)
    assert (
        await client.post(_rev_url(project, article, fid, 1), json=_review(links))
    ).status_code == 201
    await _login(do_login, LEAD)
    response = await client.post(
        f"{_base(project, article)}/{fid}/revisions",
        json={"content": _content(statement="Revised claim."), "evidence": links[:1]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["revision"] == 2 and response.json()["state"] == "proposed"
    assert response.json()["reviews"] == []
    revisions = _revisions(await _list(client, project, article), fid)
    assert [r["state"] for r in revisions] == ["superseded", "proposed"]
    assert [len(r["reviews"]) for r in revisions] == [1, 0]
    assert [r["is_latest"] for r in revisions] == [False, True]
    # The old revision is no longer reviewable, even by a fresh reviewer.
    await _login(do_login, REV2)
    old = await client.post(_rev_url(project, article, fid, 1), json=_review(links))
    assert old.status_code == 409
    # The new revision needs its own review and links only its own evidence set.
    assert (
        await client.post(_rev_url(project, article, fid, 2), json=_review(links))
    ).status_code == 422
    assert (
        await client.post(_rev_url(project, article, fid, 2), json=_review(links[:1]))
    ).status_code == 201
    assert _revisions(await _list(client, project, article), fid)[1]["state"] == "approved"
    # Revising someone else's finding id from another article is not possible.
    other = await _article(session, project)
    missing = await client.post(
        f"{_base(project, other)}/{fid}/revisions",
        json={"content": _content(), "evidence": links[:1]},
    )
    assert missing.status_code == 404


async def test_quote_match_and_judgment_are_separate_and_refs_are_pseudonyms(
    client, session, researcher_user, do_login
):
    project, article, users = await _world(session, researcher_user)
    _, links = await _prediction(session, project, article, status="not_found", snapshot=True)
    await _login(do_login, LEAD)
    created = await _propose(client, project, article, links)
    fid = created["finding_id"]
    await _login(do_login, REV1)
    # A not_found machine match does not block or alter the human judgment.
    done = await client.post(_rev_url(project, article, fid, 1), json=_review(links))
    assert done.status_code == 201, done.text
    listing = await client.get(_base(project, article))
    revision = _revisions(listing.json()["findings"], fid)[0]
    for item in revision["evidence"]:
        assert item["quote"] and item["quote_match"]["status"] == "not_found"
        assert item["quote_match"]["start"] is None and item["quote_match"]["view_ref"] is None
    assert {j["status"] for j in revision["reviews"][0]["judgments"]} == {"supports"}
    # Matched passages expose offsets, still without implying any judgment.
    _, exact = await _prediction(session, project, article, status="exact", snapshot=True)
    await _login(do_login, LEAD)
    second = await _propose(client, project, article, exact[:1])
    body = (await client.get(_base(project, article))).text
    match = _revisions(json.loads(body)["findings"], second["finding_id"])[0]["evidence"][0][
        "quote_match"
    ]
    assert match["status"] == "exact" and match["start"] == 0 and match["end"] == 5
    assert _revisions(json.loads(body)["findings"], second["finding_id"])[0]["reviews"] == []
    # Only project-scoped pseudonyms: no emails, user ids or the global digest anywhere.
    lead_digest = anonymized_actor_ref(users["lead"].email)
    rev_digest = anonymized_actor_ref(users["rev1"].email)
    texts = [body, json.dumps(created), done.text]
    for blob in texts:
        for secret in (
            users["lead"].email,
            users["rev1"].email,
            str(users["lead"].id),
            str(users["rev1"].id),
            lead_digest,
            rev_digest,
        ):
            assert secret not in blob
    first = _revisions(json.loads(body)["findings"], fid)[0]
    assert first["proposer_ref"] == reviewer_reference(project.id, lead_digest)
    assert first["reviews"][0]["reviewer_ref"] == reviewer_reference(project.id, rev_digest)


async def test_finding_tables_are_append_only(client, session, researcher_user, do_login):
    project, article, _ = await _world(session, researcher_user)
    _, links = await _prediction(session, project, article)
    await _login(do_login, LEAD)
    created = await _propose(client, project, article, links)
    revision = await session.scalar(
        FindingRevision.__table__.select().where(
            FindingRevision.finding_id == uuid.UUID(created["finding_id"])
        )
    )
    assert revision is not None
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                update(FindingRevision)
                .where(FindingRevision.finding_id == uuid.UUID(created["finding_id"]))
                .values(content_json={"statement": "tampered"})
            )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                update(FindingRevision)
                .where(FindingRevision.finding_id == uuid.UUID(created["finding_id"]))
                .values(proposer_ref="f" * 64)
            )


# --- export ------------------------------------------------------------------------------


def _export_url(project):
    return f"/api/v1/projects/{project.id}/findings/export"


async def test_annotator_cannot_export(client, session, researcher_user, do_login):
    project, _, _ = await _world(session, researcher_user)
    await _login(do_login, REV1)
    assert (await client.get(_export_url(project))).status_code == 403


def _unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in archive.infolist())
        return {name: archive.read(name) for name in archive.namelist()}


async def test_export_is_deterministic_valid_and_omits_unreviewed_and_rejected(
    client, session, researcher_user, do_login, tmp_path
):
    project, article, _ = await _world(session, researcher_user)
    _, links = await _prediction(session, project, article, snapshot=True)
    await _login(do_login, LEAD)
    approved = await _propose(client, project, article, links[:1])
    unreviewed = await _propose(client, project, article, links[1:], _content(statement="Idle."))
    rejected = await _propose(client, project, article, links[:1], _content(statement="Bad."))
    await _login(do_login, REV1)
    ok = await client.post(
        _rev_url(project, article, approved["finding_id"], 1), json=_review(links[:1])
    )
    assert ok.status_code == 201, ok.text
    no = await client.post(
        _rev_url(project, article, rejected["finding_id"], 1),
        json=_review(links[:1], decision="reject", status="unclear"),
    )
    assert no.status_code == 201
    await _login(do_login, LEAD)
    first = await client.get(_export_url(project))
    assert first.status_code == 200, first.text
    assert first.headers["content-type"].startswith("application/zip")
    second = await client.get(_export_url(project))
    assert first.content == second.content
    files = _unzip(first.content)
    assert "manifest.json" in files
    for name in ("papers", "findings", "passages", "assessments"):
        assert f"{name}.jsonl" in files
    findings = [json.loads(x) for x in files["findings.jsonl"].decode().splitlines()]
    exported = json.dumps(findings)
    assert approved["finding_id"] in exported
    assert unreviewed["finding_id"] not in exported and rejected["finding_id"] not in exported
    manifest = json.loads(files["manifest.json"])
    blob = json.dumps(manifest)
    assert "partial" in blob and "not-approved" in blob and "rejected" in blob
    for secret in (REV1[0], LEAD[0]):
        assert secret.encode() not in b"".join(files.values())
    directory = tmp_path / "packet"
    directory.mkdir()
    for name, data in files.items():
        (directory / name).write_bytes(data)
    result = _load_validator().validate_packet(directory, "for-import")
    assert result["errors"] == [], result["errors"]
    # A later revision supersedes: the old approval no longer exports anything for it.
    response = await client.post(
        f"{_base(project, article)}/{approved['finding_id']}/revisions",
        json={"content": _content(statement="Changed."), "evidence": links[:1]},
    )
    assert response.status_code == 201
    after = _unzip((await client.get(_export_url(project))).content)
    assert approved["finding_id"] not in after["findings.jsonl"].decode()
