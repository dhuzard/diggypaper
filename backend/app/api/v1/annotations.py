"""Routes d'annotation (MODULE 5) — viewer d'annotation mobile-first.

- GET  /projects/{project_id}/articles/{article_id}            : article seul (methods_text)
- GET  /projects/{project_id}/articles/{article_id}/prediction : prédiction la plus récente
- POST /projects/{project_id}/articles/{article_id}/annotation : soumission d'annotation

Authorization : ``_require_project_member`` → 403 si l'user n'est pas membre du
projet (LEAD/ANNOTATOR) et n'est pas ADMIN global. L'article doit appartenir au
projet (404 sinon) — pas de fuite inter-projets.
"""

import asyncio
import json
import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, rate_limit_user
from app.api.v1.projects import (
    _require_project_member,
    biblio_payload,
    latest_reviews,
    review_payload,
)
from app.article_text import compose_text
from app.db.models import (
    Article,
    ArticlePrediction,
    HumanAnnotation,
    Project,
    Search,
    SourceSnapshot,
    User,
)
from app.provenance import Components, review_binding, source_from_record
from app.provenance_capture import capture_article_source
from app.provenance_render import composed_view
from app.review_source import issue_review_token, parse_review_token
from app.schema import project_fields, validate_decision
from app.searches import get_search, in_search, lot_condition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["annotations"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

# Schéma d'annotation (spec §5) — mêmes clés que le gold dataset.
_AnimalSex = Literal["MALE_ONLY", "FEMALE_ONLY", "BOTH_SEXES", "UNSPECIFIED"]


_META_KEYS = {
    "confidence_score", "evidence_quotes", "reasoning_summary", "review_status", "source_binding",
}


class AnnotationCreate(BaseModel):
    use_ai_behavior: bool
    animal_sex: _AnimalSex
    confidence_score: float = Field(ge=0, le=1)
    evidence_quotes: list[str] = Field(default_factory=list, max_length=100)
    reasoning_summary: str = Field(default="", max_length=5000)


async def _require_article(
    db: AsyncSession, project_id: uuid.UUID, article_id: uuid.UUID, user: User
) -> Article:
    """Charge l'article ; 403/404 si pas membre du projet, 404 si l'article n'y est pas.

    Le 403 (non membre) est levé AVANT toute vérification de l'article : un
    non-membre ne peut pas énumérer les articles d'un projet.
    """
    project = await _require_project_member(db, project_id, user)
    article = await db.get(Article, article_id)
    if article is None or article.project_id != project.id:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


async def _scope(db: AsyncSession, project_id: uuid.UUID, search_id: uuid.UUID | None) -> Search:
    """Recherche demandée (par défaut la recherche initiale) : question, items, parties de texte."""
    project = await db.get(Project, project_id)
    return await get_search(db, project, search_id)


def _article_payload(a: Article, kinds: list[str] | None = None) -> dict:
    return {
        "id": str(a.id),
        "pmid": a.pmid,
        "pmcid": a.pmcid,
        "doi": a.doi,
        "title": a.title,
        "abstract": a.abstract,
        "methods_text": a.methods_text,
        # Texte fourni aux modèles = parties choisies pour le projet (cf. text_sections).
        "text": compose_text(a, kinds),
        "has_fulltext": bool(a.sections),
        "fulltext_source": a.fulltext_source,
        "fulltext_note": a.fulltext_note,
        **biblio_payload(a),
        "original_language": a.original_language,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _prediction_payload(p: ArticlePrediction) -> dict:
    return {
        "id": str(p.id),
        "article_id": str(p.article_id) if p.article_id else None,
        "prompt_template_id": str(p.prompt_template_id) if p.prompt_template_id else None,
        "model_used": p.model_used,
        "decision_json": p.decision_json,
        "confidence_score": p.confidence_score,
        "evidence_quotes": p.evidence_quotes,
        # Null means this historical/scorer prediction was not checked against
        # a captured model input. Do not infer a result from today's article.
        "quote_checks": p.quote_checks_json,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("/{project_id}/articles/{article_id}")
async def get_article(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Article seul (avec methods_text) pour le viewer d'annotation ; le texte « fourni aux
    modèles » suit les parties de la recherche."""
    article = await _require_article(db, project_id, article_id, user)
    scope = await _scope(db, project_id, search)
    if scope.kind == "blind" and await db.scalar(
        select(Article.id).where(Article.id == article.id, lot_condition(scope))
    ) is None:
        raise HTTPException(status_code=404, detail="Article not found in search")
    snapshot, view_id = await capture_article_source(
        db, project_id=project_id, article_id=article_id, text_sections=scope.text_sections
    )
    view = next(view for view in snapshot.views if view.id == view_id)
    result = _article_payload(article, scope.text_sections)
    # Build all displayed source fields from the same locked capture, not a stale
    # ORM article or a second read after another worker can replace the source.
    result.update(
        title=snapshot.components.title,
        abstract=snapshot.components.abstract,
        methods_text=snapshot.components.methods_text,
        text=view.text,
        has_fulltext=bool(snapshot.components.sections),
        source_view_token=issue_review_token(snapshot, view_id, scope, user.id),
    )
    await db.commit()
    return result


@router.post(
    "/{project_id}/articles/{article_id}/fulltext/search",
    dependencies=[Depends(rate_limit_user("fulltext_search", 10))],
)
async def search_fulltext(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Relance la recherche du texte intégral de CET article (sources ouvertes), après avoir
    relu sa notice PubMed (un préprint publié change de PMCID/DOI/titre). Un texte déposé à la
    main n'est pas écrasé. Renvoie ``{found, source, article}``."""
    import httpx

    from app import fulltext as ft
    from app.settings.service import get_setting

    article = await _require_article(db, project_id, article_id, user)
    email = (
        await get_setting(db, "contact_email")
        or await get_setting(db, "smtp_from_email")
        or "diggypaper@example.org"
    )
    async with httpx.AsyncClient(timeout=45.0) as http:
        try:
            await ft.refresh_article_metadata(db, article, http)
        except Exception:  # noqa: BLE001 - NCBI indisponible : on cherche avec ce qu'on a
            logger.warning("metadata refresh failed for article %s", article.id, exc_info=True)
        source = await ft.complete_article(db, article, email, http)
    await db.commit()
    scope = await _scope(db, project_id, search)
    return {
        "found": source is not None,
        "source": source,
        "article": _article_payload(article, scope.text_sections),
    }


@router.post("/{project_id}/fulltext/match")
async def match_fulltext_pdf(
    project_id: uuid.UUID, request: Request, db: DbDep, user: UserDep
) -> dict:
    """Dépôt en lot : un PDF dont l'article n'est pas désigné. On retrouve l'article du projet
    qu'il représente (DOI ou titre en tête de document) et on lui attache ce texte. Un seul
    candidat net est requis : 404 ``no_match`` sinon, 409 ``ambiguous`` si plusieurs."""
    from app.api.v1.projects import _require_lead_or_admin
    from app.fulltext import (
        MAX_PDF_BYTES,
        MIN_CHARS,
        TITLE_COVERAGE,
        apply_uploaded_pdf,
        match_scores,
        pdf_to_sections,
    )

    await _require_project_member(db, project_id, user)
    await _require_lead_or_admin(db, project_id, user)
    data = await request.body()
    if not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=422, detail="a PDF file (max 25 MB) is required")
    try:
        sections = await asyncio.to_thread(pdf_to_sections, data)
    except Exception:  # noqa: BLE001 - PDF corrompu / chiffré
        raise HTTPException(status_code=422, detail="unreadable PDF") from None
    if sum(len(s["text"]) for s in sections) < MIN_CHARS:
        raise HTTPException(status_code=422, detail="no text found in the PDF (scanned?)")
    rows = (
        await db.execute(
            select(Article.id, Article.title, Article.doi).where(Article.project_id == project_id)
        )
    ).all()
    scores = [s for s in match_scores(sections, [tuple(r) for r in rows]) if s[0] >= TITLE_COVERAGE]
    if not scores:
        raise HTTPException(status_code=404, detail="no_match")
    if len(scores) > 1 and scores[1][0] >= scores[0][0] - 0.05:
        raise HTTPException(status_code=409, detail="ambiguous")
    article = await db.get(Article, scores[0][1])
    await apply_uploaded_pdf(db, article, sections, data)
    await db.commit()
    return {
        "article_id": str(article.id),
        "title": article.title,
        "score": round(min(scores[0][0], 1.0), 2),
    }


@router.post("/{project_id}/articles/{article_id}/fulltext")
async def upload_fulltext(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    request: Request,
    db: DbDep,
    user: UserDep,
    force: bool = False,
    search: uuid.UUID | None = None,
) -> dict:
    """Dépôt manuel du PDF d'un article (accès institutionnel) : corps = octets du PDF.

    Le texte est extrait et découpé en sections ; il remplace le texte intégral existant.
    Le PDF doit être celui de l'article (titre, à défaut DOI, en tête de document) : sinon 422
    ``pdf_mismatch``, sauf ``force=true`` (scan, titre illisible…).
    """
    from app.fulltext import (
        MAX_PDF_BYTES,
        MIN_CHARS,
        apply_uploaded_pdf,
        pdf_matches_article,
        pdf_to_sections,
    )

    article = await _require_article(db, project_id, article_id, user)
    from app.api.v1.projects import _require_lead_or_admin

    await _require_lead_or_admin(db, project_id, user)
    data = await request.body()
    if not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=422, detail="a PDF file (max 25 MB) is required")
    try:
        sections = await asyncio.to_thread(pdf_to_sections, data)
    except Exception:  # noqa: BLE001 - PDF corrompu / chiffré
        raise HTTPException(status_code=422, detail="unreadable PDF") from None
    if sum(len(s["text"]) for s in sections) < MIN_CHARS:
        raise HTTPException(status_code=422, detail="no text found in the PDF (scanned?)")
    verdict = pdf_matches_article(article.title, article.doi, sections)
    if verdict is False and not force:
        raise HTTPException(status_code=422, detail="pdf_mismatch")
    await apply_uploaded_pdf(db, article, sections, data)
    await db.commit()
    scope = await _scope(db, project_id, search)
    payload = _article_payload(article, scope.text_sections)
    payload["pdf_check"] = {True: "match", False: "forced", None: "unverified"}[verdict]
    return payload


@router.get("/{project_id}/articles/{article_id}/prediction")
async def get_prediction(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict | None:
    """Prédiction la plus récente de l'article dans cette recherche (null si aucune)."""
    article = await _require_article(db, project_id, article_id, user)
    scope = await _scope(db, project_id, search)
    if scope.kind == "blind":
        # Échantillon à l'aveugle : prédiction visible seulement après la relecture de l'user.
        mine = (
            await db.execute(
                select(HumanAnnotation.id)
                .where(
                    HumanAnnotation.article_id == article.id,
                    HumanAnnotation.user_id == user.id,
                    in_search(HumanAnnotation.search_id, scope),
                )
                .limit(1)
            )
        ).first()
        if mine is None:
            return None
    prediction = (
        (
            await db.execute(
                select(ArticlePrediction)
                .where(
                    ArticlePrediction.article_id == article.id,
                    in_search(ArticlePrediction.search_id, scope),
                )
                .order_by(
                    ArticlePrediction.created_at.desc().nulls_last(),
                    ArticlePrediction.id.desc(),
                )
            )
        )
        .scalars()
        .first()
    )
    if prediction is None:
        return None
    return _prediction_payload(prediction)


@router.get("/{project_id}/articles/{article_id}/annotation")
async def get_annotation(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict | None:
    """Dernière relecture humaine de l'article dans cette recherche (réponses + relecteur +
    statut), null si aucune."""
    article = await _require_article(db, project_id, article_id, user)
    scope = await _scope(db, project_id, search)
    if scope.kind == "blind":  # aveugle : seulement SA relecture, jamais celle d'un autre relecteur
        own = (
            await db.execute(
                select(HumanAnnotation)
                .where(
                    HumanAnnotation.article_id == article.id,
                    HumanAnnotation.user_id == user.id,
                    in_search(HumanAnnotation.search_id, scope),
                )
                .order_by(HumanAnnotation.created_at.desc().nulls_last(), HumanAnnotation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        ann, reviewer = (own, user) if own else (None, None)
    else:
        ann, reviewer = (await latest_reviews(db, [article.id], scope)).get(
            article.id, (None, None)
        )
    if ann is None:
        return None
    return {
        "decision_json": ann.decision_json,
        "notes": ann.notes,
        **review_payload(ann, reviewer),
    }


def _review_status(decision: dict, prediction: ArticlePrediction | None) -> str:
    """VALIDATED = réponses de l'IA inchangées ; CORRECTED = modifiées ; MANUAL = sans IA."""
    pred = (prediction.decision_json or {}) if prediction else {}
    keys = [k for k in decision if k not in _META_KEYS and k in pred]
    if not prediction or not pred:
        return "MANUAL"
    return "VALIDATED" if all(decision[k] == pred[k] for k in keys) else "CORRECTED"


@router.post("/{project_id}/articles/{article_id}/annotation", status_code=201)
async def create_annotation(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    payload: Annotated[dict, Body()],
    db: DbDep,
    user: UserDep,
    search: uuid.UUID | None = None,
) -> dict:
    """Soumet une annotation humaine pour la recherche ``search`` (``blind`` : faite sans voir la
    prédiction du modèle, cf. échantillon à l'aveugle).

    Le payload est stocké dans ``decision_json`` (schéma spec §5) ; le
    ``reasoning_summary`` est aussi conservé dans ``notes`` (colonne Text).
    """
    article = await _require_article(db, project_id, article_id, user)
    scope = await _scope(db, project_id, search)
    if scope.kind == "blind":
        if await db.scalar(
            select(Article.id).where(Article.id == article.id, lot_condition(scope))
        ) is None:
            raise HTTPException(status_code=404, detail="Article not found in search")
        # Serialize first submissions on PostgreSQL so two parallel POSTs cannot
        # both pass the existence check before either commits.
        await db.scalar(
            select(Article.id).where(Article.id == article.id).with_for_update()
        )
        prior = await db.scalar(
            select(HumanAnnotation.id).where(
                HumanAnnotation.article_id == article.id,
                HumanAnnotation.user_id == user.id,
                in_search(HumanAnnotation.search_id, scope),
            ).limit(1)
        )
        if prior is not None:
            raise HTTPException(status_code=409, detail="blind review already submitted")
    token = payload.get("source_view_token")
    if token is None:
        if scope.kind == "blind":
            raise HTTPException(
                status_code=422, detail="source view token required for blind review"
            )
        binding = {"state": "legacy_unversioned", "current_display_text_drift": None}
        source_columns = {}
    else:
        if not isinstance(token, str):
            raise HTTPException(status_code=422, detail="invalid source view token")
        try:
            source_id, view_id, digest = parse_review_token(
                token, project_id=project_id, article_id=article_id,
                scope=scope, user_id=user.id,
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid source view token") from None
        retained = await db.get(SourceSnapshot, source_id)
        if retained is None:
            raise HTTPException(status_code=409, detail="retained source view unavailable")
        try:
            snapshot = source_from_record(retained)
            source_columns = review_binding(snapshot, view_id)
        except ValueError:
            raise HTTPException(
                status_code=409, detail="retained source view unavailable"
            ) from None
        if (
            snapshot.project_id != project_id or snapshot.article_id != article_id
            or source_columns["source_view_sha256"] != digest
        ):
            raise HTTPException(status_code=422, detail="invalid source view token")
        current = await db.scalar(
            select(Article).where(Article.id == article_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        current_components = Components(
            title=current.title, abstract=current.abstract, methods_text=current.methods_text,
            sections=tuple(current.sections or ()),
        )
        drift = (
            current_components != snapshot.components
            or composed_view(current_components, scope.text_sections).sha256 != digest
        )
        binding = {"state": "view_bound", "current_display_text_drift": drift}
    if scope.fields is not None:
        # Schéma propre à la recherche : champs définis + clés standard optionnelles.
        errors = validate_decision(project_fields(scope), payload)
        if errors:
            raise HTTPException(status_code=422, detail=errors)
        decision = {f["key"]: payload[f["key"]] for f in project_fields(scope)}
        decision["confidence_score"] = payload.get("confidence_score", 1.0)
        decision["evidence_quotes"] = [str(q) for q in payload.get("evidence_quotes") or []][:100]
        decision["reasoning_summary"] = str(payload.get("reasoning_summary") or "")[:5000]
    else:
        try:
            decision = AnnotationCreate(**payload).model_dump()
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
    latest = (
        (
            await db.execute(
                select(ArticlePrediction)
                .where(
                    ArticlePrediction.article_id == article.id,
                    in_search(ArticlePrediction.search_id, scope),
                )
                .order_by(
                    ArticlePrediction.created_at.desc().nulls_last(), ArticlePrediction.id.desc()
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    decision["review_status"] = _review_status(decision, latest)
    decision["source_binding"] = binding
    ann = HumanAnnotation(
        article_id=article.id,
        user_id=user.id,
        search_id=scope.id,
        blind=scope.kind == "blind",
        decision_json=decision,
        notes=decision["reasoning_summary"],
        **source_columns,
    )
    db.add(ann)
    await db.commit()
    await db.refresh(ann)
    return {
        "id": str(ann.id),
        "article_id": str(ann.article_id) if ann.article_id else None,
        "user_id": str(ann.user_id) if ann.user_id else None,
        "annotator_hash": ann.annotator_hash,
        "decision_json": ann.decision_json,
        "notes": ann.notes,
        "created_at": ann.created_at.isoformat() if ann.created_at else None,
        **review_payload(ann, user),
    }
