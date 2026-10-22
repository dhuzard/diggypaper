"""Celery tasks for NTS traceability: ALURES export acquisition and the exact-ID join.

Same execution pattern as ``import_pubmed_task`` (sync wrapper, dedicated thread,
``asyncio.run``), tracked in ``import_jobs`` with ``kind = nts-alures`` / ``nts-join`` so the
project jobs list shows them.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import UTC, datetime

from celery import current_task

from app import tasks as _tasks
from app.celery_app import celery_app
from app.db.models import ImportJob, ImportJobStatus
from app.nts import service

log = logging.getLogger(__name__)


def _run_in_thread(coro_factory, name: str) -> dict:
    result: dict = {}
    error: BaseException | None = None

    def _run() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(coro_factory())
        except BaseException as exc:  # propagated to the main thread
            error = exc

    thread = threading.Thread(target=_run, name=name)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


async def _tracked(
    project_id: str,
    kind: str,
    job_id: str | None,
    work,
    session_factory=None,
    record_id: str | None = None,
):
    """Run ``work`` under an ``import_jobs`` row: the PENDING row the API created, else a new one.

    Messages queued before the API created rows carry no ``record_id``; they still get a row.
    """
    factory = session_factory or _tasks._session_factory_override  # read at call time
    engine = None
    if factory is None:
        engine, factory = _tasks._make_session_factory()
    try:
        async with factory() as session:
            job = await session.get(ImportJob, uuid.UUID(record_id)) if record_id else None
            if job is None or str(job.project_id) != project_id or job.kind != kind:
                job = ImportJob(project_id=uuid.UUID(project_id), query=kind, kind=kind)
                session.add(job)
            job.job_id = job_id
            job.status = ImportJobStatus.RUNNING
            job.result_json = {"phase": "running"}
            await session.commit()
            try:
                result = await work(session)
                job.status = ImportJobStatus.COMPLETED
                job.result_json = result
            except Exception as exc:
                log.exception("nts task failed (%s, project %s)", kind, project_id)
                job.status = ImportJobStatus.FAILED
                job.error = f"{type(exc).__name__}: {exc}"[:2000]
                job.completed_at = datetime.now(UTC)
                await session.commit()
                raise
            job.completed_at = datetime.now(UTC)
            await session.commit()
            return result
    finally:
        if engine is not None:
            await engine.dispose()


async def _alures_sync(
    project_id: str,
    offline: bool,
    job_id: str | None,
    session_factory=None,
    record_id: str | None = None,
):
    async def work(session):
        return await service.sync_alures(session, uuid.UUID(project_id), offline=offline)

    return await _tracked(project_id, "nts-alures", job_id, work, session_factory, record_id)


async def _join(
    project_id: str, job_id: str | None, session_factory=None, record_id: str | None = None
):
    async def work(session):
        return await service.join_project(session, uuid.UUID(project_id))

    return await _tracked(project_id, "nts-join", job_id, work, session_factory, record_id)


@celery_app.task(name="diggypaper.nts_alures_sync")
def nts_alures_sync_task(
    project_id: str, offline: bool = False, record_id: str | None = None
) -> dict:
    job_id = current_task.request.id
    return _run_in_thread(
        lambda: _alures_sync(project_id, offline, job_id, record_id=record_id), "nts-alures-sync"
    )


@celery_app.task(name="diggypaper.nts_join")
def nts_join_task(project_id: str, record_id: str | None = None) -> dict:
    job_id = current_task.request.id
    return _run_in_thread(lambda: _join(project_id, job_id, record_id=record_id), "nts-join")
