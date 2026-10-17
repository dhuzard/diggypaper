"""Human support judgments on exact, immutable prediction evidence links."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.annotations import _require_article, _scope
from app.db.models import (
    Article,
    ArticlePrediction,
    EvidenceSupportAssessment,
    HumanAnnotation,
    User,
)
from app.evidence_links import EvidenceEnvelopeV1
from app.pseudonym import anonymized_actor_ref, reviewer_reference
from app.searches import in_search

router = APIRouter(prefix="/projects", tags=["support assessments"])
DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]
PROTOCOL_VERSION = "seip-support-v1"


class AssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_revision_ref: uuid.UUID
    answer_ref: uuid.UUID
    evidence_ref: uuid.UUID
    status: Literal["supports", "contradicts", "insufficient", "unclear", "not_assessed"]
    rationale: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def require_reason(self):
        if self.status != "not_assessed" and not self.rationale.strip():
            raise ValueError("a rationale is required for a support judgment")
        return self


def _payload(row: EvidenceSupportAssessment) -> dict:
    """The stored ``actor_ref`` is the durable global digest; expose only its
    project-scoped derivation (the same one annotations and gold use)."""
    return {
        "id": str(row.id),
        "prediction_id": str(row.prediction_id),
        "graph_revision_ref": str(row.graph_revision_ref),
        "answer_ref": str(row.answer_ref),
        "evidence_ref": str(row.evidence_ref),
        "actor_ref": reviewer_reference(row.project_id, row.actor_ref),
        "status": row.status,
        "rationale": row.rationale,
        "protocol_version": row.protocol_version,
        "created_at": row.created_at.isoformat(),
    }


async def _visible_prediction(
    db: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    prediction_id: uuid.UUID,
    search: uuid.UUID | None,
    *,
    lock_article: bool = False,
):
    article = await _require_article(db, project_id, article_id, user)
    scope = await _scope(db, project_id, search)
    if lock_article:
        # A concurrent new prediction takes a FK key-share lock on this row.
        await db.execute(select(Article.id).where(Article.id == article.id).with_for_update())
    if scope.kind == "blind":
        mine = await db.scalar(
            select(HumanAnnotation.id)
            .where(
                HumanAnnotation.article_id == article.id,
                HumanAnnotation.user_id == user.id,
                in_search(HumanAnnotation.search_id, scope),
            )
            .limit(1)
        )
        if mine is None:
            raise HTTPException(
                status_code=404, detail="Prediction not available before own review"
            )
    prediction = await db.scalar(
        select(ArticlePrediction).where(
            ArticlePrediction.id == prediction_id,
            ArticlePrediction.article_id == article.id,
            in_search(ArticlePrediction.search_id, scope),
        )
    )
    if prediction is None:
        raise HTTPException(status_code=404, detail="Prediction not found")
    latest_id = await db.scalar(
        select(ArticlePrediction.id)
        .where(
            ArticlePrediction.article_id == article.id,
            in_search(ArticlePrediction.search_id, scope),
        )
        .order_by(ArticlePrediction.created_at.desc().nulls_last(), ArticlePrediction.id.desc())
        .limit(1)
    )
    return prediction, scope, latest_id == prediction.id


@router.get("/{project_id}/articles/{article_id}/predictions/{prediction_id}/support-assessments")
async def list_support_assessments(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    prediction_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Read human judgments; absent/legacy graphs remain not assessed.

    Blind searches reveal only the caller's judgments after their own review.
    A past prediction's records remain historical and never transfer to a newer
    answer or evidence revision.
    """
    prediction, scope, current = await _visible_prediction(
        db, user, project_id, article_id, prediction_id, search
    )
    query = select(EvidenceSupportAssessment).where(
        EvidenceSupportAssessment.prediction_id == prediction.id,
        EvidenceSupportAssessment.project_id == project_id,
        EvidenceSupportAssessment.article_id == article_id,
    )
    if scope.kind == "blind":
        query = query.where(EvidenceSupportAssessment.actor_id == user.id)
    rows = (
        (
            await db.execute(
                query.order_by(EvidenceSupportAssessment.created_at, EvidenceSupportAssessment.id)
            )
        )
        .scalars()
        .all()
    )
    graph = prediction.evidence_graph_json
    revision = graph["envelope"]["scope"]["revision_ref"] if graph else None
    return {
        "prediction_id": str(prediction.id),
        "graph_revision_ref": revision,
        "current": current,
        "state": (
            "assessed" if any(row.status != "not_assessed" for row in rows) else "not_assessed"
        ),
        "assessments": [_payload(row) for row in rows],
    }


@router.post(
    "/{project_id}/articles/{article_id}/predictions/{prediction_id}/support-assessments",
    status_code=201,
)
async def create_support_assessment(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    prediction_id: uuid.UUID,
    payload: AssessmentCreate,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Append a reviewer judgment; no match status or answer agreement supplies one."""
    prediction, _, current = await _visible_prediction(
        db, user, project_id, article_id, prediction_id, search, lock_article=True
    )
    if not current:
        raise HTTPException(status_code=409, detail="Prediction revision is no longer current")
    graph = prediction.evidence_graph_json
    if not graph:
        raise HTTPException(status_code=409, detail="Prediction has no versioned evidence graph")
    envelope = EvidenceEnvelopeV1.model_validate(graph["envelope"])
    if envelope.state != "structured" or not envelope.links:
        raise HTTPException(status_code=409, detail="Prediction has no assessable answer links")
    if (
        envelope.scope.project_ref != str(project_id)
        or envelope.scope.article_ref != str(article_id)
        or envelope.scope.record_kind != "prediction"
        or envelope.scope.record_ref != str(prediction_id)
        or envelope.scope.revision_ref != str(prediction_id)
        or envelope.scope.revision_ref != str(payload.graph_revision_ref)
    ):
        raise HTTPException(status_code=409, detail="Evidence graph revision mismatch")
    # Pydantic checks record shape, not graph referential integrity. Stored
    # JSON may predate or bypass the producer's validation; reject ambiguous,
    # dangling, duplicate, or legacy links before assigning a judgment.
    answers = {item.answer_ref: item for item in envelope.answers}
    evidence = {item.evidence_ref: item for item in envelope.evidence}
    pairs = [(link.answer_ref, link.evidence_ref) for link in envelope.links]
    if (
        len(answers) != len(envelope.answers)
        or len(evidence) != len(envelope.evidence)
        or len(set(pairs)) != len(pairs)
        or any(
            answer_ref not in answers
            or evidence_ref not in evidence
            or evidence[evidence_ref].origin != "captured_input_context"
            for answer_ref, evidence_ref in pairs
        )
    ):
        raise HTTPException(status_code=409, detail="Evidence graph has unassessable links")
    pair = (str(payload.answer_ref), str(payload.evidence_ref))
    if pair not in set(pairs):
        raise HTTPException(status_code=422, detail="Answer/evidence link not in this revision")
    row = EvidenceSupportAssessment(
        prediction_id=prediction.id,
        project_id=project_id,
        article_id=article_id,
        graph_revision_ref=payload.graph_revision_ref,
        answer_ref=payload.answer_ref,
        evidence_ref=payload.evidence_ref,
        actor_id=user.id,
        actor_ref=anonymized_actor_ref(user.email),
        status=payload.status,
        rationale=payload.rationale.strip(),
        protocol_version=PROTOCOL_VERSION,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _payload(row)
