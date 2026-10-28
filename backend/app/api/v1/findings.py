"""Finding curation: immutable revisions linked to exact captured evidence, independently reviewed.

A finding is a stable identity; every change is a new revision. Only the latest revision
can be reviewed or exported and an approval never carries to another revision. Quote match
status (machine) and the human support judgment stay separate and are never derived from
each other.
"""

import io
import os
import re
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.annotations import _require_article
from app.api.v1.gold import _require_lead
from app.db.models import (
    Article,
    ArticlePrediction,
    ArticleRightsDeclaration,
    Finding,
    FindingEvidence,
    FindingReview,
    FindingRevision,
    Project,
    Search,
    SourceSnapshot,
    User,
)
from app.evidence_links import EvidenceEnvelopeV1
from app.finding_schema import FindingContent
from app.pseudonym import anonymized_actor_ref, reviewer_reference
from app.rights import PACKET_METADATA_LICENSES, REDISTRIBUTABLE_LICENSES

router = APIRouter(prefix="/projects", tags=["findings"])
DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]
PROTOCOL_VERSION = "seip-support-v1"
Status = Literal["supports", "contradicts", "insufficient", "unclear", "not_assessed"]
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prediction_id: uuid.UUID
    answer_ref: uuid.UUID
    evidence_ref: uuid.UUID


class FindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: FindingContent
    evidence: list[EvidenceIn] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def distinct_passages(self):
        refs = [item.evidence_ref for item in self.evidence]
        if len(set(refs)) != len(refs):
            raise ValueError("an evidence passage may be linked only once per revision")
        return self


class JudgmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ref: uuid.UUID
    status: Status
    rationale: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def require_reason(self):
        if self.status != "not_assessed" and not self.rationale.strip():
            raise ValueError("a rationale is required for a support judgment")
        return self


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    rationale: str = Field(max_length=5000)
    judgments: list[JudgmentIn] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid(self):
        if not self.rationale.strip():
            raise ValueError("a rationale is required")
        refs = [item.evidence_ref for item in self.judgments]
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate judgment for one evidence passage")
        if self.decision == "approve" and not any(j.status == "supports" for j in self.judgments):
            raise ValueError("approval requires at least one 'supports' judgment")
        return self


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _graph_pairs(prediction: ArticlePrediction, project_id, article_id) -> EvidenceEnvelopeV1:
    """Validated structured envelope of ``prediction`` or 409 (same checks as assessments)."""
    graph = prediction.evidence_graph_json
    if not graph:
        raise HTTPException(status_code=409, detail="Prediction has no versioned evidence graph")
    try:
        envelope = EvidenceEnvelopeV1.model_validate(graph["envelope"])
    except (ValidationError, KeyError, TypeError):
        raise HTTPException(status_code=409, detail="Evidence graph is not valid") from None
    if envelope.state != "structured" or not envelope.links:
        raise HTTPException(status_code=409, detail="Prediction has no linkable answers")
    scope = envelope.scope
    if (
        scope.project_ref != str(project_id)
        or scope.article_ref != str(article_id)
        or scope.record_kind != "prediction"
        or scope.record_ref != str(prediction.id)
        or scope.revision_ref != str(prediction.id)
    ):
        raise HTTPException(status_code=409, detail="Evidence graph revision mismatch")
    answers = {item.answer_ref for item in envelope.answers}
    evidence = {item.evidence_ref: item for item in envelope.evidence}
    pairs = [(link.answer_ref, link.evidence_ref) for link in envelope.links]
    if (
        len(answers) != len(envelope.answers)
        or len(evidence) != len(envelope.evidence)
        or len(set(pairs)) != len(pairs)
        or any(
            a not in answers or e not in evidence or evidence[e].origin != "captured_input_context"
            for a, e in pairs
        )
    ):
        raise HTTPException(status_code=409, detail="Evidence graph has unlinkable evidence")
    return envelope


