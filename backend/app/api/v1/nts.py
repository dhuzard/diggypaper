"""NTS publication traceability endpoints (issue #11), under ``/projects/{id}/nts``.

Setup turns a project into an NTS project (question + fields + extractor model); the ordinary
inference endpoint then runs ``nts-deterministic-extractor-v2``. Proposals, ALURES sync and the
join derive data from stored predictions; adjudications are the only writes a human makes here
(finding reviews stay on the findings endpoints).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.findings import WithdrawalBody, _load, _locked_finding, add_withdrawal
from app.api.v1.gold import _require_lead
from app.api.v1.projects import _require_project_member
from app.celery_app import celery_app
from app.db.models import (
    Article,
    ArticlePrediction,
    BackendType,
    ImportJob,
    ImportJobStatus,
    InferenceJob,
    ModelRegistry,
    NtsAdjudication,
    NtsDataset,
    NtsLink,
    User,
)
from app.nts import service
from app.nts.backend import MODEL_IDENTIFIER, MODEL_NAME, MODEL_NAMES
from app.nts.fields import NTS_FIELDS, NTS_QUESTION, is_nts_project
from app.pseudonym import anonymized_actor_ref
from app.schema import project_fields
from app.tasks.nts import nts_alures_sync_task, nts_join_task

router = APIRouter(prefix="/projects", tags=["nts"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User, Depends(get_current_user)]

TEXT_SECTIONS = ["methods", "results"]


class AdjudicationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["confirmed", "rejected", "unresolved"]
    rationale: str = Field(min_length=1, max_length=5000)


class SyncBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offline: bool = False  # re-receipt the files already on disk, no network


async def _ensure_model(db: AsyncSession) -> ModelRegistry:
    model = await db.scalar(select(ModelRegistry).where(ModelRegistry.name == MODEL_NAME))
    if model is None:
        model = ModelRegistry(
            name=MODEL_NAME,
            backend_type=BackendType.DETERMINISTIC,
            model_identifier=MODEL_IDENTIFIER,
            ram_usage_mb=0,
        )
        db.add(model)
        await db.flush()
    elif not model.is_active:
        model.is_active = True
    return model


def _job_payload(job: ImportJob | InferenceJob) -> dict:
    return {
        "id": str(job.id),
        "status": job.status.value if job.status else None,
        "result": job.result_json,
        "error": job.error,
        "created_at": service._iso(job.created_at),
        "completed_at": service._iso(job.completed_at),
    }


@router.get("/{project_id}/nts")
async def status(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    project = await _require_project_member(db, project_id, user)
    predictions = await service.latest_nts_predictions(db, project_id)
    datasets = (
        (
            await db.execute(
                select(NtsDataset)
                .where(NtsDataset.project_id == project_id)
                .order_by(NtsDataset.retrieved_at.desc(), NtsDataset.file_name)
            )
        )
        .scalars()
        .all()
    )
    links = await db.scalar(select(NtsLink.id).where(NtsLink.project_id == project_id).limit(1))
    jobs = (
        (
            await db.execute(
                select(ImportJob)
                .where(ImportJob.project_id == project_id, ImportJob.kind.like("nts-%"))
                .order_by(ImportJob.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    extraction = await db.scalar(
        select(InferenceJob)
        .where(InferenceJob.project_id == project_id, InferenceJob.model_used.in_(MODEL_NAMES))
        .order_by(InferenceJob.created_at.desc())
        .limit(1)
    )
    proposed = await service.proposed_count(db, project_id)
    return {
        "is_nts_project": is_nts_project(project_fields(project)),
        "model_name": MODEL_NAME,
        "extracted_articles": len(predictions),
        # The extractor runs as an ordinary inference job; its latest run, so the UI can follow it.
        "extraction_job": _job_payload(extraction) if extraction is not None else None,
        "proposed_findings": proposed,
        "datasets": [_dataset_payload(d) for d in datasets],
        "has_links": links is not None,
        "review_pending": len(await service.review_queue(db, project_id)),
        "jobs": [{"kind": j.kind, **_job_payload(j)} for j in jobs],
    }


@router.post("/{project_id}/nts/setup")
async def setup(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Make this project an NTS traceability project (question, fields, sections, model)."""
    project = await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    fields = project_fields(project)
    if not is_nts_project(fields):
        existing = await db.scalar(
            select(ArticlePrediction.id)
            .join(Article, Article.id == ArticlePrediction.article_id)
            .where(Article.project_id == project_id)
            .limit(1)
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail="project already has predictions for another question",
            )
        project.question = NTS_QUESTION
        project.fields = NTS_FIELDS
    if not project.text_sections:
        project.text_sections = TEXT_SECTIONS
    model = await _ensure_model(db)
    await db.commit()
    return {
        "is_nts_project": True,
        "question": project.question,
        "fields": project.fields,
        "text_sections": project.text_sections,
        "model_name": model.name,
    }


