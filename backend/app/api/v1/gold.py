"""Routes Gold Dataset (gold_articles) — Module 3.

Lecture : membre du projet. Écriture : LEAD du projet.
``ground_truth_json`` doit contenir les clés du schéma spec §5.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.projects import _require_project_member, latest_reviews
from app.db.models import (
    Article,
    ArticlePrediction,
    EvidenceSupportAssessment,
    GoldArticle,
    GoldRevision,
    HumanAnnotation,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)
from app.provenance import digest
from app.pseudonym import anonymized_actor_ref
from app.schema import project_fields, validate_decision
from app.searches import get_search, gold_condition, in_search

router = APIRouter(prefix="/projects", tags=["gold"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

# Clés obligatoires du schéma d'annotation (spec §5).
_GROUND_TRUTH_KEYS = {
    "use_ai_behavior",
    "animal_sex",
    "confidence_score",
    "evidence_quotes",
    "reasoning_summary",
}


class GoldCreate(BaseModel):
    article_id: uuid.UUID
    ground_truth_json: dict = Field(min_length=1)
    assessment_refs: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class GoldFromReviews(BaseModel):
    """Ajoute au gold les relectures humaines (dernière relecture de chaque article)."""

    article_ids: list[uuid.UUID] | None = None  # None : tous les articles relus de la recherche
    refresh: bool = False  # remplace la copie des articles déjà au gold par la relecture actuelle
    assessment_refs_by_article: dict[uuid.UUID, list[uuid.UUID]] = Field(default_factory=dict)


async def _require_lead(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    if user.role == UserRole.ADMIN:
        return
    member = await db.get(ProjectMember, (project_id, user.id))
    if member is None or member.role_in_project != ProjectMemberRole.LEAD:
        raise HTTPException(status_code=403, detail="lead required")


def _validate_ground_truth(gt: dict, project=None) -> None:
    if project is not None and project.fields is not None:
        errors = validate_decision(project_fields(project), gt)
        if errors:
            raise HTTPException(status_code=422, detail=f"ground_truth_json: {errors}")
        return
    missing = _GROUND_TRUTH_KEYS - gt.keys()
    if missing:
        raise HTTPException(
            status_code=422, detail=f"ground_truth_json missing keys: {sorted(missing)}"
        )


def _snapshot(ann: HumanAnnotation, fields: list[dict]) -> dict:
    """Copie figée d'une relecture : réponses aux items + pertinence, preuves, raisonnement."""
    d = ann.decision_json or {}
    out = {f["key"]: d.get(f["key"]) for f in fields}
    out["confidence_score"] = d.get("confidence_score", 1.0)
    out["evidence_quotes"] = d.get("evidence_quotes") or []
    out["reasoning_summary"] = d.get("reasoning_summary") or ""
    return out


def _answers_of(gt: dict | None, fields: list[dict]) -> dict:
    return {f["key"]: (gt or {}).get(f["key"]) for f in fields}


def _revision(
    gold: GoldArticle,
    sequence: int,
    fields: list[dict],
    *,
    ann: HumanAnnotation | None,
    review_actor_ref: str | None,
    recorded_by_ref: str | None,
    assessment_refs: list[uuid.UUID] | None = None,
    legacy: bool = False,
) -> GoldRevision:
    """Freeze facts known at copy time; missing links are never inferred."""
    truth = gold.ground_truth_json
    return GoldRevision(
        gold_id=gold.id,
        sequence=sequence,
        ground_truth_json=truth,
        answer_keys_json=[field["key"] for field in fields],
        answer_sha256=digest(_answers_of(truth, fields)),
        evidence_sha256=digest(truth.get("evidence_quotes") or []),
        source_annotation_ref=ann.id if ann else gold.source_annotation_id,
        source_snapshot_ref=ann.source_snapshot_id if ann else None,
        source_view_ref=ann.source_view_id if ann else None,
        source_view_sha256=ann.source_view_sha256 if ann else None,
        review_actor_ref=review_actor_ref,
        review_status=(ann.decision_json or {}).get("review_status") if ann else None,
        review_blind=ann.blind if ann else None,
        review_created_at=ann.created_at if ann else None,
        assessment_refs_json=[str(ref) for ref in assessment_refs or []],
        recorded_by_ref=recorded_by_ref,
        provenance_state="legacy_unqualified" if legacy else "unqualified",
    )


