"""Opt-in real upgrade test. SEIP_TEST_POSTGRES_URL must allow temporary databases.

Each run creates/drops its own UUID-named database; it never resets the supplied
database. Run against a disposable PostgreSQL 16 service, not production.
"""

import asyncio
import copy
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_provenance import numeric_envelopes, prepared

from app.db.models import (
    Article,
    ArticlePrediction,
    BenchmarkRun,
    HumanAnnotation,
    InferenceJob,
    Invocation,
    Project,
    Search,
    SourceSnapshot,
)
from app.evidence_prediction import prediction_envelope
from app.provenance import (
    MAX_RAW_BYTES,
    Reconciliation,
    ResponseCapture,
    SourceSnapshotV1,
    complete_invocation,
    digest,
    invocation_from_record,
    invocation_record,
    prepare_invocation,
    review_binding,
    source_from_record,
    source_record,
)
from app.provenance_capture import capture_article_source
from app.quote_checks import check_prediction_quotes

BACKEND = Path(__file__).resolve().parents[1]
DATA = json.loads((Path(__file__).parent / "fixtures/provenance_v1.json").read_text("utf-8"))
pytestmark = pytest.mark.skipif(
    not os.environ.get("SEIP_TEST_POSTGRES_URL"), reason="requires disposable PostgreSQL admin URL"
)


async def alembic(url, action, revision, *, success=True):
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", action, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == success, result.stderr[-6000:]
    return result


@pytest.fixture
async def postgres_database():
    url = make_url(os.environ["SEIP_TEST_POSTGRES_URL"])
    name = "seip_storage_test_" + uuid.uuid4().hex
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    test_url = url.set(database=name).render_as_string(hide_password=False)
    engine = create_async_engine(test_url)
    try:
        yield test_url, engine
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            await connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        await admin.dispose()


