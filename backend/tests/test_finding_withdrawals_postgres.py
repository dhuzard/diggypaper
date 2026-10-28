"""Opt-in PostgreSQL validation of migration f0036 (finding withdrawals).

Real Alembic upgrade/downgrade on a temporary database created from SEIP_TEST_POSTGRES_URL:
scope foreign keys (successor and revision cannot come from another project or finding),
CHECK constraints, the append-only trigger, actor-FK nulling and the refusing downgrade.
"""

import os
import uuid

import pytest
from test_finding_curation_postgres import REF, _exec, _refused, _seed, _tables_and_triggers
from test_provenance_migration import alembic
from test_review_binding_postgres import postgres_database  # noqa: F401 (fixture)

pytestmark = pytest.mark.skipif(
    not os.environ.get("SEIP_TEST_POSTGRES_URL"), reason="requires disposable PostgreSQL"
)


async def _finding(engine, project, article, actor):
    finding, revision = uuid.uuid4(), uuid.uuid4()
    await _exec(
        engine,
        "INSERT INTO findings (id, project_id, article_id) VALUES (:id, :p, :a)",
        id=finding,
        p=project,
        a=article,
    )
    await _exec(
        engine,
        "INSERT INTO finding_revisions (id, finding_id, revision, content_json, proposer_id, "
        'proposer_ref) VALUES (:id, :f, 1, CAST(\'{"statement": "s"}\' AS jsonb), :u, :ref)',
        id=revision,
        f=finding,
        u=actor,
        ref=REF,
    )
    return finding, revision


async def test_finding_withdrawals_migration(postgres_database):  # noqa: F811
    url, engine = postgres_database
    await alembic(url, "upgrade", "f0035")
    tables, _ = await _tables_and_triggers(engine)
    assert "finding_withdrawals" not in tables
    await alembic(url, "upgrade", "f0036")
    tables, triggers = await _tables_and_triggers(engine)
    assert "finding_withdrawals" in tables and "finding_withdrawals_append_only" in triggers
    # Empty downgrade is reversible.
    await alembic(url, "downgrade", "f0035")
    tables, triggers = await _tables_and_triggers(engine)
    assert "finding_withdrawals" not in tables
    await alembic(url, "upgrade", "head")

    ids = await _seed(engine)
    old, old_rev = await _finding(engine, ids["project"], ids["article"], ids["actor"])
    new, new_rev = await _finding(engine, ids["project"], ids["article"], ids["actor"])
    # Same lead, another project and article: a successor there is out of scope.
    other_project, other_article = uuid.uuid4(), uuid.uuid4()
    await _exec(
        engine,
        "INSERT INTO projects (id, title, created_by, created_at) VALUES (:id, 'B', :o, now())",
        id=other_project,
        o=ids["owner"],
    )
    await _exec(
        engine,
        "INSERT INTO articles (id, project_id, title, doi) VALUES (:id, :p, 'B', '10.1000/b')",
        id=other_article,
        p=other_project,
    )
    foreign, _ = await _finding(engine, other_project, other_article, ids["actor"])

    insert = (
        "INSERT INTO finding_withdrawals (id, project_id, article_id, finding_id, revision_id, "
        "superseded_by_finding_id, reason, rationale, withdrawn_by_id, withdrawn_by_ref) "
        "VALUES (:id, :p, :a, :f, :r, :s, :reason, :why, :u, :ref)"
    )
    good = {
        "p": ids["project"],
        "a": ids["article"],
        "f": old,
        "r": old_rev,
        "s": new,
        "reason": "superseded-extraction",
        "why": "Re-extracted.",
        "u": ids["actor"],
        "ref": REF,
    }
    for bad, constraint in (
        ({"s": foreign}, "fk_finding_withdrawals_successor_scope"),
        (
            {"p": other_project, "a": other_article, "s": foreign},
            "fk_finding_withdrawals_finding_scope",
        ),
        ({"r": new_rev}, "fk_finding_withdrawals_revision_scope"),
        ({"s": old}, "ck_finding_withdrawals_not_self"),
        ({"reason": "obsolete"}, "ck_finding_withdrawals_reason"),
        ({"why": "   "}, "ck_finding_withdrawals_rationale"),
        ({"ref": "short"}, "ck_finding_withdrawals_withdrawn_by_ref"),
    ):
        await _refused(engine, insert, constraint, id=uuid.uuid4(), **{**good, **bad})
    withdrawal = uuid.uuid4()
    await _exec(engine, insert, id=withdrawal, **good)
    await _refused(engine, insert, "uq_finding_withdrawals_revision", id=uuid.uuid4(), **good)
    for column, value in (
        ("rationale", "'edited'"),
        ("reason", "'duplicate'"),
        ("superseded_by_finding_id", f"'{old}'"),
        ("withdrawn_by_ref", f"'{'d' * 64}'"),
    ):
        await _refused(
            engine,
            f"UPDATE finding_withdrawals SET {column} = {value} WHERE id = :id",
            "append-only",
            id=withdrawal,
        )

    # Account deletion nulls the actor FK and nothing else.
    await _exec(engine, "DELETE FROM users WHERE id = :id", id=ids["actor"])
    row = (
        await _exec(
            engine,
            "SELECT withdrawn_by_id, withdrawn_by_ref FROM finding_withdrawals WHERE id = :id",
            id=withdrawal,
        )
    ).one()
    assert row[0] is None and row[1] == REF

    result = await alembic(url, "downgrade", "f0035", success=False)
    assert "refusing to discard finding withdrawals" in result.stderr
    await _exec(engine, "DELETE FROM finding_withdrawals")
    await alembic(url, "downgrade", "f0035")
    await alembic(url, "upgrade", "head")
