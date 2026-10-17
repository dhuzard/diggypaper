"""One reviewer pseudonym policy across annotations, support assessments and gold.

Merge audit finding F-4: the crate's annotation ``reviewer_ref`` was project-scoped
while assessment ``actor_ref`` and gold actor refs exposed the global salted digest.
Every exported or API-exposed reference now derives from the stored digest with
``app.pseudonym.reviewer_reference``; the stored digest itself stays the deletion anchor.
The crate-side assessment ``actor_ref`` derivation is covered by
``tests/unit/test_evidence_crate.py``; crate survival of account deletion by
``tests/test_crate_human_evidence.py``.
"""

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.v1.export import write_crate
from app.cleanup import delete_user
from app.db.models import (
    Article,
    EvidenceSupportAssessment,
    GoldRevision,
    HumanAnnotation,
    Project,
    ProjectMember,
    ProjectMemberRole,
)
from app.pseudonym import anonymized_actor_ref, reviewer_reference
from tests.test_gold_api import VALID_GT
from tests.test_support_assessments_api import _prediction, _url


async def _lead_project(session, user, title, pmid):
    project = Project(title=title, created_by=user.id)
    session.add(project)
    await session.flush()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    article = Article(project_id=project.id, pmid=pmid, title="Mice")
    session.add(article)
    await session.flush()
    session.add(
        HumanAnnotation(
            article_id=article.id,
            user_id=user.id,
            decision_json={**VALID_GT, "review_status": "MANUAL"},
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    )
    await session.commit()
    return project, article


async def _crate_reviewer_ref(session, project, pmid):
    buf = io.BytesIO()
    await write_crate(session, project, "exporter@example.org", buf)
    with zipfile.ZipFile(buf) as archive:
        record = json.loads(archive.read(f"articles/pmid-{pmid}/metadata.json"))
    return record["human"][0]["reviewer_ref"]


async def _api_refs(client, project, article, prediction):
    assessments = (await client.get(_url(project, article, prediction))).json()["assessments"]
    gold = (await client.get(f"/api/v1/projects/{project.id}/gold")).json()["items"]
    revisions = gold[0]["revisions"]
    return {
        "assessment": {row["actor_ref"] for row in assessments},
        "review_actor_ref": {r["review_actor_ref"] for r in revisions},
        "recorded_by_ref": {r["recorded_by_ref"] for r in revisions},
    }


async def test_same_reviewer_has_one_project_scoped_ref_across_annotation_assessment_and_gold(
    client, session, researcher_user, admin_user, do_login
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project, article = await _lead_project(session, researcher_user, "Primary", "4242")
    other, other_article = await _lead_project(session, researcher_user, "Unrelated", "4343")
    digest = anonymized_actor_ref(researcher_user.email)
    expected = reviewer_reference(project.id, digest)
    other_expected = reviewer_reference(other.id, digest)
    assert expected != other_expected
    # Annotation reference as exported in the crate (metadata.json ``human[].reviewer_ref``).
    assert await _crate_reviewer_ref(session, project, "4242") == expected
    assert await _crate_reviewer_ref(session, other, "4343") == other_expected

    prediction, payload = await _prediction(session, project, article)
    other_prediction, other_payload = await _prediction(session, other, other_article)
    created = await client.post(_url(project, article, prediction), json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["actor_ref"] == expected
    derived = await client.post(f"/api/v1/projects/{project.id}/gold/from-reviews", json={})
    assert derived.status_code == 201 and derived.json()["added"] == 1, derived.text
    assert (
        await client.post(_url(other, other_article, other_prediction), json=other_payload)
    ).status_code == 201
    assert (await client.post(f"/api/v1/projects/{other.id}/gold/from-reviews", json={})).json()[
        "added"
    ] == 1

    # (a) one person, one reference: assessment and gold agree with the annotation ref.
    refs = await _api_refs(client, project, article, prediction)
    assert refs == {
        "assessment": {expected},
        "review_actor_ref": {expected},
        "recorded_by_ref": {expected},
    }
    # (b) an unrelated project yields an unrelated reference for the same person.
    other_refs = await _api_refs(client, other, other_article, other_prediction)
    assert other_refs == {
        "assessment": {other_expected},
        "review_actor_ref": {other_expected},
        "recorded_by_ref": {other_expected},
    }
    # The stored global digest is the only persisted value and is never exposed.
    assert digest not in json.dumps([refs, other_refs], default=sorted)
    assessment = await session.get(EvidenceSupportAssessment, uuid.UUID(created.json()["id"]))
    revision = await session.scalar(select(GoldRevision).where(GoldRevision.sequence == 1).limit(1))
    assert assessment.actor_ref == revision.recorded_by_ref == revision.review_actor_ref == digest

    # (c) the references survive account deletion: rows keep the digest, derivations hold.
    await delete_user(session, researcher_user)
    await session.commit()
    await do_login("admin@lab.fr", "admin-pass-123", consent=True)
    assert await _api_refs(client, project, article, prediction) == refs
    assert await _api_refs(client, other, other_article, other_prediction) == other_refs