async def test_populated_postgres_upgrade_downgrade_and_storage_guards(postgres_database):
    url, engine = postgres_database
    await alembic(url, "upgrade", "0021")
    source = SourceSnapshotV1.model_validate(DATA["snapshot"])
    pid, aid = source.project_id, source.article_id
    legacy_pred, legacy_review, run, legacy_bench = [uuid.uuid4() for _ in range(4)]
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO projects(id,title) VALUES (:id,'Legacy')"), {"id": pid}
        )
        await connection.execute(
            text(
                "INSERT INTO articles(id,project_id,title,abstract) "
                "VALUES (:id,:pid,'Old title','Old abstract')"
            ),
            {"id": aid, "pid": pid},
        )
        await connection.execute(
            text(
                "INSERT INTO article_predictions(id,article_id,model_used,decision_json) "
                "VALUES (:id,:aid,'old-model',jsonb_build_object('old',true))"
            ),
            {"id": legacy_pred, "aid": aid},
        )
        await connection.execute(
            text(
                "INSERT INTO human_annotations(id,article_id,decision_json) "
                "VALUES (:id,:aid,jsonb_build_object('old_review',true))"
            ),
            {"id": legacy_review, "aid": aid},
        )
        await connection.execute(
            text("INSERT INTO benchmark_runs(id,project_id) VALUES (:id,:pid)"),
            {"id": run, "pid": pid},
        )
        await connection.execute(
            text(
                "INSERT INTO benchmark_predictions"
                "(id,benchmark_run_id,article_id,model_name,decision_json) "
                "VALUES (:id,:run,:aid,'old-model',jsonb_build_object('old_benchmark',true))"
            ),
            {"id": legacy_bench, "run": run, "aid": aid},
        )
        assert await connection.scalar(text("SELECT gen_random_uuid()")) is not None
    await alembic(url, "upgrade", "head")
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM source_snapshots")) == 0
        for table in ("article_predictions", "human_annotations", "benchmark_predictions"):
            row = (
                await connection.execute(
                    text(
                        "SELECT decision_json,source_snapshot_id,provenance_project_id "
                        f"FROM {table}"
                    )
                )
            ).one()
            assert (
                row.decision_json
                and row.source_snapshot_id is None
                and row.provenance_project_id is None
            )
    # A rollback with populated legacy tables and zero captured sources loses no rows.
    await alembic(url, "downgrade", "0021")
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT decision_json FROM article_predictions")) == {
            "old": True
        }
        assert await connection.scalar(text("SELECT count(*) FROM human_annotations")) == 1
    await alembic(url, "upgrade", "head")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(source_record(source))
        await session.commit()
        values = copy.deepcopy(DATA["invocations"][0]["envelope"])
        for key in ("project_id", "article_id", "source_snapshot_id", "request_sha256"):
            values.pop(key)
        invocation = invocation_record(prepare_invocation(source, **values))
        session.add(invocation)
        await session.commit()
        complete_invocation(invocation, "outcome_unknown")
        await session.commit()
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    update(Invocation)
                    .where(Invocation.id == invocation.id)
                    .values(
                        outcome="succeeded",
                        completed_at=datetime.now(UTC),
                        response_capture={
                            "origin": "driver_parsed",
                            "raw_availability": "unavailable",
                        },
                        reconciliation={
                            "actor_ref": "test",
                            "reason": "claimed recovery",
                            "recorded_at": datetime.now(UTC).isoformat(),
                        },
                    )
                )
        complete_invocation(
            invocation,
            "succeeded",
            response=ResponseCapture(
                origin="driver_parsed",
                raw_availability="unavailable",
                parsed={"answer": True},
                reason="actual recovered parsed output; raw bytes unavailable",
            ),
            reconciliation=Reconciliation(
                actor_ref="operator:test", reason="recovered actual call result"
            ),
        )
        await session.commit()
        bound = ArticlePrediction(
            article_id=aid,
            model_used="new-model",
            provenance_project_id=pid,
            source_snapshot_id=source.id,
            invocation_id=invocation.id,
            invocation_group_id=invocation.group_id,
        )
        review = HumanAnnotation(article_id=aid, **review_binding(source, source.views[1].id))
        session.add_all([bound, review])
        await session.commit()
        numeric_source, numeric_call = numeric_envelopes()
        numeric_source = SourceSnapshotV1.model_validate(
            {**numeric_source.model_dump(mode="json"), "id": str(uuid.uuid4())}
        )
        session.add(source_record(numeric_source))
        await session.commit()
        numeric_values = numeric_call.model_dump()
        for key in ("project_id", "article_id", "source_snapshot_id", "request_sha256"):
            numeric_values.pop(key)
        numeric_values.update(id=uuid.uuid4(), group_id=uuid.uuid4())
        numeric_call = prepare_invocation(numeric_source, **numeric_values)
        numeric_row = invocation_record(numeric_call)
        session.add(numeric_row)
        await session.commit()
        loaded_source = await session.get(SourceSnapshot, numeric_source.id, populate_existing=True)
        await session.refresh(numeric_row)
        assert digest(loaded_source.payload) != numeric_source.sha256
        assert digest(numeric_row.request_envelope) != numeric_row.envelope_sha256
        assert source_from_record(loaded_source).sha256 == numeric_source.sha256
        assert invocation_from_record(numeric_row).request_sha256 == numeric_call.request_sha256
        assert source_from_record(loaded_source).model_dump(
            mode="json"
        ) == numeric_source.model_dump(mode="json")
        complete_invocation(
            numeric_row,
            "succeeded",
            response=ResponseCapture(
                origin="driver_parsed", raw_availability="not_captured", parsed={"actual": True}
            ),
        )
        await session.commit()
        standard = Search(project_id=pid, name="Standard", is_default=True)
        blind = Search(project_id=pid, name="Blind", kind="blind")
        session.add_all([standard, blind])
        await session.flush()
        for owner_class, key in (
            (InferenceJob, "inference_job_id"),
            (BenchmarkRun, "benchmark_run_id"),
        ):
            owners = []
            for search_id in (standard.id, None):
                args = {"project_id": pid, "search_id": search_id}
                if owner_class is InferenceJob:
                    args["model_used"] = "test"
                owner = owner_class(**args)
                session.add(owner)
                owners.append(owner)
            await session.flush()
            for owner, search_id, allowed in (
                (owners[0], standard.id, True),
                (owners[0], blind.id, False),
                (owners[0], None, False),
                (owners[1], standard.id, False),
                (owners[1], None, True),
            ):
                candidate = invocation_record(
                    prepared(
                        source,
                        id=uuid.uuid4(),
                        group_id=uuid.uuid4(),
                        search_id=search_id,
                        **{key: owner.id},
                    )
                )
                statement = insert(Invocation).values(
                    **{c.name: getattr(candidate, c.name) for c in Invocation.__table__.columns}
                )
                if allowed:
                    await session.execute(statement)
                else:
                    with pytest.raises(DBAPIError):
                        async with session.begin_nested():
                            await session.execute(statement)
            for target in owners:
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        await session.execute(
                            update(owner_class)
                            .where(owner_class.id == target.id)
                            .values(search_id=blind.id)
                        )
        await session.commit()
        for origin in ("driver_parsed", "unavailable"):
            for raw_state in ("retained", "unavailable", "not_captured"):
                candidate = invocation_record(
                    prepared(source, id=uuid.uuid4(), group_id=uuid.uuid4())
                )
                session.add(candidate)
                await session.commit()
                fake = {
                    "origin": origin,
                    "raw_availability": raw_state,
                    "raw_base64": "FAKE",
                    "raw_sha256": "bogus",
                    "parsed": {} if origin == "driver_parsed" else None,
                    "reason": "unavailable",
                }
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        await session.execute(
                            update(Invocation)
                            .where(Invocation.id == candidate.id)
                            .values(
                                outcome="succeeded",
                                completed_at=datetime.now(UTC),
                                response_capture=fake,
                            )
                        )
        other_project = Project(title="Other")
        session.add(other_project)
        await session.flush()
        other_article = Article(project_id=other_project.id)
        session.add(other_article)
        await session.commit()
        statements = [
            update(SourceSnapshot)
            .where(SourceSnapshot.id == source.id)
            .values(snapshot_sha256="0" * 64),
            update(Invocation)
            .where(Invocation.id == invocation.id)
            .values(request_envelope={"forged": True}),
            update(Invocation).where(Invocation.id == invocation.id).values(canonical_bytes=b"{}"),
            update(Invocation).where(Invocation.id == invocation.id).values(outcome="failed"),
            update(ArticlePrediction)
            .where(ArticlePrediction.id == bound.id)
            .values(
                source_snapshot_id=None,
                provenance_project_id=None,
                invocation_id=None,
                invocation_group_id=None,
            ),
            update(HumanAnnotation)
            .where(HumanAnnotation.id == review.id)
            .values(source_view_id=uuid.uuid4()),
            update(ArticlePrediction)
            .where(ArticlePrediction.id == legacy_pred)
            .values(source_snapshot_id=source.id, provenance_project_id=pid),
            insert(ArticlePrediction).values(
                article_id=other_article.id,
                provenance_project_id=pid,
                source_snapshot_id=source.id,
                model_used="wrong scope",
            ),
            insert(ArticlePrediction).values(
                article_id=aid,
                provenance_project_id=other_project.id,
                source_snapshot_id=source.id,
                model_used="wrong scope",
            ),
            delete(SourceSnapshot).where(SourceSnapshot.id == source.id),
        ]
        for statement in statements:
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(statement)
        for change in (
            {"canonical_bytes": b"{}"},
            {"payload": {**source.model_dump(mode="json"), "doi": "wrong"}},
        ):
            candidate = source_record(source)
            columns = {c.name: getattr(candidate, c.name) for c in SourceSnapshot.__table__.columns}
            columns.update(id=uuid.uuid4(), **change)
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(insert(SourceSnapshot).values(**columns))
        # Core SQL bypasses constructor checks: PostgreSQL still enforces raw digest/cap.
        for data, checksum in ((b"wrong", "a" * 64), (b"x" * (MAX_RAW_BYTES + 1), None)):
            row = source_record(source)
            columns = {
                column.name: getattr(row, column.name)
                for column in SourceSnapshot.__table__.columns
            }
            columns.update(
                id=uuid.uuid4(),
                raw_data=data,
                raw_availability="retained",
                raw_sha256=checksum or hashlib.sha256(data).hexdigest(),
                raw_media_type="application/pdf",
            )
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(insert(SourceSnapshot).values(**columns))
        assert (
            await session.scalar(
                select(ArticlePrediction.source_snapshot_id).where(
                    ArticlePrediction.id == legacy_pred
                )
            )
            is None
        )
    blocked = await alembic(url, "downgrade", "0021", success=False)
    assert "refusing to discard retained provenance" in blocked.stderr
    async with engine.begin() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0025"
        assert await connection.scalar(text("SELECT count(*) FROM source_snapshots")) == 2
        # Explicit ownership deletion remains the documented retention boundary.
        await connection.execute(delete(Project).where(Project.id == pid))
        assert await connection.scalar(select(SourceSnapshot.id)) is None
        assert await connection.scalar(select(Invocation.id)) is None