async def _check_links(
    db: AsyncSession, project_id: uuid.UUID, article: Article, links: list[EvidenceIn]
) -> None:
    ids = {item.prediction_id for item in links}
    rows = (
        (await db.execute(select(ArticlePrediction).where(ArticlePrediction.id.in_(ids))))
        .scalars()
        .all()
    )
    predictions = {row.id: row for row in rows}
    for prediction_id in sorted(ids, key=str):
        prediction = predictions.get(prediction_id)
        if prediction is None or prediction.article_id != article.id:
            raise HTTPException(status_code=422, detail="Evidence is not from this article")
        if prediction.search_id is not None:
            search = await db.get(Search, prediction.search_id)
            if search is None or search.project_id != project_id:
                raise HTTPException(status_code=422, detail="Evidence is not from this project")
            if search.kind == "blind":
                raise HTTPException(
                    status_code=409, detail="Findings cannot be curated from a blind search"
                )
    allowed = {}
    for prediction_id, prediction in predictions.items():
        envelope = _graph_pairs(prediction, project_id, article.id)
        allowed[prediction_id] = {(str(l.answer_ref), str(l.evidence_ref)) for l in envelope.links}  # noqa: E741
    for item in links:
        if (str(item.answer_ref), str(item.evidence_ref)) not in allowed[item.prediction_id]:
            raise HTTPException(status_code=422, detail="Answer/evidence link not in prediction")


def _quote_info(prediction: ArticlePrediction | None, evidence_ref: uuid.UUID) -> tuple:
    """(quote, quote_match | None) from the stored graph and quote checks; never invented."""
    graph = prediction.evidence_graph_json if prediction else None
    if not graph:
        return None, None
    try:
        record = next(
            e for e in graph["envelope"]["evidence"] if e.get("evidence_ref") == str(evidence_ref)
        )
    except (StopIteration, KeyError, TypeError):
        return None, None
    quote, ref = record.get("quote"), record.get("quote_match_ref")
    if not ref:
        return quote, None
    checks = prediction.quote_checks_json or {}
    item = next((i for i in checks.get("items", []) if i.get("match_ref") == ref), None)
    if item is None:
        return quote, None
    result = item.get("result") or {}
    status = result.get("status")
    candidates = result.get("candidates") or []
    view_ref = start = end = None
    if status in ("exact", "normalized") and len(candidates) == 1:
        c = candidates[0]
        view_ref, start, end = c.get("view_ref"), c.get("view_start"), c.get("view_end")
        if start is None or end is None or view_ref is None:
            view_ref = start = end = None
    return quote, {"ref": ref, "status": status, "view_ref": view_ref, "start": start, "end": end}


def _state(revision: int, latest: int, reviews: list[dict]) -> str:
    if revision != latest:
        return "superseded"
    decisions = {r["decision"] for r in reviews}
    if "reject" in decisions:
        return "rejected"
    return "approved" if "approve" in decisions else "proposed"


