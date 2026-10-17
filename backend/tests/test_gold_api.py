"""Tests des routes Gold Dataset (app/api/v1/gold.py) — 100 % offline."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Article,
    GoldArticle,
    GoldRevision,
    HumanAnnotation,
    Project,
    ProjectMember,
    ProjectMemberRole,
    Search,
)
from app.provenance import review_binding
from app.provenance_capture import capture_article_source
from app.pseudonym import anonymized_actor_ref
from tests.test_support_assessments_api import _prediction, _url

VALID_GT = {
    "use_ai_behavior": True,
    "animal_sex": "BOTH_SEXES",
    "confidence_score": 0.9,
    "evidence_quotes": ["mice were randomized"],
    "reasoning_summary": "ok",
}


async def _make_project_with_article(session, lead_user) -> tuple[Project, Article]:
    project = Project(title="P", created_by=lead_user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=lead_user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    article = Article(
        project_id=project.id, pmid="111", title="A study", methods_text="Methods here."
    )
    session.add(article)
    await session.commit()
    return project, article


async def test_create_gold_lead(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(article.id), "ground_truth_json": VALID_GT},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["article_title"] == "A study"
    assert body["ground_truth_json"]["use_ai_behavior"] is True
    assert body["qualification"] == "unqualified"
    assert len(body["revisions"]) == 1
    assert body["staleness"] == {
        "source": "unknown",
        "evidence": "unknown",
        "answer": "unknown",
    }
    reviewed = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        json={**VALID_GT, "use_ai_behavior": False},
    )
    assert reviewed.status_code == 201
    later = (await client.get(f"/api/v1/projects/{project.id}/gold")).json()["items"][0]
    assert set(later["staleness"].values()) == {"unknown"}


async def test_list_gold(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(article.id), "ground_truth_json": VALID_GT},
    )
    resp = await client.get(f"/api/v1/projects/{project.id}/gold")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


async def test_create_gold_missing_key_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    bad_gt = {k: v for k, v in VALID_GT.items() if k != "animal_sex"}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(article.id), "ground_truth_json": bad_gt},
    )
    assert resp.status_code == 422


async def test_create_gold_article_not_in_project_422(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    # Article d'un autre projet.
    other = Project(title="Other", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign_article = Article(project_id=other.id, pmid="222", title="Foreign")
    session.add(foreign_article)
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(foreign_article.id), "ground_truth_json": VALID_GT},
    )
    assert resp.status_code == 422


async def test_delete_gold_lead(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    gid = (
        await client.post(
            f"/api/v1/projects/{project.id}/gold",
            json={"article_id": str(article.id), "ground_truth_json": VALID_GT},
        )
    ).json()["id"]
    resp = await client.delete(f"/api/v1/projects/{project.id}/gold/{gid}")
    assert resp.status_code == 204
    assert (await client.get(f"/api/v1/projects/{project.id}/gold")).json()["items"] == []


async def test_delete_gold_404(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    resp = await client.delete(
        f"/api/v1/projects/{project.id}/gold/00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404


async def _bound_review(session, project, article, user, *, methods, truth, created_at):
    article.methods_text = methods
    await session.commit()
    snapshot, view_id = await capture_article_source(
        session,
        project_id=project.id,
        article_id=article.id,
        text_sections=["methods"],
    )
    ann = HumanAnnotation(
        article_id=article.id,
        user_id=user.id,
        annotator_hash=anonymized_actor_ref(user.email),
        decision_json={**truth, "review_status": "MANUAL"},
        created_at=created_at,
        **review_binding(snapshot, view_id),
    )
    session.add(ann)
    await session.commit()
    return ann


async def test_gold_revisions_separate_source_quote_and_answer_staleness(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    base = f"/api/v1/projects/{project.id}/gold"
    first_at = datetime.now(UTC) - timedelta(days=1)
    first = await _bound_review(
        session,
        project,
        article,
        researcher_user,
        methods="Methods A.",
        truth=VALID_GT,
        created_at=first_at,
    )
    assert (await client.post(f"{base}/from-reviews", json={})).json()["added"] == 1
    initial = (await client.get(base)).json()["items"][0]
    assert initial["revisions"][0]["source_annotation_ref"] == str(first.id)
    assert initial["revisions"][0]["source_view_sha256"]
    assert initial["revisions"][0]["assessment_refs"] == []
    assert initial["qualification"] == "unqualified"
    await _bound_review(
        session,
        project,
        article,
        researcher_user,
        methods="Methods B.",
        truth=VALID_GT,
        created_at=first_at + timedelta(hours=1),
    )
    source_only = (await client.get(base)).json()["items"][0]
    assert source_only["staleness"] == {
        "source": "changed",
        "evidence": "unchanged",
        "answer": "unchanged",
    }
    assert source_only["stale"] is True
    refreshed = await client.post(f"{base}/from-reviews", json={"refresh": True})
    assert refreshed.json()["updated"] == 1
    second = (await client.get(base)).json()["items"][0]
    assert [r["sequence"] for r in second["revisions"]] == [1, 2]
    assert second["revisions"][0] == initial["revisions"][0]
    assert second["staleness"] == {
        "source": "unchanged",
        "evidence": "unchanged",
        "answer": "unchanged",
    }
    quoted = {**VALID_GT, "evidence_quotes": ["Different quote."]}
    # New review uses the same retained view, so only evidence changes.
    prior = await session.get(HumanAnnotation, uuid.UUID(second["source"]["annotation_id"]))
    session.add(
        HumanAnnotation(
            article_id=article.id,
            user_id=researcher_user.id,
            annotator_hash=anonymized_actor_ref(researcher_user.email),
            decision_json={**quoted, "review_status": "MANUAL"},
            created_at=first_at + timedelta(hours=2),
            provenance_project_id=project.id,
            source_snapshot_id=prior.source_snapshot_id,
            source_view_id=prior.source_view_id,
            source_view_sha256=prior.source_view_sha256,
        )
    )
    await session.commit()
    quote_only = (await client.get(base)).json()["items"][0]
    assert quote_only["staleness"] == {
        "source": "unchanged",
        "evidence": "changed",
        "answer": "unchanged",
    }
    await client.post(f"{base}/from-reviews", json={"refresh": True})
    answered = {**quoted, "use_ai_behavior": False}
    session.add(
        HumanAnnotation(
            article_id=article.id,
            user_id=researcher_user.id,
            annotator_hash=anonymized_actor_ref(researcher_user.email),
            decision_json={**answered, "review_status": "MANUAL"},
            created_at=first_at + timedelta(hours=3),
            provenance_project_id=project.id,
            source_snapshot_id=prior.source_snapshot_id,
            source_view_id=prior.source_view_id,
            source_view_sha256=prior.source_view_sha256,
        )
    )
    await session.commit()
    answer_only = (await client.get(base)).json()["items"][0]
    assert answer_only["staleness"] == {
        "source": "unchanged",
        "evidence": "unchanged",
        "answer": "changed",
    }
    assert [r["sequence"] for r in answer_only["revisions"]] == [1, 2, 3]
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                update(GoldRevision)
                .where(GoldRevision.id == uuid.UUID(initial["revisions"][0]["id"]))
                .values(provenance_state="legacy_unqualified")
            )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                delete(GoldRevision).where(
                    GoldRevision.id == uuid.UUID(initial["revisions"][0]["id"])
                )
            )
    gold_id = uuid.UUID(answer_only["id"])
    assert (await client.delete(f"{base}/{gold_id}")).status_code == 204
    assert (
        await session.execute(select(GoldRevision).where(GoldRevision.gold_id == gold_id))
    ).first() is None


async def test_legacy_gold_is_explicitly_unqualified_then_preserved_on_refresh(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    gold = GoldArticle(
        project_id=project.id,
        article_id=article.id,
        ground_truth_json=VALID_GT,
        added_by=researcher_user.id,
    )
    session.add(gold)
    await session.commit()
    base = f"/api/v1/projects/{project.id}/gold"
    before = (await client.get(base)).json()["items"][0]
    assert before["qualification"] == "legacy_unqualified"
    assert before["revisions"] == []
    assert set(before["staleness"].values()) == {"unknown"}
    await _bound_review(
        session,
        project,
        article,
        researcher_user,
        methods="Methods after legacy.",
        truth=VALID_GT,
        created_at=datetime.now(UTC),
    )
    refreshed = await client.post(f"{base}/from-reviews", json={"refresh": True})
    assert refreshed.json()["updated"] == 1
    after = (await client.get(base)).json()["items"][0]
    assert [r["qualification"] for r in after["revisions"]] == ["legacy_unqualified", "unqualified"]
    assert after["revisions"][0]["ground_truth_json"] == VALID_GT


async def test_manual_gold_preserves_only_explicit_scoped_assessment_refs(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    prediction, payload = await _prediction(session, project, article)
    assessment = await client.post(_url(project, article, prediction), json=payload)
    assert assessment.status_code == 201
    ref = assessment.json()["id"]
    base = f"/api/v1/projects/{project.id}/gold"
    invalid = await client.post(
        base,
        json={
            "article_id": str(article.id),
            "ground_truth_json": VALID_GT,
            "assessment_refs": [str(uuid.uuid4())],
        },
    )
    assert invalid.status_code == 422
    created = await client.post(
        base,
        json={
            "article_id": str(article.id),
            "ground_truth_json": VALID_GT,
            "assessment_refs": [ref],
        },
    )
    assert created.status_code == 201
    assert created.json()["revisions"][0]["assessment_refs"] == [ref]
    assert created.json()["qualification"] == "unqualified"


async def test_blind_gold_hides_other_reviewers_revisions(
    client, session, researcher_user, admin_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    blind = Search(project_id=project.id, name="Blind", kind="blind", lot_all=True)
    session.add(blind)
    await session.commit()
    base = f"/api/v1/projects/{project.id}/gold"
    params = {"search": str(blind.id)}
    first = HumanAnnotation(
        article_id=article.id,
        search_id=blind.id,
        user_id=researcher_user.id,
        blind=True,
        decision_json={**VALID_GT, "review_status": "MANUAL"},
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    session.add(first)
    await session.commit()
    assert (await client.post(f"{base}/from-reviews", params=params, json={})).json()["added"] == 1
    second = HumanAnnotation(
        article_id=article.id,
        search_id=blind.id,
        user_id=admin_user.id,
        blind=True,
        decision_json={**VALID_GT, "review_status": "MANUAL"},
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    session.add(second)
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    assert (await client.get(base, params=params)).json()["items"] == []
    assert (
        await client.post(f"{base}/from-reviews", params=params, json={"refresh": True})
    ).json()["updated"] == 1
    own = (await client.get(base, params=params)).json()["items"][0]
    assert [r["source_annotation_ref"] for r in own["revisions"]] == [str(second.id)]
    assert own["added_by"] is None
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    assert (await client.get(base, params=params)).json()["items"] == []


async def test_gold_revision_keeps_actor_pseudonym_after_account_anonymization(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    created = await client.post(
        f"/api/v1/projects/{project.id}/gold",
        json={"article_id": str(article.id), "ground_truth_json": VALID_GT},
    )
    revision = uuid.UUID(created.json()["revisions"][0]["id"])
    prior_ref = created.json()["revisions"][0]["recorded_by_ref"]
    assert prior_ref == anonymized_actor_ref(researcher_user.email)
    assert (await client.delete("/api/v1/users/me/anonymize")).status_code == 204
    retained = await session.get(GoldRevision, revision)
    await session.refresh(retained)
    assert retained.recorded_by_ref == prior_ref
