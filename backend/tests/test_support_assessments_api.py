"""Human support labels stay scoped to authorized, exact graph revisions."""

import uuid
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Article,
    ArticlePrediction,
    EvidenceSupportAssessment,
    HumanAnnotation,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Search,
)
from app.evidence_prediction import prediction_envelope
from app.pseudonym import anonymized_actor_ref


async def _project_article(session, user):
    project = Project(title="Assessment", created_by=user.id)
    session.add(project)
    await session.flush()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.ANNOTATOR
        )
    )
    article = Article(project_id=project.id, title="Mice")
    session.add(article)
    await session.commit()
    return project, article


async def _prediction(session, project, article, *, search_id=None):
    prediction_id, invocation_id, snapshot_id = (uuid.uuid4() for _ in range(3))
    decision = {
        "use_ai_behavior": True,
        "animal_sex": "BOTH_SEXES",
        "evidence_quotes": ["Mice were randomized."],
        "evidence_links": [{"field_key": "use_ai_behavior", "quote_index": 0}],
    }
    checks = {
        "items": [
            {
                "quote_index": 0,
                "match_ref": str(uuid.uuid4()),
                "result": {
                    "quote": "Mice were randomized.",
                    "input_ref": str(invocation_id),
                    "status": "not_found",
                },
            }
        ]
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
    prediction = ArticlePrediction(
        id=prediction_id,
        article_id=article.id,
        search_id=search_id,
        model_used="test",
        decision_json=decision,
        evidence_quotes=decision["evidence_quotes"],
        evidence_graph_json=graph,
    )
    session.add(prediction)
    await session.commit()
    link = graph["envelope"]["links"][0]
    payload = {
        "graph_revision_ref": graph["envelope"]["scope"]["revision_ref"],
        "answer_ref": link["answer_ref"],
        "evidence_ref": link["evidence_ref"],
        "status": "supports",
        "rationale": "Reviewer inspected the study context.",
    }
    return prediction, payload


def _url(project, article, prediction):
    return (
        f"/api/v1/projects/{project.id}/articles/{article.id}"
        f"/predictions/{prediction.id}/support-assessments"
    )


async def test_assessment_is_human_and_exact_despite_not_found_quote(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _project_article(session, researcher_user)
    prediction, payload = await _prediction(session, project, article)
    url = _url(project, article, prediction)
    before = (await client.get(url)).json()
    assert before["state"] == "not_assessed" and before["assessments"] == []
    response = await client.post(url, json=payload)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["actor_ref"] == anonymized_actor_ref(researcher_user.email)
    assert row["protocol_version"] == "seip-support-v1"
    assert row["created_at"]
    assert row["status"] == "supports"
    # The deliberately not_found occurrence check did not auto-create the label.
    assert (await client.get(url)).json()["assessments"] == [row]


async def test_unknown_link_revision_and_rationale_rejected(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _project_article(session, researcher_user)
    prediction, payload = await _prediction(session, project, article)
    url = _url(project, article, prediction)
    wrong_link = {**payload, "answer_ref": str(uuid.uuid4())}
    wrong_revision = {**payload, "graph_revision_ref": str(uuid.uuid4())}
    assert (await client.post(url, json=wrong_link)).status_code == 422
    assert (await client.post(url, json=wrong_revision)).status_code == 409
    assert (await client.post(url, json={**payload, "rationale": " "})).status_code == 422
    assert (await client.get(url)).json()["state"] == "not_assessed"


async def test_forged_structured_link_to_legacy_evidence_is_not_assessable(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _project_article(session, researcher_user)
    prediction, payload = await _prediction(session, project, article)
    graph = deepcopy(prediction.evidence_graph_json)
    evidence = graph["envelope"]["evidence"][0]
    evidence.update(origin="legacy_unversioned", source_binding_ref=None, quote_match_ref=None)
    prediction.evidence_graph_json = graph
    await session.commit()
    url = _url(project, article, prediction)
    response = await client.post(url, json=payload)
    assert response.status_code == 409, response.text
    assert (await client.get(url)).json()["assessments"] == []


@pytest.mark.parametrize(
    "fault",
    [
        "missing_answer",
        "duplicate_answer",
        "missing_evidence",
        "duplicate_evidence",
        "duplicate_link",
    ],
)
async def test_forged_graph_nodes_or_links_cannot_receive_judgment(
    client, session, researcher_user, do_login, fault
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _project_article(session, researcher_user)
    prediction, payload = await _prediction(session, project, article)
    graph = deepcopy(prediction.evidence_graph_json)
    envelope = graph["envelope"]
    if fault == "missing_answer":
        envelope["answers"] = []
    elif fault == "duplicate_answer":
        envelope["answers"].append(deepcopy(envelope["answers"][0]))
    elif fault == "missing_evidence":
        envelope["evidence"] = []
    elif fault == "duplicate_evidence":
        envelope["evidence"].append(deepcopy(envelope["evidence"][0]))
    else:
        envelope["links"].append(deepcopy(envelope["links"][0]))
    prediction.evidence_graph_json = graph
    await session.commit()
    url = _url(project, article, prediction)
    response = await client.post(url, json=payload)
    assert response.status_code == 409, response.text
    assert (await client.get(url)).json()["assessments"] == []


async def test_explicit_not_assessed_is_not_a_support_verdict(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _project_article(session, researcher_user)
    prediction, payload = await _prediction(session, project, article)
    url = _url(project, article, prediction)
    response = await client.post(url, json={**payload, "status": "not_assessed", "rationale": ""})
    assert response.status_code == 201
    body = (await client.get(url)).json()
    assert body["state"] == "not_assessed"
    assert len(body["assessments"]) == 1


async def test_new_prediction_does_not_inherit_old_assessment(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _project_article(session, researcher_user)
    old, payload = await _prediction(session, project, article)
    old_url = _url(project, article, old)
    assert (await client.post(old_url, json=payload)).status_code == 201
    new, new_payload = await _prediction(session, project, article)
    await session.refresh(old)
    new.created_at = old.created_at + timedelta(seconds=1)
    await session.commit()
    new_url = _url(project, article, new)
    assert (await client.get(old_url)).json()["current"] is False
    assert (await client.post(old_url, json=payload)).status_code == 409
    latest = (await client.get(new_url)).json()
    assert latest["current"] is True
    assert latest["state"] == "not_assessed" and latest["assessments"] == []
    assert new_payload["answer_ref"] != payload["answer_ref"]


async def test_legacy_prediction_unassessed_and_write_conflicts(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _project_article(session, researcher_user)
    prediction = ArticlePrediction(article_id=article.id, model_used="legacy")
    session.add(prediction)
    await session.commit()
    url = _url(project, article, prediction)
    body = (await client.get(url)).json()
    assert body["graph_revision_ref"] is None
    assert body["state"] == "not_assessed"
    payload = {
        "graph_revision_ref": str(uuid.uuid4()),
        "answer_ref": str(uuid.uuid4()),
        "evidence_ref": str(uuid.uuid4()),
        "status": "supports",
        "rationale": "test",
    }
    assert (await client.post(url, json=payload)).status_code == 409


async def test_member_and_blind_boundaries(client, session, researcher_user, admin_user, do_login):
    project, article = await _project_article(session, researcher_user)
    search = Search(project_id=project.id, name="Blind", kind="blind", lot_all=True)
    session.add(search)
    await session.commit()
    prediction, payload = await _prediction(session, project, article, search_id=search.id)
    url = _url(project, article, prediction) + f"?search={search.id}"
    assert (await client.get(url)).status_code == 401
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert (await client.get(url)).status_code == 404
    assert (await client.post(url, json=payload)).status_code == 404
    session.add(
        HumanAnnotation(
            article_id=article.id,
            search_id=search.id,
            user_id=researcher_user.id,
            blind=True,
        )
    )
    await session.commit()
    session.add(
        EvidenceSupportAssessment(
            prediction_id=prediction.id,
            project_id=project.id,
            article_id=article.id,
            graph_revision_ref=prediction.id,
            answer_ref=uuid.UUID(payload["answer_ref"]),
            evidence_ref=uuid.UUID(payload["evidence_ref"]),
            actor_id=admin_user.id,
            actor_ref=anonymized_actor_ref(admin_user.email),
            status="unclear",
            rationale="Independent review pending.",
            protocol_version="seip-support-v1",
        )
    )
    await session.commit()
    assert (await client.get(url)).json()["assessments"] == []
    assert (await client.post(url, json=payload)).status_code == 201
    own = (await client.get(url)).json()["assessments"]
    assert len(own) == 1 and own[0]["actor_ref"] == anonymized_actor_ref(researcher_user.email)
    # After account cleanup clears the actor FK, blind GET cannot identify this
    # preserved pseudonymous judgment as the caller's own record.
    await session.execute(
        update(EvidenceSupportAssessment)
        .where(EvidenceSupportAssessment.id == uuid.UUID(own[0]["id"]))
        .values(actor_id=None)
    )
    await session.commit()
    assert (await client.get(url)).json()["assessments"] == []
    foreign = Project(title="Foreign")
    session.add(foreign)
    await session.flush()
    foreign_article = Article(project_id=foreign.id, title="Other")
    session.add(foreign_article)
    await session.commit()
    foreign_url = _url(foreign, foreign_article, prediction)
    assert (await client.get(foreign_url)).status_code == 403


async def test_anonymization_preserves_pseudonymous_assessment(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _project_article(session, researcher_user)
    prediction, payload = await _prediction(session, project, article)
    response = await client.post(_url(project, article, prediction), json=payload)
    assert response.status_code == 201
    prior_ref = response.json()["actor_ref"]
    assert (await client.delete("/api/v1/users/me/anonymize")).status_code == 204
    assessment = await session.get(EvidenceSupportAssessment, uuid.UUID(response.json()["id"]))
    await session.refresh(assessment)
    await session.refresh(researcher_user)
    assert assessment.actor_id is None
    assert assessment.actor_ref == researcher_user.anonymized_hash == prior_ref


async def test_admin_deletion_preserves_pseudonymous_assessment(
    client, session, researcher_user, admin_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _project_article(session, researcher_user)
    prediction, payload = await _prediction(session, project, article)
    response = await client.post(_url(project, article, prediction), json=payload)
    assert response.status_code == 201
    prior_ref = response.json()["actor_ref"]
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    deleted = await client.delete(f"/api/v1/admin/users/{researcher_user.id}")
    assert deleted.status_code == 200, deleted.text
    assessment = await session.get(EvidenceSupportAssessment, uuid.UUID(response.json()["id"]))
    await session.refresh(assessment)
    assert assessment.actor_id is None
    assert assessment.actor_ref == prior_ref
    assert await session.get(ArticlePrediction, prediction.id) is not None
    history = (await client.get(_url(project, article, prediction))).json()
    assert history["assessments"][0]["actor_ref"] == prior_ref


async def test_sqlite_direct_writes_require_exact_scope_revision_and_link(session, researcher_user):
    project, article = await _project_article(session, researcher_user)
    prediction, payload = await _prediction(session, project, article)
    valid = {
        "id": uuid.uuid4(),
        "prediction_id": prediction.id,
        "project_id": project.id,
        "article_id": article.id,
        "graph_revision_ref": uuid.UUID(payload["graph_revision_ref"]),
        "answer_ref": uuid.UUID(payload["answer_ref"]),
        "evidence_ref": uuid.UUID(payload["evidence_ref"]),
        "actor_id": researcher_user.id,
        "actor_ref": anonymized_actor_ref(researcher_user.email),
        "status": "supports",
        "rationale": "Inspected context.",
        "protocol_version": "seip-support-v1",
    }
    for forged in (
        {"project_id": uuid.uuid4()},
        {"article_id": uuid.uuid4()},
        {"graph_revision_ref": uuid.uuid4()},
        {"answer_ref": uuid.uuid4()},
        {"evidence_ref": uuid.uuid4()},
        {"actor_id": None},
    ):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    insert(EvidenceSupportAssessment).values(valid | forged | {"id": uuid.uuid4()})
                )

    await session.execute(insert(EvidenceSupportAssessment).values(**valid))
    await session.commit()
    for changed in (
        {"status": "contradicts"},
        {"rationale": "Changed after review."},
        {"actor_ref": "a" * 64},
        {"answer_ref": uuid.uuid4()},
        {"actor_id": None, "status": "unclear"},
    ):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    update(EvidenceSupportAssessment)
                    .where(EvidenceSupportAssessment.id == valid["id"])
                    .values(**changed)
                )
    await session.execute(
        update(EvidenceSupportAssessment)
        .where(EvidenceSupportAssessment.id == valid["id"])
        .values(actor_id=None)
    )
    await session.commit()
    retained = await session.get(EvidenceSupportAssessment, valid["id"])
    assert retained.actor_id is None and retained.actor_ref == valid["actor_ref"]