@pytest.mark.parametrize(
    "owner_class,owner_key",
    [
        (InferenceJob, "inference_job_id"),
        (BenchmarkRun, "benchmark_run_id"),
    ],
)
async def test_parent_search_and_invocation_insert_serialize(
    postgres_database, owner_class, owner_key
):
    """A concurrent parent retarget cannot escape the child insert guard."""
    url, engine = postgres_database
    await alembic(url, "upgrade", "head")
    source = SourceSnapshotV1.model_validate(DATA["snapshot"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Project(id=source.project_id, title="Concurrency"))
        await session.flush()
        session.add(Article(id=source.article_id, project_id=source.project_id))
        original = Search(project_id=source.project_id, name="Original")
        other = Search(project_id=source.project_id, name="Blind", kind="blind")
        session.add_all([original, other])
        await session.flush()
        session.add(source_record(source))
        await session.commit()

        async def owner():
            values = {"project_id": source.project_id, "search_id": original.id}
            if owner_class is InferenceJob:
                values["model_used"] = "test"
            row = owner_class(**values)
            session.add(row)
            await session.commit()
            return row.id

        def child(owner_id):
            row = invocation_record(
                prepared(
                    source,
                    id=uuid.uuid4(),
                    group_id=uuid.uuid4(),
                    search_id=original.id,
                    **{owner_key: owner_id},
                )
            )
            return insert(Invocation).values(
                **{
                    column.name: getattr(row, column.name)
                    for column in Invocation.__table__.columns
                }
            )

        # Child wins the row lock: the parent UPDATE must wait, then its
        # immutable-parent trigger rejects the retarget after child commit.
        first_id = await owner()
        async with engine.connect() as child_conn, engine.connect() as parent_conn:
            child_tx = await child_conn.begin()
            await child_conn.execute(child(first_id))
            parent_tx = await parent_conn.begin()
            await parent_conn.execute(text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises(DBAPIError):
                await parent_conn.execute(
                    update(owner_class).where(owner_class.id == first_id).values(search_id=other.id)
                )
            await parent_tx.rollback()
            await child_tx.commit()
        async with engine.begin() as connection:
            with pytest.raises(DBAPIError):
                async with connection.begin_nested():
                    await connection.execute(
                        update(owner_class)
                        .where(owner_class.id == first_id)
                        .values(search_id=other.id)
                    )

        # Parent wins: a child INSERT waits on its locked row, and after the
        # parent commits the child must see the new search and reject the link.
        second_id = await owner()
        async with engine.connect() as parent_conn, engine.connect() as child_conn:
            parent_tx = await parent_conn.begin()
            await parent_conn.execute(
                update(owner_class).where(owner_class.id == second_id).values(search_id=other.id)
            )
            child_tx = await child_conn.begin()
            await child_conn.execute(text("SET LOCAL lock_timeout = '200ms'"))
            with pytest.raises(DBAPIError):
                await child_conn.execute(child(second_id))
            await child_tx.rollback()
            await parent_tx.commit()
        async with engine.begin() as connection:
            with pytest.raises(DBAPIError):
                async with connection.begin_nested():
                    await connection.execute(child(second_id))


async def test_capture_source_serializes_with_article_replacement(postgres_database):
    """A captured text/PDF revision is complete before a writer replaces it."""
    url, engine = postgres_database
    await alembic(url, "upgrade", "head")
    project_id, article_id = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO projects(id,title) VALUES (:id,'Capture')"),
            {"id": project_id},
        )
        await connection.execute(
            text(
                "INSERT INTO articles(id,project_id,methods_text) "
                "VALUES (:id,:project_id,'old methods')"
            ),
            {"id": article_id, "project_id": project_id},
        )
        await connection.execute(
            text("INSERT INTO article_files(id,article_id,data) VALUES (:id,:article_id,:data)"),
            {"id": uuid.uuid4(), "article_id": article_id, "data": b"old PDF"},
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as capture_session, engine.connect() as writer:
        old, _ = await capture_article_source(
            capture_session,
            project_id=project_id,
            article_id=article_id,
            text_sections=["methods"],
        )
        writer_tx = await writer.begin()
        await writer.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(DBAPIError):
            await writer.execute(
                update(Article).where(Article.id == article_id).values(methods_text="new methods")
            )
        await writer_tx.rollback()
        await capture_session.commit()
    async with engine.begin() as writer:
        await writer.execute(
            update(Article).where(Article.id == article_id).values(methods_text="new methods")
        )
        await writer.execute(
            text("UPDATE article_files SET data = :data WHERE article_id = :article_id"),
            {"data": b"new PDF", "article_id": article_id},
        )
    async with factory() as capture_session:
        new, _ = await capture_article_source(
            capture_session,
            project_id=project_id,
            article_id=article_id,
            text_sections=["methods"],
        )
        await capture_session.commit()
    assert old.components.methods_text == "old methods"
    assert old.raw_artifact.sha256 == hashlib.sha256(b"old PDF").hexdigest()
    assert new.components.methods_text == "new methods"
    assert new.raw_artifact.sha256 == hashlib.sha256(b"new PDF").hexdigest()
    assert old.id != new.id


async def test_quote_checks_upgrade_keeps_legacy_null_and_guarded_downgrade(postgres_database):
    url, engine = postgres_database
    await alembic(url, "upgrade", "head")
    source = SourceSnapshotV1.model_validate(DATA["snapshot"])
    project_id, article_id, prediction_id = source.project_id, source.article_id, uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO projects(id,title) VALUES (:id,'Legacy')"),
            {"id": project_id},
        )
        await connection.execute(
            text("INSERT INTO articles(id,project_id) VALUES (:id,:project_id)"),
            {"id": article_id, "project_id": project_id},
        )
        await connection.execute(
            text(
                "INSERT INTO article_predictions(id,article_id,model_used) "
                "VALUES (:id,:article_id,'legacy')"
            ),
            {"id": prediction_id, "article_id": article_id},
        )
        assert (
            await connection.scalar(
                text("SELECT quote_checks_json FROM article_predictions WHERE id = :id"),
                {"id": prediction_id},
            )
            is None
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(source_record(source))
        await session.commit()
        call = invocation_record(prepared(source))
        session.add(call)
        await session.commit()
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    insert(ArticlePrediction).values(
                        article_id=article_id,
                        model_used="prepared-without-checks",
                        provenance_project_id=project_id,
                        source_snapshot_id=source.id,
                        invocation_id=call.id,
                        invocation_group_id=call.group_id,
                    )
                )
        decision = {"evidence_quotes": ["Mice α."]}
        pending_check = ArticlePrediction(
            article_id=article_id,
            model_used="pending",
            provenance_project_id=project_id,
            source_snapshot_id=source.id,
            invocation_id=call.id,
            invocation_group_id=call.group_id,
            quote_checks_json=check_prediction_quotes(invocation_from_record(call), decision),
        )
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                session.add(pending_check)
                await session.flush()
        complete_invocation(
            call,
            "succeeded",
            response=ResponseCapture(
                origin="driver_parsed", raw_availability="not_captured", parsed={"actual": True}
            ),
        )
        await session.commit()
        checked = ArticlePrediction(
            article_id=article_id,
            model_used="new",
            decision_json=decision,
            evidence_quotes=decision["evidence_quotes"],
            provenance_project_id=project_id,
            source_snapshot_id=source.id,
            invocation_id=call.id,
            invocation_group_id=call.group_id,
            quote_checks_json=check_prediction_quotes(invocation_from_record(call), decision),
        )
        session.add(checked)
        await session.commit()
        for mutation in (
            {"quote_checks_json": None},
            {"decision_json": {"evidence_quotes": ["changed"]}},
            {"evidence_quotes": ["changed"]},
        ):
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(
                        update(ArticlePrediction)
                        .where(ArticlePrediction.id == checked.id)
                        .values(**mutation)
                    )
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    insert(ArticlePrediction).values(
                        article_id=article_id,
                        model_used="forged",
                        provenance_project_id=project_id,
                        source_snapshot_id=source.id,
                        invocation_id=call.id,
                        invocation_group_id=call.group_id,
                        quote_checks_json={
                            **checked.quote_checks_json,
                            "input_ref": str(uuid.uuid4()),
                        },
                    )
                )
    refused = await alembic(url, "downgrade", "0022", success=False)
    assert "refusing to discard persisted quotation checks" in refused.stderr
    async with factory() as session:
        await session.execute(delete(ArticlePrediction).where(ArticlePrediction.id == checked.id))
        await session.commit()
    await alembic(url, "downgrade", "0022")