@router.post("/{project_id}/nts/propose", status_code=201)
async def propose(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Create finding proposals (automation proposer) from the latest extractor predictions."""
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    results = []
    for prediction in await service.latest_nts_predictions(db, project_id):
        try:
            results.append(await service.propose_findings(db, project_id, prediction))
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from None
    return {
        "predictions": len(results),
        "created": sum(len(r["created"]) for r in results),
        "skipped": sum(r["skipped"] for r in results),
        "items": results,
    }


def _dataset_payload(d: NtsDataset) -> dict:
    return {
        "id": str(d.id),
        "kind": d.kind,
        "file_name": d.file_name,
        "source_url": d.source_url,
        "retrieved_at": service._iso(d.retrieved_at),
        "http_status": d.http_status,
        "bytes": d.bytes,
        "sha256": d.sha256,
        "row_count": d.row_count,
    }


async def _queue(db: AsyncSession, project_id: uuid.UUID, kind: str, send) -> dict:
    """Record the job as PENDING, then hand it to the worker.

    The row exists before the message is sent, so the page shows the task as queued even when
    no worker picks it up; the worker then updates that same row. One task of a kind at a time.
    """
    active = await db.scalar(
        select(ImportJob.id).where(
            ImportJob.project_id == project_id,
            ImportJob.kind == kind,
            ImportJob.status.in_([ImportJobStatus.PENDING, ImportJobStatus.RUNNING]),
        )
    )
    if active is not None:
        raise HTTPException(status_code=409, detail=f"{kind} already queued or running")
    job = ImportJob(
        project_id=project_id,
        query=kind,
        kind=kind,
        status=ImportJobStatus.PENDING,
        result_json={"phase": "queued"},
    )
    db.add(job)
    await db.commit()
    record_id = str(job.id)
    try:
        result = send(record_id)
    except Exception as exc:  # broker unreachable: say so on the row, not only in the logs
        await db.refresh(job)
        job.status = ImportJobStatus.FAILED
        job.error = f"task queue unavailable: {type(exc).__name__}"
        job.completed_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(status_code=503, detail="task queue unavailable") from None
    if celery_app.conf.task_always_eager:
        status = result.state  # already ran inline and updated the row itself
    else:
        await db.refresh(job)
        if job.job_id is None:  # the worker may already have claimed the row
            job.job_id = result.id
            await db.commit()
        status = "PENDING"
    return {"job_id": result.id, "record_id": record_id, "status": status}


@router.post("/{project_id}/nts/alures/sync", status_code=202)
async def alures_sync(
    project_id: uuid.UUID, db: DbDep, user: UserDep, body: SyncBody | None = None
) -> dict:
    """Acquire the official ALURES exports with receipts (Celery task)."""
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    offline = bool(body and body.offline)
    return await _queue(
        db,
        project_id,
        "nts-alures",
        lambda rid: nts_alures_sync_task.delay(str(project_id), offline, rid),
    )


@router.get("/{project_id}/nts/alures/datasets")
async def alures_datasets(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    rows = (
        (
            await db.execute(
                select(NtsDataset)
                .where(NtsDataset.project_id == project_id)
                .order_by(NtsDataset.retrieved_at.desc(), NtsDataset.file_name)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_dataset_payload(d) for d in rows]}


@router.post("/{project_id}/nts/join", status_code=202)
async def join(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    """Recompute publication-NTS links and count observations (Celery task)."""
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    return await _queue(
        db, project_id, "nts-join", lambda rid: nts_join_task.delay(str(project_id), rid)
    )


@router.get("/{project_id}/nts/links")
async def links(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    return {"items": await service.links(db, project_id)}


@router.get("/{project_id}/nts/observations")
async def observations(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    return {"items": await service.observations(db, project_id)}


@router.get("/{project_id}/nts/summary")
async def summary(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    return {"items": await service.summary(db, project_id)}


@router.get("/{project_id}/nts/review-queue")
async def review_queue(project_id: uuid.UUID, db: DbDep, user: UserDep) -> dict:
    await _require_project_member(db, project_id, user)
    viewer = anonymized_actor_ref(user.email)
    return {"items": await service.review_queue(db, project_id, viewer)}


@router.post("/{project_id}/nts/links/{link_id}/adjudications", status_code=201)
async def adjudicate(
    project_id: uuid.UUID,
    link_id: uuid.UUID,
    body: AdjudicationBody,
    db: DbDep,
    user: UserDep,
) -> dict:
    """Append a human decision on one link; a later decision supersedes, nothing is edited."""
    await _require_project_member(db, project_id, user)
    link = await db.get(NtsLink, link_id)
    if link is None or link.project_id != project_id:
        raise HTTPException(status_code=404, detail="Link not found")
    if link.identifier_system != "ALURES-NTS" or not link.identifier_normalized:
        raise HTTPException(status_code=409, detail="only publication-NTS links are adjudicated")
    db.add(
        NtsAdjudication(
            link_id=link.id,
            decision=body.decision,
            rationale=body.rationale.strip(),
            adjudicator_id=user.id,
            adjudicator_ref=anonymized_actor_ref(user.email),
            created_at=datetime.now(UTC),  # microsecond order; DB now() is second-grained
        )
    )
    await db.commit()
    rows = (
        (
            await db.execute(
                select(NtsAdjudication)
                .where(NtsAdjudication.link_id == link.id)
                .order_by(NtsAdjudication.created_at, NtsAdjudication.id)
            )
        )
        .scalars()
        .all()
    )
    return service.link_payload(link, rows)


class RetireBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    apply: bool = False  # false: only report what would be withdrawn


@router.post("/{project_id}/nts/retire-superseded")
async def retire_superseded(
    project_id: uuid.UUID, body: RetireBody, db: DbDep, user: UserDep
) -> dict:
    """LEAD: withdraw approved findings of a superseded extraction run that the newest run
    proposed again with the identical statement (append-only finding withdrawals).

    Re-running the extractor proposes the findings again on a new source snapshot; the
    reviewed copies of the older run then put the paper on two snapshots and the packet
    withholds all of them. ``apply: false`` (default) only returns the plan.
    """
    await _require_project_member(db, project_id, user)
    await _require_lead(db, project_id, user)
    current = {str(p.id) for p in await service.latest_nts_predictions(db, project_id)}
    plan = service.superseded_withdrawal_plan(await _load(db, project_id), current)
    if body.apply:
        for item in plan["candidates"]:
            finding = await _locked_finding(
                db, project_id, uuid.UUID(item["article_id"]), uuid.UUID(item["finding_id"])
            )
            reason = WithdrawalBody(
                reason="superseded-extraction",
                superseded_by=uuid.UUID(item["superseded_by"]),
                rationale=(
                    f"Superseded by finding {item['superseded_by']}: the newest extraction run "
                    "proposed the identical statement on the article's current source snapshot."
                ),
            )
            await add_withdrawal(db, project_id, finding, item["revision"], reason, user)
        await db.commit()
    return {"applied": body.apply, **plan}