async def _load(
    db: AsyncSession,
    project_id: uuid.UUID,
    article_id: uuid.UUID | None = None,
    finding_id: uuid.UUID | None = None,
) -> list[dict]:
    query = select(Finding).where(Finding.project_id == project_id)
    if article_id is not None:
        query = query.where(Finding.article_id == article_id)
    if finding_id is not None:
        query = query.where(Finding.id == finding_id)
    findings = (await db.execute(query.order_by(Finding.created_at, Finding.id))).scalars().all()
    if not findings:
        return []
    ids = [f.id for f in findings]
    revisions = (
        (
            await db.execute(
                select(FindingRevision)
                .where(FindingRevision.finding_id.in_(ids))
                .order_by(FindingRevision.finding_id, FindingRevision.revision)
            )
        )
        .scalars()
        .all()
    )
    rev_ids = [r.id for r in revisions]
    links = (
        (
            await db.execute(
                select(FindingEvidence)
                .where(FindingEvidence.revision_id.in_(rev_ids))
                .order_by(FindingEvidence.evidence_ref)
            )
        )
        .scalars()
        .all()
    )
    reviews = (
        (
            await db.execute(
                select(FindingReview)
                .where(FindingReview.revision_id.in_(rev_ids))
                .order_by(FindingReview.created_at, FindingReview.id)
            )
        )
        .scalars()
        .all()
    )
    pred_ids = {link.prediction_id for link in links}
    predictions = {
        p.id: p
        for p in (
            (await db.execute(select(ArticlePrediction).where(ArticlePrediction.id.in_(pred_ids))))
            .scalars()
            .all()
        )
    }
    links_by, reviews_by, revs_by = {}, {}, {}
    for link in links:
        links_by.setdefault(link.revision_id, []).append(link)
    for review in reviews:
        reviews_by.setdefault(review.revision_id, []).append(review)
    for rev in revisions:
        revs_by.setdefault(rev.finding_id, []).append(rev)
    result = []
    for finding in findings:
        revs = revs_by.get(finding.id, [])
        latest = max((r.revision for r in revs), default=0)
        out = []
        for rev in revs:
            evidence = []
            for link in links_by.get(rev.id, []):
                prediction = predictions.get(link.prediction_id)
                quote, match = _quote_info(prediction, link.evidence_ref)
                snapshot = prediction.source_snapshot_id if prediction else None
                evidence.append(
                    {
                        "prediction_id": str(link.prediction_id),
                        "answer_ref": str(link.answer_ref),
                        "evidence_ref": str(link.evidence_ref),
                        "quote": quote,
                        "quote_match": match,
                        "source_snapshot_id": str(snapshot) if snapshot else None,
                    }
                )
            review_rows = [
                {
                    "id": str(r.id),
                    "reviewer_ref": reviewer_reference(project_id, r.reviewer_ref),
                    "decision": r.decision,
                    "rationale": r.rationale,
                    "judgments": r.judgments_json,
                    "created_at": _iso(r.created_at),
                }
                for r in reviews_by.get(rev.id, [])
            ]
            out.append(
                {
                    "finding_id": str(finding.id),
                    "revision": rev.revision,
                    "is_latest": rev.revision == latest,
                    "state": _state(rev.revision, latest, review_rows),
                    "content": rev.content_json,
                    "proposer_ref": reviewer_reference(project_id, rev.proposer_ref),
                    "created_at": _iso(rev.created_at),
                    "evidence": evidence,
                    "reviews": review_rows,
                }
            )
        result.append(
            {
                "id": str(finding.id),
                "article_id": str(finding.article_id),
                "created_at": _iso(finding.created_at),
                "revisions": out,
            }
        )
    return result


async def _add_revision(
    db: AsyncSession,
    project_id: uuid.UUID,
    article: Article,
    finding: Finding,
    body: FindingBody,
    user: User,
) -> dict:
    number = (
        await db.scalar(
            select(func.max(FindingRevision.revision)).where(
                FindingRevision.finding_id == finding.id
            )
        )
        or 0
    ) + 1
    revision = FindingRevision(
        finding_id=finding.id,
        revision=number,
        content_json=body.content.model_dump(mode="json"),
        proposer_id=user.id,
        proposer_ref=anonymized_actor_ref(user.email),
    )
    db.add(revision)
    await db.flush()
    for item in sorted(body.evidence, key=lambda e: str(e.evidence_ref)):
        db.add(
            FindingEvidence(
                revision_id=revision.id,
                prediction_id=item.prediction_id,
                answer_ref=item.answer_ref,
                evidence_ref=item.evidence_ref,
            )
        )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Concurrent revision conflict") from None
    loaded = await _load(db, project_id, finding_id=finding.id)
    return next(r for r in loaded[0]["revisions"] if r["revision"] == number)


@router.post("/{project_id}/articles/{article_id}/findings", status_code=201)
async def create_finding(
    project_id: uuid.UUID, article_id: uuid.UUID, body: FindingBody, db: DbDep, user: UserDep
) -> dict:
    article = await _require_article(db, project_id, article_id, user)
    await _check_links(db, project_id, article, body.evidence)
    finding = Finding(project_id=project_id, article_id=article.id)
    db.add(finding)
    await db.flush()
    return await _add_revision(db, project_id, article, finding, body, user)


async def _locked_finding(
    db: AsyncSession, project_id: uuid.UUID, article_id: uuid.UUID, finding_id: uuid.UUID
) -> Finding:
    finding = await db.scalar(
        select(Finding)
        .where(
            Finding.id == finding_id,
            Finding.project_id == project_id,
            Finding.article_id == article_id,
        )
        .with_for_update()
    )
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding


