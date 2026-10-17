"""Tests des routes d'annotation (app/api/v1/annotations.py) — MODULE 5, 100 % offline.

Endpoints du viewer d'annotation :
- GET  /projects/{project_id}/articles/{article_id}            (article seul + methods_text)
- GET  /projects/{project_id}/articles/{article_id}/prediction (prédiction de l'article)
- POST /projects/{project_id}/articles/{article_id}/annotation (soumission annotation)
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings
from app.db.models import (
    Article,
    ArticlePrediction,
    HumanAnnotation,
    Project,
    ProjectMember,
    ProjectMemberRole,
)

VALID_ANNOTATION = {
    "use_ai_behavior": True,
    "animal_sex": "BOTH_SEXES",
    "confidence_score": 0.9,
    "evidence_quotes": ["mice were randomized"],
    "reasoning_summary": "ok",
}


async def _make_project_with_article(session, user) -> tuple[Project, Article]:
    project = Project(title="P", created_by=user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    article = Article(
        project_id=project.id,
        pmid="111",
        title="A study",
        methods_text="Mice were randomized to groups.",
    )
    session.add(article)
    await session.commit()
    return project, article


async def _make_prediction(session, article) -> ArticlePrediction:
    p = ArticlePrediction(
        article_id=article.id,
        model_used="qwen3.8",
        decision_json={
            "use_ai_behavior": True,
            "animal_sex": "BOTH_SEXES",
            "confidence_score": 0.9,
            "evidence_quotes": ["mice were randomized"],
            "reasoning_summary": "ok",
        },
        confidence_score=0.9,
        evidence_quotes=["mice were randomized"],
    )
    session.add(p)
    await session.commit()
    return p


# --- GET /projects/{id}/articles/{article_id} ---


async def test_get_article_returns_methods_text(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(article.id)
    assert body["pmid"] == "111"
    assert body["title"] == "A study"
    assert body["methods_text"] == "Mice were randomized to groups."
    assert body["source_view_token"]


async def test_review_binds_retained_display_after_source_replacement(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    url = f"/api/v1/projects/{project.id}/articles/{article.id}"
    shown = (await client.get(url)).json()
    article.methods_text = "Different methods and animals."
    await session.commit()
    response = await client.post(
        url + "/annotation",
        json={**VALID_ANNOTATION, "source_view_token": shown["source_view_token"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["decision_json"]["source_binding"] == {
        "state": "view_bound",
        "current_display_text_drift": True,
    }
    ann = await session.get(HumanAnnotation, uuid.UUID(response.json()["id"]))
    assert ann.source_snapshot_id and ann.source_view_id and ann.source_view_sha256


async def test_review_token_rejects_forgery_and_scope_reuse(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    first, article = await _make_project_with_article(session, researcher_user)
    second, other = await _make_project_with_article(session, researcher_user)
    first_url = f"/api/v1/projects/{first.id}/articles/{article.id}"
    token = (await client.get(first_url)).json()["source_view_token"]
    other_url = f"/api/v1/projects/{second.id}/articles/{other.id}/annotation"
    for candidate in (token, token[:-1] + ("a" if token[-1] != "a" else "b")):
        response = await client.post(
            other_url, json={**VALID_ANNOTATION, "source_view_token": candidate}
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "invalid source view token"


async def test_old_client_review_is_explicitly_unversioned(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    response = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        json=VALID_ANNOTATION,
    )
    assert response.status_code == 201
    assert response.json()["decision_json"]["source_binding"] == {
        "state": "legacy_unversioned",
        "current_display_text_drift": None,
    }


async def test_blind_second_submission_cannot_replace_first_judgment(
    client, session, researcher_user, do_login
):
    from app.db.models import Search

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    blind = Search(
        project_id=project.id,
        name="Blind",
        kind="blind",
        question="Q",
        lot_all=True,
    )
    session.add(blind)
    await session.commit()
    url = f"/api/v1/projects/{project.id}/articles/{article.id}"
    token = (await client.get(url + f"?search={blind.id}")).json()["source_view_token"]
    body = {**VALID_ANNOTATION, "source_view_token": token}
    assert (await client.post(url + f"/annotation?search={blind.id}", json=body)).status_code == 201
    repeat = await client.post(url + f"/annotation?search={blind.id}", json=body)
    assert repeat.status_code == 409


async def test_blind_requires_source_token_but_standard_legacy_remains_allowed(
    client, session, researcher_user, do_login
):
    from app.db.models import Search

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    blind = Search(project_id=project.id, name="Blind", kind="blind", question="Q", lot_all=True)
    session.add(blind)
    await session.commit()
    url = f"/api/v1/projects/{project.id}/articles/{article.id}/annotation"
    response = await client.post(url + f"?search={blind.id}", json=VALID_ANNOTATION)
    assert response.status_code == 422
    assert response.json()["detail"] == "source view token required for blind review"
    legacy = await client.post(url, json=VALID_ANNOTATION)
    assert legacy.status_code == 201
    assert legacy.json()["decision_json"]["source_binding"]["state"] == "legacy_unversioned"


async def test_blind_review_rejects_article_outside_search_lot(
    client, session, researcher_user, do_login
):
    from app.db.models import Search

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    blind = Search(project_id=project.id, name="Frozen", kind="blind", question="Q", lot_all=False)
    session.add(blind)
    await session.commit()
    url = f"/api/v1/projects/{project.id}/articles/{article.id}"
    assert (await client.get(url + f"?search={blind.id}")).status_code == 404
    assert (
        await client.post(url + f"/annotation?search={blind.id}", json=VALID_ANNOTATION)
    ).status_code == 404


async def test_review_token_rejects_changed_search_definition(
    client, session, researcher_user, do_login
):
    from app.db.models import Search

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    blind = Search(project_id=project.id, name="Blind", kind="blind", question="Q1", lot_all=True)
    session.add(blind)
    await session.commit()
    url = f"/api/v1/projects/{project.id}/articles/{article.id}"
    token = (await client.get(url + f"?search={blind.id}")).json()["source_view_token"]
    blind.question = "Q2"
    await session.commit()
    response = await client.post(
        url + f"/annotation?search={blind.id}",
        json={**VALID_ANNOTATION, "source_view_token": token},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid source view token"


async def test_blind_query_on_standard_search_cannot_spoof_blind_record(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    url = f"/api/v1/projects/{project.id}/articles/{article.id}/annotation?blind=true"
    first = await client.post(url, json=VALID_ANNOTATION)
    second = await client.post(url, json=VALID_ANNOTATION)
    assert first.status_code == second.status_code == 201
    ann = await session.get(HumanAnnotation, uuid.UUID(first.json()["id"]))
    assert ann.blind is False


async def test_review_token_is_user_search_and_expiry_scoped(
    client, session, researcher_user, do_login
):
    from app.db.models import Search, UserRole
    from tests.conftest import _make_user

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    other_search = Search(
        project_id=project.id,
        name="Other",
        kind="standard",
        question="Q",
        lot_all=True,
    )
    member = await _make_user(
        session, "second-reviewer@lab.fr", UserRole.RESEARCHER, "member-pass-123", True
    )
    session.add_all(
        [
            other_search,
            ProjectMember(
                project_id=project.id,
                user_id=member.id,
                role_in_project=ProjectMemberRole.ANNOTATOR,
            ),
        ]
    )
    await session.commit()
    article_url = f"/api/v1/projects/{project.id}/articles/{article.id}"
    token = (await client.get(article_url)).json()["source_view_token"]
    payload = {**VALID_ANNOTATION, "source_view_token": token}
    wrong_search = await client.post(
        article_url + f"/annotation?search={other_search.id}", json=payload
    )
    assert wrong_search.status_code == 422
    await do_login("second-reviewer@lab.fr", "member-pass-123", consent=True)
    wrong_user = await client.post(article_url + "/annotation", json=payload)
    assert wrong_user.status_code == 422
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    claims = jwt.decode(
        token, get_settings().jwt_secret, algorithms=["HS256"], options={"verify_exp": False}
    )
    claims["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    expired = jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")
    response = await client.post(
        article_url + "/annotation", json={**payload, "source_view_token": expired}
    )
    assert response.status_code == 422


async def test_get_article_404_not_found(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_get_article_404_foreign_project(client, session, researcher_user, do_login):
    """Article d'un autre projet → 404 (pas de fuite inter-projets)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    other = Project(title="Other", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign = Article(project_id=other.id, pmid="222", title="Foreign")
    session.add(foreign)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{foreign.id}")
    assert resp.status_code == 404