async def _checked_assessment_refs(
    db: AsyncSession,
    refs: list[uuid.UUID],
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    scope,
    user: User,
) -> list[uuid.UUID]:
    if len(refs) != len(set(refs)) or len(refs) > 100:
        raise HTTPException(status_code=422, detail="duplicate or excessive assessment references")
    if not refs:
        return []
    rows = await db.execute(
        select(EvidenceSupportAssessment)
        .join(ArticlePrediction, EvidenceSupportAssessment.prediction_id == ArticlePrediction.id)
        .where(
            EvidenceSupportAssessment.id.in_(refs),
            EvidenceSupportAssessment.project_id == project_id,
            EvidenceSupportAssessment.article_id == article_id,
            in_search(ArticlePrediction.search_id, scope),
        )
    )
    found = rows.scalars().all()
    if len(found) != len(refs) or (
        scope.kind == "blind" and any(row.actor_id != user.id for row in found)
    ):
        raise HTTPException(status_code=422, detail="assessment reference is unavailable in scope")
    return refs


def _dimension(old: str | None, new: str | None) -> str:
    if old is None or new is None:
        return "unknown"
    return "changed" if old != new else "unchanged"


def _staleness(revision: GoldRevision | None, latest: HumanAnnotation | None) -> dict[str, str]:
    if (
        revision is None
        or revision.provenance_state == "legacy_unqualified"
        or revision.source_annotation_ref is None
        or latest is None
    ):
        return {key: "unknown" for key in ("source", "evidence", "answer")}
    return {
        "source": _dimension(revision.source_view_sha256, latest.source_view_sha256),
        "evidence": _dimension(
            revision.evidence_sha256,
            digest((latest.decision_json or {}).get("evidence_quotes") or []),
        ),
        "answer": _dimension(
            revision.answer_sha256,
            digest(
                {key: (latest.decision_json or {}).get(key) for key in revision.answer_keys_json}
            ),
        ),
    }


def _revision_payload(revision: GoldRevision) -> dict:
    return {
        "id": str(revision.id),
        "sequence": revision.sequence,
        "ground_truth_json": revision.ground_truth_json,
        "answer_keys": revision.answer_keys_json,
        "answer_sha256": revision.answer_sha256,
        "evidence_sha256": revision.evidence_sha256,
        "source_annotation_ref": str(revision.source_annotation_ref)
        if revision.source_annotation_ref
        else None,
        "source_snapshot_ref": str(revision.source_snapshot_ref)
        if revision.source_snapshot_ref
        else None,
        "source_view_ref": str(revision.source_view_ref) if revision.source_view_ref else None,
        "source_view_sha256": revision.source_view_sha256,
        "review_actor_ref": revision.review_actor_ref,
        "review_status": revision.review_status,
        "review_blind": revision.review_blind,
        "review_created_at": revision.review_created_at.isoformat()
        if revision.review_created_at
        else None,
        "assessment_refs": revision.assessment_refs_json,
        "recorded_by_ref": revision.recorded_by_ref,
        "qualification": revision.provenance_state,
        "created_at": revision.created_at.isoformat() if revision.created_at else None,
    }