async def test_prediction_evidence_graph_scope_immutability_and_guarded_downgrade(
    postgres_database,
):
    url, engine = postgres_database
    await alembic(url, "upgrade", "head")
    source = SourceSnapshotV1.model_validate(DATA["snapshot"])
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO projects(id,title) VALUES (:id,'Evidence')"),
            {"id": source.project_id},
        )
        await connection.execute(
            text("INSERT INTO articles(id,project_id) VALUES (:id,:project_id)"),
            {"id": source.article_id, "project_id": source.project_id},
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(source_record(source))
        await session.commit()
        call = invocation_record(prepared(source))
        session.add(call)
        await session.commit()
        complete_invocation(
            call,
            "succeeded",
            response=ResponseCapture(
                origin="driver_parsed", raw_availability="not_captured", parsed={"actual": True}
            ),
        )
        await session.commit()
        decision = {"evidence_quotes": ["Mice α."]}
        checks = check_prediction_quotes(invocation_from_record(call), decision)
        prediction_id = uuid.uuid4()
        graph = prediction_envelope(
            prediction_id=prediction_id,
            project_id=source.project_id,
            article_id=source.article_id,
            project=SimpleNamespace(question=None, fields=None),
            decision=decision,
            invocation_id=call.id,
            source_snapshot_id=source.id,
            quote_checks=checks,
        )
        assert graph["envelope"]["state"] == "legacy_unlinked"
        row = ArticlePrediction(
            id=prediction_id,
            article_id=source.article_id,
            model_used="chat",
            decision_json=decision,
            evidence_quotes=decision["evidence_quotes"],
            quote_checks_json=checks,
            evidence_graph_json=graph,
            provenance_project_id=source.project_id,
            source_snapshot_id=source.id,
            invocation_id=call.id,
            invocation_group_id=call.group_id,
        )
        session.add(row)
        await session.commit()
        for mutation in (
            {"evidence_graph_json": None},
            {"decision_json": {"evidence_quotes": []}},
        ):
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(
                        update(ArticlePrediction)
                        .where(ArticlePrediction.id == prediction_id)
                        .values(**mutation)
                    )
        forged = copy.deepcopy(graph)
        forged["envelope"]["scope"]["article_ref"] = str(uuid.uuid4())
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    insert(ArticlePrediction).values(
                        article_id=source.article_id,
                        model_used="forged",
                        quote_checks_json=checks,
                        evidence_graph_json=forged,
                        provenance_project_id=source.project_id,
                        source_snapshot_id=source.id,
                        invocation_id=call.id,
                        invocation_group_id=call.group_id,
                    )
                )
    refused = await alembic(url, "downgrade", "0024", success=False)
    assert "refusing to discard persisted answer/evidence graphs" in refused.stderr
    async with factory() as session:
        await session.execute(
            delete(ArticlePrediction).where(ArticlePrediction.id == prediction_id)
        )
        await session.commit()
    await alembic(url, "downgrade", "0024")