@router.post("/{project_id}/articles/{article_id}/findings/{finding_id}/revisions", status_code=201)
async def revise_finding(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: FindingBody,
    db: DbDep,
    user: UserDep,
) -> dict:
    article = await _require_article(db, project_id, article_id, user)
    finding = await _locked_finding(db, project_id, article.id, finding_id)
    await _check_links(db, project_id, article, body.evidence)
    return await _add_revision(db, project_id, article, finding, body, user)


@router.get("/{project_id}/articles/{article_id}/findings")
async def list_findings(
    project_id: uuid.UUID, article_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict:
    article = await _require_article(db, project_id, article_id, user)
    return {"findings": await _load(db, project_id, article.id)}


@router.post(
    "/{project_id}/articles/{article_id}/findings/{finding_id}/revisions/{revision}/reviews",
    status_code=201,
)
async def review_revision(
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    finding_id: uuid.UUID,
    revision: int,
    body: ReviewBody,
    db: DbDep,
    user: UserDep,
) -> dict:
    article = await _require_article(db, project_id, article_id, user)
    finding = await _locked_finding(db, project_id, article.id, finding_id)
    rows = (
        (await db.execute(select(FindingRevision).where(FindingRevision.finding_id == finding.id)))
        .scalars()
        .all()
    )
    target = next((r for r in rows if r.revision == revision), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    if revision != max(r.revision for r in rows):
        raise HTTPException(status_code=409, detail="Revision is superseded and not reviewable")
    reviewer = anonymized_actor_ref(user.email)
    if reviewer == target.proposer_ref:
        raise HTTPException(status_code=403, detail="reviewer must be independent")
    refs = set(
        (
            await db.execute(
                select(FindingEvidence.evidence_ref).where(FindingEvidence.revision_id == target.id)
            )
        )
        .scalars()
        .all()
    )
    if {j.evidence_ref for j in body.judgments} != refs:
        raise HTTPException(
            status_code=422, detail="Judgments must cover exactly this revision's evidence"
        )
    if await db.scalar(
        select(FindingReview.id).where(
            FindingReview.revision_id == target.id, FindingReview.reviewer_ref == reviewer
        )
    ):
        raise HTTPException(status_code=409, detail="Already reviewed by this reviewer")
    judgments = [
        {"evidence_ref": str(j.evidence_ref), "status": j.status, "rationale": j.rationale.strip()}
        for j in sorted(body.judgments, key=lambda j: str(j.evidence_ref))
    ]
    review = FindingReview(
        revision_id=target.id,
        decision=body.decision,
        judgments_json=judgments,
        rationale=body.rationale.strip(),
        protocol_version=PROTOCOL_VERSION,
        reviewer_id=user.id,
        reviewer_ref=reviewer,
    )
    db.add(review)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already reviewed by this reviewer") from None
    await db.refresh(review)
    return {
        "id": str(review.id),
        "finding_id": str(finding.id),
        "revision": revision,
        "reviewer_ref": reviewer_reference(project_id, review.reviewer_ref),
        "decision": review.decision,
        "rationale": review.rationale,
        "judgments": review.judgments_json,
        "protocol_version": review.protocol_version,
        "created_at": _iso(review.created_at),
    }


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, files[name], compresslevel=6)
    return buffer.getvalue()


# --- redistribution rights ---------------------------------------------------------------


class RightsBody(BaseModel):
    """One redistribution decision for an article. ``allowed`` needs an open licence + URL."""

    model_config = ConfigDict(extra="forbid")

    license_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^([A-Za-z0-9][A-Za-z0-9.+-]*|proprietary|unknown)$",
    )
    redistribution: Literal["allowed", "metadata-only", "restricted"]
    basis: str = Field(min_length=1, max_length=4000)
    evidence_url: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def valid(self):
        if not self.basis.strip():
            raise ValueError("basis must state why this decision is correct")
        if self.evidence_url is not None and not re.fullmatch(r"https://\S+", self.evidence_url):
            raise ValueError("evidence_url must be an https URL")
        if self.redistribution == "allowed":
            if self.license_id not in REDISTRIBUTABLE_LICENSES:
                raise ValueError(
                    "redistribution 'allowed' requires one of "
                    + ", ".join(REDISTRIBUTABLE_LICENSES)
                )
            if self.evidence_url is None:
                raise ValueError(
                    "redistribution 'allowed' requires evidence_url stating the licence"
                )
        return self


