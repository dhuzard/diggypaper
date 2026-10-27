"""Opt-in PostgreSQL validation of migrations 0029 (finding curation) and 0030 (rights).

Runs real Alembic upgrades/downgrades on a UUID-named temporary database created from
SEIP_TEST_POSTGRES_URL, and checks the PG-only guards the SQLite test schema approximates:
CHECK constraints, append-only triggers, actor-FK nulling, and refusing downgrades.
"""

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_provenance_migration import alembic
from test_review_binding_postgres import postgres_database  # noqa: F401 (fixture)

pytestmark = pytest.mark.skipif(
    not os.environ.get("SEIP_TEST_POSTGRES_URL"), reason="requires disposable PostgreSQL"
)

REF = "a" * 64


async def _exec(engine, sql, **params):
    async with engine.begin() as connection:
        return await connection.execute(text(sql), params)


async def _refused(engine, sql, match, **params):
    with pytest.raises(DBAPIError, match=match):
        await _exec(engine, sql, **params)


async def _tables_and_triggers(engine):
    tables = {
        row[0]
        for row in (
            await _exec(
                engine,
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
            )
        ).all()
    }
    triggers = {
        row[0]
        for row in (
            await _exec(engine, "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
        ).all()
    }
    return tables, triggers


async def _seed(engine):
    """Owner (project creator) plus an actor whose account is later deleted."""
    ids = {k: uuid.uuid4() for k in ("owner", "actor", "project", "article")}
    for key in ("owner", "actor"):
        await _exec(
            engine,
            "INSERT INTO users (id, email, hashed_password, role, data_consent_given, "
            "can_create_projects, is_active, created_at) VALUES "
            "(:id, :email, 'x', 'RESEARCHER', true, true, true, now())",
            id=ids[key],
            email=f"{key}@lab.fr",
        )
    await _exec(
        engine,
        "INSERT INTO projects (id, title, created_by, created_at) "
        "VALUES (:id, 'PG', :owner, now())",
        id=ids["project"],
        owner=ids["owner"],
    )
    await _exec(
        engine,
        "INSERT INTO articles (id, project_id, title, doi) VALUES (:id, :p, 'A', '10.1000/pg')",
        id=ids["article"],
        p=ids["project"],
    )
    return ids


async def test_finding_curation_and_rights_migrations(postgres_database):  # noqa: F811
    url, engine = postgres_database
    await alembic(url, "upgrade", "0028")  # upstream branch first: the ORM expects its columns
    await alembic(url, "upgrade", "f0028")
    tables, _ = await _tables_and_triggers(engine)
    assert "findings" not in tables

    # 0029: tables and append-only triggers; an empty downgrade is reversible.
    await alembic(url, "upgrade", "f0029")
    tables, triggers = await _tables_and_triggers(engine)
    assert {"findings", "finding_revisions", "finding_evidence", "finding_reviews"} <= tables
    assert {
        "finding_revisions_append_only",
        "finding_reviews_append_only",
        "finding_evidence_append_only",
    } <= triggers
    assert "article_rights_declarations" not in tables
    await alembic(url, "downgrade", "f0028")
    tables, triggers = await _tables_and_triggers(engine)
    assert "findings" not in tables and "finding_revisions_append_only" not in triggers

    await alembic(url, "upgrade", "head")
    tables, triggers = await _tables_and_triggers(engine)
    assert "article_rights_declarations" in tables
    assert "article_rights_declarations_append_only" in triggers
    ids = await _seed(engine)

    # --- 0029 guards ---
    finding, revision = uuid.uuid4(), uuid.uuid4()
    await _exec(
        engine,
        "INSERT INTO findings (id, project_id, article_id) VALUES (:id, :p, :a)",
        id=finding,
        p=ids["project"],
        a=ids["article"],
    )
    insert_revision = (
        "INSERT INTO finding_revisions (id, finding_id, revision, content_json, proposer_id, "
        'proposer_ref) VALUES (:id, :f, :n, CAST(\'{"statement": "s"}\' AS jsonb), :u, :ref)'
    )
    await _exec(engine, insert_revision, id=revision, f=finding, n=1, u=ids["actor"], ref=REF)
    await _refused(
        engine,
        insert_revision,
        "ck_finding_revisions_number",
        id=uuid.uuid4(),
        f=finding,
        n=0,
        u=ids["actor"],
        ref=REF,
    )
    await _refused(
        engine,
        insert_revision,
        "ck_finding_revisions_proposer_ref",
        id=uuid.uuid4(),
        f=finding,
        n=2,
        u=ids["actor"],
        ref="short",
    )
    insert_review = (
        "INSERT INTO finding_reviews (id, revision_id, decision, judgments_json, rationale, "
        "protocol_version, reviewer_id, reviewer_ref) VALUES (:id, :r, :d, CAST('[]' AS jsonb), "
        "'ok', :proto, :u, :ref)"
    )
    await _refused(
        engine,
        insert_review,
        "ck_finding_reviews_decision",
        id=uuid.uuid4(),
        r=revision,
        d="maybe",
        proto="seip-support-v1",
        u=ids["owner"],
        ref="b" * 64,
    )
    await _refused(
        engine,
        insert_review,
        "ck_finding_reviews_protocol",
        id=uuid.uuid4(),
        r=revision,
        d="approve",
        proto="other",
        u=ids["owner"],
        ref="b" * 64,
    )
    await _exec(
        engine,
        insert_review,
        id=uuid.uuid4(),
        r=revision,
        d="approve",
        proto="seip-support-v1",
        u=ids["owner"],
        ref="b" * 64,
    )
    await _refused(
        engine,
        "UPDATE finding_revisions SET content_json = CAST('{}' AS jsonb) WHERE id = :id",
        "append-only",
        id=revision,
    )
    await _refused(
        engine,
        "UPDATE finding_revisions SET proposer_ref = :ref WHERE id = :id",
        "append-only",
        id=revision,
        ref="c" * 64,
    )
    await _refused(engine, "UPDATE finding_reviews SET decision = 'reject'", "append-only")

    # --- 0030 rights declarations ---
    insert_rights = (
        "INSERT INTO article_rights_declarations (id, project_id, article_id, revision, "
        "license_id, redistribution, basis, evidence_url, declared_by_id, declared_by_ref) "
        "VALUES (:id, :p, :a, :n, :lic, :red, :basis, :url, :u, :ref)"
    )
    good = {
        "p": ids["project"],
        "a": ids["article"],
        "n": 1,
        "lic": "CC-BY-4.0",
        "red": "allowed",
        "basis": "Publisher states CC BY 4.0.",
        "url": "https://example.org/licence",
        "u": ids["actor"],
        "ref": REF,
    }
    for bad, constraint in (
        ({"lic": "CC-BY-NC-4.0"}, "ck_article_rights_allowed"),
        ({"lic": "proprietary"}, "ck_article_rights_allowed"),
        ({"url": None}, "ck_article_rights_allowed"),
        ({"url": "http://example.org/licence"}, "ck_article_rights_allowed"),
        ({"red": "public"}, "ck_article_rights_redistribution"),
        ({"basis": "   "}, "ck_article_rights_basis"),
        ({"ref": "short"}, "ck_article_rights_declared_by"),
        ({"n": 0}, "ck_article_rights_revision"),
        ({"a": uuid.uuid4()}, "fk_article_rights_article_scope"),
    ):
        await _refused(engine, insert_rights, constraint, id=uuid.uuid4(), **{**good, **bad})
    declaration = uuid.uuid4()
    await _exec(engine, insert_rights, id=declaration, **good)
    # Non-allowed decisions need no licence evidence.
    await _exec(
        engine,
        insert_rights,
        id=uuid.uuid4(),
        **{**good, "n": 2, "lic": "unknown", "red": "restricted", "url": None},
    )
    await _refused(engine, insert_rights, "uq_article_rights_revision", id=uuid.uuid4(), **good)
    for column, value in (
        ("redistribution", "'restricted'"),
        ("license_id", "'CC0-1.0'"),
        ("basis", "'edited'"),
        ("declared_by_ref", f"'{'d' * 64}'"),
    ):
        await _refused(
            engine,
            f"UPDATE article_rights_declarations SET {column} = {value} WHERE id = :id",
            "append-only",
            id=declaration,
        )

    # Account deletion may null the actor FK on both 0029 and 0030 rows, nothing else.
    await _exec(engine, "DELETE FROM users WHERE id = :id", id=ids["actor"])
    actors = (
        await _exec(
            engine,
            "SELECT (SELECT count(*) FROM finding_revisions WHERE proposer_id IS NULL), "
            "(SELECT count(*) FROM article_rights_declarations WHERE declared_by_id IS NULL)",
        )
    ).one()
    assert tuple(actors) == (1, 2)

    # Downgrades refuse while curated data exists, and succeed once it is gone.
    result = await alembic(url, "downgrade", "f0029", success=False)
    assert "refusing to discard article rights declarations" in result.stderr
    await _exec(engine, "DELETE FROM article_rights_declarations")
    await alembic(url, "downgrade", "f0029")
    result = await alembic(url, "downgrade", "f0028", success=False)
    assert "refusing to discard curated findings" in result.stderr
    await _exec(engine, "DELETE FROM findings")
    await alembic(url, "downgrade", "f0028")
    tables, _ = await _tables_and_triggers(engine)
    assert "findings" not in tables and "article_rights_declarations" not in tables
    await alembic(url, "upgrade", "head")