async def test_get_article_403_not_member(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    # Projet dont le researcher n'est pas membre.
    other = Project(title="Foreign", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign = Article(project_id=other.id, pmid="333", title="Foreign")
    session.add(foreign)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{other.id}/articles/{foreign.id}")
    assert resp.status_code == 403


async def test_get_article_401_no_auth(client, session, researcher_user):
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}")
    assert resp.status_code == 401


# --- GET /projects/{id}/articles/{article_id}/prediction ---


async def test_get_prediction_returns_prediction(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    p = await _make_prediction(session, article)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(p.id)
    assert body["model_used"] == "qwen3.8"
    assert body["confidence_score"] == 0.9
    assert body["evidence_quotes"] == ["mice were randomized"]
    assert body["quote_checks"] is None  # historical prediction: no captured-input check
    assert body["evidence_graph"] is None
    assert body["status"] == "NEEDS_HUMAN_REVIEW"


async def test_get_prediction_returns_latest(client, session, researcher_user, do_login):
    """Plusieurs prédictions → la plus récente (created_at) est renvoyée."""
    from datetime import datetime

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    old = ArticlePrediction(
        article_id=article.id,
        model_used="old-model",
        confidence_score=0.5,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    new = ArticlePrediction(
        article_id=article.id,
        model_used="new-model",
        confidence_score=0.9,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.add_all([old, new])
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 200
    assert resp.json()["model_used"] == "new-model"


async def test_get_prediction_nulls_last(client, session, researcher_user, do_login):
    """Prédiction ancienne (created_at NULL) + récente → la récente est renvoyée.

    Garantit un comportement cohérent SQLite/PostgreSQL (NULLS LAST en DESC).
    """
    from datetime import UTC, datetime

    from sqlalchemy import text

    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    # Ancienne prédiction (created_at NULL, comme les lignes pré-migration).
    # Le server_default (CURRENT_TIMESTAMP) remplirait la colonne via l'ORM ;
    # on la remet à NULL par UPDATE SQL après commit.
    old = ArticlePrediction(article_id=article.id, model_used="legacy-model")
    new = ArticlePrediction(
        article_id=article.id,
        model_used="new-model",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.add_all([old, new])
    await session.commit()
    # Le format stocké en SQLite est sans tirets (32 hex) → on utilise .hex.
    await session.execute(
        text("UPDATE article_predictions SET created_at = NULL WHERE id = :pid"),
        {"pid": old.id.hex},
    )
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 200
    assert resp.json()["model_used"] == "new-model"


async def test_get_prediction_null_when_absent(client, session, researcher_user, do_login):
    """Pas de prédiction → 200 avec null (le viewer pré-remplit à vide)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 200
    assert resp.json() is None


async def test_get_prediction_404_not_found(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{uuid.uuid4()}/prediction")
    assert resp.status_code == 404


async def test_get_prediction_403_not_member(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    other = Project(title="Foreign", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign = Article(project_id=other.id, pmid="444", title="Foreign")
    session.add(foreign)
    await session.commit()
    resp = await client.get(f"/api/v1/projects/{other.id}/articles/{foreign.id}/prediction")
    assert resp.status_code == 403


async def test_get_prediction_401_no_auth(client, session, researcher_user):
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/prediction")
    assert resp.status_code == 401


# --- POST /projects/{id}/articles/{article_id}/annotation ---


async def test_create_annotation(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["article_id"] == str(article.id)
    assert body["user_id"] == str(researcher_user.id)
    assert body["decision_json"]["use_ai_behavior"] is True
    assert body["decision_json"]["animal_sex"] == "BOTH_SEXES"
    assert body["decision_json"]["confidence_score"] == 0.9
    assert body["decision_json"]["evidence_quotes"] == ["mice were randomized"]
    # Persistance en base.
    ann = await session.get(HumanAnnotation, uuid.UUID(body["id"]))
    assert ann is not None
    assert ann.user_id == researcher_user.id


async def test_create_annotation_stores_reasoning_in_notes(
    client, session, researcher_user, do_login
):
    """reasoning_summary est conservé dans ``notes`` (colonne Text dédiée)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 201
    ann = await session.get(HumanAnnotation, uuid.UUID(resp.json()["id"]))
    assert ann.notes == "ok"


async def test_create_annotation_422_confidence_out_of_range(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    payload = {**VALID_ANNOTATION, "confidence_score": 1.5}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation", json=payload
    )
    assert resp.status_code == 422


async def test_create_annotation_422_confidence_negative(
    client, session, researcher_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    payload = {**VALID_ANNOTATION, "confidence_score": -0.1}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation", json=payload
    )
    assert resp.status_code == 422


async def test_create_annotation_422_invalid_animal_sex(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    payload = {**VALID_ANNOTATION, "animal_sex": "ANDROGYNE"}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation", json=payload
    )
    assert resp.status_code == 422


async def test_create_annotation_422_missing_key(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _make_project_with_article(session, researcher_user)
    payload = {k: v for k, v in VALID_ANNOTATION.items() if k != "use_ai_behavior"}
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation", json=payload
    )
    assert resp.status_code == 422


async def test_create_annotation_404_not_found(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, _ = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{uuid.uuid4()}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 404


async def test_create_annotation_403_not_member(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    other = Project(title="Foreign", created_by=researcher_user.id)
    session.add(other)
    await session.commit()
    foreign = Article(project_id=other.id, pmid="555", title="Foreign")
    session.add(foreign)
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{other.id}/articles/{foreign.id}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 403


async def test_create_annotation_401_no_auth(client, session, researcher_user):
    project, article = await _make_project_with_article(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/annotation",
        json=VALID_ANNOTATION,
    )
    assert resp.status_code == 401