def _rights_out(project_id: uuid.UUID, d: ArticleRightsDeclaration) -> dict:
    return {
        "id": str(d.id),
        "article_id": str(d.article_id),
        "revision": d.revision,
        "license_id": d.license_id,
        "redistribution": d.redistribution,
        "basis": d.basis,
        "evidence_url": d.evidence_url,
        "declared_by_ref": reviewer_reference(project_id, d.declared_by_ref),
        "created_at": _iso(d.created_at),
    }


@router.post("/{project_id}/articles/{article_id}/rights", status_code=201)
async def declare_rights(
    project_id: uuid.UUID, article_id: uuid.UUID, body: RightsBody, db: DbDep, user: UserDep
) -> dict:
    """LEAD-only, append-only: a new revision supersedes; nothing is edited or deleted."""
    article = await _require_article(db, project_id, article_id, user)
    await _require_lead(db, project_id, user)
    latest = await db.scalar(
        select(func.max(ArticleRightsDeclaration.revision)).where(
            ArticleRightsDeclaration.project_id == project_id,
            ArticleRightsDeclaration.article_id == article.id,
        )
    )
    declaration = ArticleRightsDeclaration(
        project_id=project_id,
        article_id=article.id,
        revision=(latest or 0) + 1,
        license_id=body.license_id,
        redistribution=body.redistribution,
        basis=body.basis.strip(),
        evidence_url=body.evidence_url,
        declared_by_id=user.id,
        declared_by_ref=anonymized_actor_ref(user.email),
    )
    db.add(declaration)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="concurrent rights declaration") from exc
    await db.refresh(declaration)
    return _rights_out(project_id, declaration)


@router.get("/{project_id}/articles/{article_id}/rights")
async def list_rights(
    project_id: uuid.UUID, article_id: uuid.UUID, db: DbDep, user: UserDep
) -> dict:
    """Full declaration history (the audit trail); the last revision is the one that applies."""
    article = await _require_article(db, project_id, article_id, user)
    rows = (
        await db.execute(
            select(ArticleRightsDeclaration)
            .where(
                ArticleRightsDeclaration.project_id == project_id,
                ArticleRightsDeclaration.article_id == article.id,
            )
            .order_by(ArticleRightsDeclaration.revision)
        )
    ).scalars()
    return {"declarations": [_rights_out(project_id, d) for d in rows]}


async def _latest_rights(
    db: AsyncSession, project_id: uuid.UUID, article_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ArticleRightsDeclaration]:
    rows = (
        await db.execute(
            select(ArticleRightsDeclaration)
            .where(
                ArticleRightsDeclaration.project_id == project_id,
                ArticleRightsDeclaration.article_id.in_(article_ids),
            )
            .order_by(ArticleRightsDeclaration.revision)
        )
    ).scalars()
    return {d.article_id: d for d in rows}  # ordered by revision: the last one wins