def _payload(
    g: GoldArticle,
    title: str | None,
    ann: HumanAnnotation | None,
    names: dict,
    latest: HumanAnnotation | None,
    revisions: list[GoldRevision],
    blind: bool,
) -> dict:
    """Une ligne du gold : réponses, lien vers sa fiche source, relecteur, obsolescence."""
    d = (ann.decision_json or {}) if ann else {}
    current = revisions[-1] if revisions else None
    staleness = _staleness(current, latest)
    return {
        "id": str(g.id),
        "project_id": str(g.project_id) if g.project_id else None,
        "article_id": str(g.article_id) if g.article_id else None,
        "article_title": title,
        "ground_truth_json": g.ground_truth_json,
        "search_id": str(g.search_id) if g.search_id else None,
        "source": {
            "annotation_id": str(current.source_annotation_ref)
            if current and current.source_annotation_ref
            else str(ann.id),
            "by": names.get(ann.user_id),
            "at": (
                current.review_created_at.isoformat()
                if current and current.review_created_at
                else ann.created_at.isoformat()
                if ann.created_at
                else None
            ),
            "status": current.review_status if current else d.get("review_status"),
            "blind": bool(current.review_blind) if current else bool(ann.blind),
        }
        if ann
        else None,
        "added_by": None if blind else names.get(g.added_by),
        "stale": "changed" in staleness.values(),
        "staleness": staleness,
        "qualification": current.provenance_state if current else "legacy_unqualified",
        "revisions": [_revision_payload(revision) for revision in revisions],
    }


async def _names(db: AsyncSession, user_ids: set) -> dict:
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = await db.execute(select(User).where(User.id.in_(ids)))
    return {u.id: u.full_name or u.email for u in rows.scalars()}


async def _items(db: AsyncSession, project, scope, rows, user: User) -> list[dict]:
    """Lignes du gold + relecture source, relecteurs, dernière relecture (pour « obsolète »)."""
    anns = {}
    ann_ids = [g.source_annotation_id for g, _ in rows if g.source_annotation_id]
    if ann_ids:
        for a in (
            await db.execute(select(HumanAnnotation).where(HumanAnnotation.id.in_(ann_ids)))
        ).scalars():
            anns[a.id] = a
    latest = await latest_reviews(db, [g.article_id for g, _ in rows if g.article_id], scope)
    own_annotation_ids: set[uuid.UUID] | None = None
    if scope.kind == "blind":
        own_rows = await db.execute(
            select(HumanAnnotation)
            .where(
                HumanAnnotation.user_id == user.id,
                in_search(HumanAnnotation.search_id, scope),
            )
            .order_by(HumanAnnotation.created_at, HumanAnnotation.id)
        )
        own_annotations = own_rows.scalars().all()
        own_latest = {ann.article_id: ann for ann in own_annotations}
        own_annotation_ids = {ann.id for ann in own_annotations}
        latest = {article_id: (ann, user) for article_id, ann in own_latest.items()}
    gold_ids = [g.id for g, _ in rows]
    revisions: dict[uuid.UUID, list[GoldRevision]] = {gold_id: [] for gold_id in gold_ids}
    if gold_ids:
        history = await db.execute(
            select(GoldRevision)
            .where(GoldRevision.gold_id.in_(gold_ids))
            .order_by(GoldRevision.gold_id, GoldRevision.sequence)
        )
        for revision in history.scalars():
            if own_annotation_ids is None or revision.source_annotation_ref in own_annotation_ids:
                revisions[revision.gold_id].append(revision)
    names = await _names(
        db,
        {a.user_id for a in anns.values()}
        | ({g.added_by for g, _ in rows} if scope.kind != "blind" else set()),
    )
    return [
        _payload(
            g,
            title,
            anns.get(g.source_annotation_id),
            names,
            (latest.get(g.article_id) or (None, None))[0],
            revisions[g.id],
            scope.kind == "blind",
        )
        for g, title in rows
    ]


@router.post("/{project_id}/gold", status_code=201)
async def create_gold(
    project_id: uuid.UUID,
    payload: GoldCreate,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Saisie manuelle d'une référence (LEAD). Préférer « Ajouter au gold » depuis une relecture."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    scope = await get_search(db, project, search)
    _validate_ground_truth(payload.ground_truth_json, scope)
    article = await db.get(Article, payload.article_id)
    if article is None or article.project_id != project_id:
        raise HTTPException(status_code=422, detail="article not in this project")
    dup = await db.execute(
        select(GoldArticle.id)
        .where(await gold_condition(db, project, scope), GoldArticle.article_id == article.id)
        .limit(1)
    )
    if dup.first() is not None:
        raise HTTPException(status_code=409, detail="already in the gold standard")
    assessment_refs = await _checked_assessment_refs(
        db, payload.assessment_refs, project_id, article.id, scope, user
    )
    g = GoldArticle(
        project_id=project_id,
        search_id=scope.id,
        article_id=payload.article_id,
        ground_truth_json=payload.ground_truth_json,
        added_by=user.id,
    )
    db.add(g)
    await db.flush()
    db.add(
        _revision(
            g,
            1,
            project_fields(scope),
            ann=None,
            review_actor_ref=None,
            recorded_by_ref=anonymized_actor_ref(user.email),
            assessment_refs=assessment_refs,
        )
    )
    await db.commit()
    await db.refresh(g)
    return (await _items(db, project, scope, [(g, article.title)], user))[0]


@router.post("/{project_id}/gold/from-reviews", status_code=201)
async def gold_from_reviews(
    project_id: uuid.UUID,
    payload: GoldFromReviews,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Ajoute au gold standard la dernière relecture humaine des articles (tout membre).

    La référence est une copie figée des réponses du relecteur, avec un lien vers sa fiche. Un
    article déjà au gold n'est pas dupliqué (``refresh`` : on met sa copie à jour)."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    fields = project_fields(scope)
    reviewer_id = user.id if scope.kind == "blind" else None
    if payload.article_ids:
        # Only this project's articles: ``latest_reviews`` does not join Article, and the
        # default search also matches legacy annotations (``search_id`` NULL) of any project.
        requested = set(payload.article_ids)
        own_articles = set(
            (
                await db.execute(
                    select(Article.id).where(
                        Article.project_id == project.id, Article.id.in_(requested)
                    )
                )
            ).scalars()
        )
        if own_articles != requested:
            raise HTTPException(status_code=422, detail="article not in this project")
        article_ids: list = list(requested)
    else:
        article_ids = await _reviewed_ids(db, scope, reviewer_id)
    reviews = await latest_reviews(db, article_ids, scope, reviewer_id=reviewer_id)
    wanted = set(article_ids) if payload.article_ids else set(reviews)
    if set(payload.assessment_refs_by_article) - wanted:
        raise HTTPException(
            status_code=422, detail="assessment references target an unselected article"
        )
    existing = {
        g.article_id: g
        for g in (
            await db.execute(
                select(GoldArticle)
                .where(await gold_condition(db, project, scope))
                .with_for_update()
            )
        ).scalars()
    }
    added = updated = skipped = 0
    is_lead = user.role == UserRole.ADMIN or bool(
        (m := await db.get(ProjectMember, (project_id, user.id)))
        and m.role_in_project == ProjectMemberRole.LEAD
    )
    for article_id in wanted:
        review = reviews.get(article_id)
        if review is None:
            skipped += 1
            continue
        ann, reviewer = review
        assessment_refs = await _checked_assessment_refs(
            db,
            payload.assessment_refs_by_article.get(article_id, []),
            project_id,
            article_id,
            scope,
            user,
        )
        reviewer_ref = ann.annotator_hash or (
            anonymized_actor_ref(reviewer.email) if reviewer else None
        )
        g = existing.get(article_id)
        if g is None:
            g = GoldArticle(
                project_id=project_id,
                search_id=scope.id,
                article_id=article_id,
                ground_truth_json=_snapshot(ann, fields),
                source_annotation_id=ann.id,
                added_by=user.id,
            )
            db.add(g)
            await db.flush()
            db.add(
                _revision(
                    g,
                    1,
                    fields,
                    ann=ann,
                    review_actor_ref=reviewer_ref,
                    recorded_by_ref=anonymized_actor_ref(user.email),
                    assessment_refs=assessment_refs,
                )
            )
            added += 1
        elif payload.refresh and (is_lead or g.added_by == user.id):
            # pas de réécriture de la référence d'un autre (sauf LEAD) : saisie manuelle, relecteur
            previous = await db.scalar(
                select(GoldRevision)
                .where(GoldRevision.gold_id == g.id)
                .order_by(GoldRevision.sequence.desc())
                .limit(1)
            )
            sequence = previous.sequence + 1 if previous else 2
            if previous is None:
                old_ann = (
                    await db.get(HumanAnnotation, g.source_annotation_id)
                    if g.source_annotation_id
                    else None
                )
                db.add(
                    _revision(
                        g,
                        1,
                        fields,
                        ann=old_ann,
                        review_actor_ref=old_ann.annotator_hash if old_ann else None,
                        recorded_by_ref=None,
                        legacy=True,
                    )
                )
            g.ground_truth_json, g.source_annotation_id = _snapshot(ann, fields), ann.id
            db.add(
                _revision(
                    g,
                    sequence,
                    fields,
                    ann=ann,
                    review_actor_ref=reviewer_ref,
                    recorded_by_ref=anonymized_actor_ref(user.email),
                    assessment_refs=assessment_refs,
                )
            )
            updated += 1
        else:
            skipped += 1
    await db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


async def _reviewed_ids(db: AsyncSession, scope, reviewer_id: uuid.UUID | None = None) -> list:
    own = [HumanAnnotation.user_id == reviewer_id] if reviewer_id else []
    rows = await db.execute(
        select(HumanAnnotation.article_id)
        .join(Article, Article.id == HumanAnnotation.article_id)
        .where(
            Article.project_id == scope.project_id,
            in_search(HumanAnnotation.search_id, scope),
            *own,
        )
        .distinct()
    )
    return [r for r in rows.scalars() if r]


@router.get("/{project_id}/gold")
async def list_gold(
    project_id: uuid.UUID, db: DbDep, user: UserDep, search: uuid.UUID | None = None
) -> dict:
    """Références valables pour la recherche (celles de toute recherche de même question)."""
    project = await _require_project_member(db, project_id, user)
    scope = await get_search(db, project, search)
    condition = await gold_condition(db, project, scope)
    if scope.kind == "blind":
        # The current gold copy must derive from this reviewer's own locked
        # annotation; an unrelated gold copy must not disclose another review.
        condition &= GoldArticle.source_annotation_id.in_(
            select(HumanAnnotation.id).where(
                HumanAnnotation.user_id == user.id,
                in_search(HumanAnnotation.search_id, scope),
            )
        )
    rows = (
        await db.execute(
            select(GoldArticle, Article.title)
            .join(Article, GoldArticle.article_id == Article.id)
            .where(condition)
            .order_by(GoldArticle.id)
        )
    ).all()
    return {"items": await _items(db, project, scope, rows, user)}


@router.delete("/{project_id}/gold/{gold_id}", status_code=204)
async def delete_gold(project_id: uuid.UUID, gold_id: uuid.UUID, db: DbDep, user: UserDep) -> None:
    """Retire une référence du gold (LEAD, ou la personne qui l'avait ajoutée)."""
    await _require_project_member(db, project_id, user)
    g = await db.get(GoldArticle, gold_id)
    if g is None or g.project_id != project_id:
        raise HTTPException(status_code=404, detail="gold article not found")
    if g.added_by != user.id:
        await _require_lead(db, project_id, user)
    await db.delete(g)
    await db.commit()