async def packet_input(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    producer_commit: str,
    access_level: str = "restricted",
    metadata_license: str | None = None,
) -> dict:
    """Input of build_packet for a project's complete reviewed revision chains.

    Shared by the LEAD export below and the connector (#53), so a digest a descriptor lists
    is the digest of the packet the export returns. Callers check access first.
    """
    findings = await _load(db, project_id)
    article_ids = sorted({uuid.UUID(f["article_id"]) for f in findings}, key=str)
    articles = {
        a.id: a
        for a in (await db.execute(select(Article).where(Article.id.in_(article_ids))))
        .scalars()
        .all()
    }
    snap_ids = {
        uuid.UUID(e["source_snapshot_id"])
        for f in findings
        for r in f["revisions"]
        for e in r["evidence"]
        if e["source_snapshot_id"]
    }
    snapshots = {
        s.id: s
        for s in (await db.execute(select(SourceSnapshot).where(SourceSnapshot.id.in_(snap_ids))))
        .scalars()
        .all()
        if s.project_id == project_id
    }
    rights = await _latest_rights(db, project_id, article_ids)
    papers, records = [], []
    for article_id in article_ids:
        article = articles[article_id]
        declaration = rights.get(article_id)
        # Every snapshot any revision of this article's findings cites; the builder decides
        # (fail closed) whether a chain or paper mixes snapshots. Nothing is repointed here.
        used = {
            e["source_snapshot_id"]: snapshots[uuid.UUID(e["source_snapshot_id"])]
            for f in findings
            if f["article_id"] == str(article_id)
            for r in f["revisions"]
            for e in r["evidence"]
            if e["source_snapshot_id"] and uuid.UUID(e["source_snapshot_id"]) in snapshots
        }
        papers.append(
            {
                "article_id": str(article.id),
                "doi": article.doi,
                "pmid": article.pmid,
                "pmcid": article.pmcid,
                "title": article.title,
                "authors": list(article.authors or []),
                "pub_types": list(article.pub_types or []),
                "rights": (
                    {
                        "revision": declaration.revision,
                        "license_id": declaration.license_id,
                        "redistribution": declaration.redistribution,
                    }
                    if declaration
                    else None
                ),
                "snapshots": [
                    {
                        "id": str(snap.id),
                        "sha256": snap.snapshot_sha256,
                        "raw_availability": snap.raw_availability,
                    }
                    for snap in sorted(used.values(), key=lambda s: str(s.id))
                ],
            }
        )
    for f in findings:
        for r in f["revisions"]:
            records.append(
                {
                    "finding_id": f["id"],
                    "article_id": f["article_id"],
                    "revision": r["revision"],
                    "is_latest": r["is_latest"],
                    "content": r["content"],
                    "proposer_ref": r["proposer_ref"],
                    "created_at": r["created_at"],
                    "evidence": [
                        {
                            "prediction_id": e["prediction_id"],
                            "answer_ref": e["answer_ref"],
                            "evidence_ref": e["evidence_ref"],
                            "quote": e["quote"],
                            "match": e["quote_match"],
                            "snapshot_id": e["source_snapshot_id"],
                        }
                        for e in r["evidence"]
                    ],
                    "reviews": [
                        {
                            "id": v["id"],
                            "reviewer_ref": v["reviewer_ref"],
                            "decision": v["decision"],
                            "judgments": v["judgments"],
                            "created_at": v["created_at"],
                        }
                        for v in r["reviews"]
                    ],
                }
            )
    return {
        "producer_commit": producer_commit,
        "access_level": access_level,
        "metadata_license": metadata_license,
        "papers": papers,
        "findings": records,
    }


@router.get("/{project_id}/findings/export")
async def export_findings(
    project_id: uuid.UUID,
    db: DbDep,
    user: UserDep,
    access_level: Literal["restricted", "public"] = "restricted",
    metadata_license: str | None = None,
) -> Response:
    """LEAD-only deterministic source-evidence packet of complete reviewed revision chains.

    Restricted by default. ``public`` is an explicit choice and needs a packet-metadata
    licence. Quotations are included only for articles whose latest rights declaration
    allows redistribution under an allowlisted licence; every other quotation stays withheld.
    """
    from app.finding_packet import build_packet  # lazy: built concurrently

    if await db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await _require_lead(db, project_id, user)
    if metadata_license is not None and metadata_license not in PACKET_METADATA_LICENSES:
        raise HTTPException(
            status_code=422,
            detail="metadata_license must be one of " + ", ".join(PACKET_METADATA_LICENSES),
        )
    if access_level == "public" and metadata_license is None:
        raise HTTPException(status_code=422, detail="a public export requires metadata_license")
    commit = os.environ.get("GIT_COMMIT", "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        # A packet must name the producer code that built it (frozen 0.1.0 contract).
        raise HTTPException(
            status_code=409, detail="packet export requires the server's GIT_COMMIT (40 hex)"
        )
    packet = build_packet(
        await packet_input(
            db,
            project_id,
            producer_commit=commit,
            access_level=access_level,
            metadata_license=metadata_license,
        )
    )
    return Response(
        content=_zip_bytes(dict(packet.files)),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="source-evidence-packet.zip"'},
    )
