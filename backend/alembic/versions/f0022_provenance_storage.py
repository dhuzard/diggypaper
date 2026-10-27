"""Add immutable source and prepared-call storage; historical links remain NULL.

Revision ID: f0022
Revises: 0021

Production PostgreSQL migration. SQLite tests create portable ORM metadata.
Downgrade deliberately refuses to discard captured provenance; export/delete it
under an explicit retention decision before downgrading an otherwise empty store.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "f0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_SCOPE_TABLES = ("articles", "searches", "inference_jobs", "benchmark_runs")
_LINK_TABLES = ("article_predictions", "benchmark_predictions", "human_annotations")
_FROZEN = (
    "id",
    "project_id",
    "article_id",
    "source_snapshot_id",
    "group_id",
    "parent_id",
    "search_id",
    "inference_job_id",
    "benchmark_run_id",
    "variant",
    "stage",
    "call_kind",
    "sequence",
    "attempt",
    "schema_version",
    "prepared_at",
    "request_sha256",
    "envelope_sha256",
    "request_envelope",
    "canonical_bytes",
)


def _uuid(name, nullable=True):
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def _scope_fk(name, columns, table, targets, *, ondelete=None):
    return sa.ForeignKeyConstraint(
        columns, [f"{table}.{target}" for target in targets], name=name, ondelete=ondelete
    )


def _add_links(table):
    columns = ["provenance_project_id", "source_snapshot_id"]
    if table == "human_annotations":
        columns += ["source_view_id"]
        op.add_column(table, sa.Column("source_view_sha256", sa.String(64)))
    else:
        columns += ["invocation_id", "invocation_group_id"]
    for column in columns:
        op.add_column(table, _uuid(column))
    if table == "benchmark_predictions":
        op.add_column(table, _uuid("search_id"))
        op.create_foreign_key(
            "fk_benchmark_predictions_provenance_run",
            table,
            "benchmark_runs",
            ["benchmark_run_id", "provenance_project_id"],
            ["id", "project_id"],
        )
    op.create_foreign_key(
        f"fk_{table}_provenance_article",
        table,
        "articles",
        ["article_id", "provenance_project_id"],
        ["id", "project_id"],
    )
    op.create_foreign_key(
        f"fk_{table}_source",
        table,
        "source_snapshots",
        ["source_snapshot_id", "provenance_project_id", "article_id"],
        ["id", "project_id", "article_id"],
    )
    op.create_foreign_key(
        f"fk_{table}_provenance_search",
        table,
        "searches",
        ["search_id", "provenance_project_id"],
        ["id", "project_id"],
    )
    op.create_check_constraint(
        f"ck_{table}_source_scope",
        table,
        "(source_snapshot_id IS NULL AND provenance_project_id IS NULL) OR "
        "(source_snapshot_id IS NOT NULL AND provenance_project_id IS NOT NULL "
        "AND article_id IS NOT NULL)",
    )
    op.create_index(f"ix_{table}_source", table, ["source_snapshot_id"])
    if table == "human_annotations":
        op.create_check_constraint(
            "ck_human_annotations_source_view",
            table,
            "(source_snapshot_id IS NULL AND source_view_id IS NULL "
            "AND source_view_sha256 IS NULL) "
            "OR (source_snapshot_id IS NOT NULL AND source_view_id IS NOT NULL "
            "AND source_view_sha256 IS NOT NULL)",
        )
    else:
        op.create_foreign_key(
            f"fk_{table}_invocation",
            table,
            "invocations",
            ["invocation_id", "source_snapshot_id", "article_id", "invocation_group_id"],
            ["id", "source_snapshot_id", "article_id", "group_id"],
        )
        op.create_check_constraint(
            f"ck_{table}_invocation_scope",
            table,
            "(invocation_id IS NULL AND invocation_group_id IS NULL) OR "
            "(invocation_id IS NOT NULL AND invocation_group_id IS NOT NULL "
            "AND source_snapshot_id IS NOT NULL)",
        )
        op.create_index(f"ix_{table}_invocation", table, ["invocation_id"])


def upgrade():
    for table in _SCOPE_TABLES:
        op.create_unique_constraint(f"uq_{table}_id_project", table, ["id", "project_id"])
    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid("project_id", False),
        _uuid("article_id", False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("raw_availability", sa.String(), nullable=False),
        sa.Column("raw_sha256", sa.String(64)),
        sa.Column("raw_media_type", sa.String()),
        sa.Column("raw_data", sa.LargeBinary()),
        sa.UniqueConstraint("id", "project_id", "article_id", name="uq_source_snapshots_scope"),
        _scope_fk(
            "fk_source_snapshots_article_scope",
            ["article_id", "project_id"],
            "articles",
            ["id", "project_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_source_snapshots_version"),
        sa.CheckConstraint(
            "length(canonical_bytes) <= 67108864", name="ck_source_snapshots_canonical_size"
        ),
        sa.CheckConstraint(
            "encode(sha256(canonical_bytes),'hex') = snapshot_sha256 AND "
            "convert_from(canonical_bytes,'UTF8')::jsonb = payload",
            name="ck_source_snapshots_canonical_integrity",
        ),
        sa.CheckConstraint(
            "raw_availability IN ('retained','digest_only','not_captured','unavailable')",
            name="ck_source_snapshots_raw_availability",
        ),
        sa.CheckConstraint(
            "(raw_availability = 'retained' AND raw_data IS NOT NULL) OR "
            "(raw_availability <> 'retained' AND raw_data IS NULL)",
            name="ck_source_snapshots_raw_retention",
        ),
        sa.CheckConstraint(
            "raw_data IS NULL OR length(raw_data) <= 26214400", name="ck_source_snapshots_raw_size"
        ),
        sa.CheckConstraint(
            "raw_availability NOT IN ('retained','digest_only') OR "
            "(raw_sha256 IS NOT NULL AND length(raw_sha256) = 64 AND raw_media_type IS NOT NULL)",
            name="ck_source_snapshots_raw_digest",
        ),
        sa.CheckConstraint(
            "raw_availability <> 'not_captured' OR raw_sha256 IS NULL",
            name="ck_source_snapshots_not_captured",
        ),
        sa.CheckConstraint(
            "raw_data IS NULL OR encode(sha256(raw_data), 'hex') = raw_sha256",
            name="ck_source_snapshots_raw_bytes_digest",
        ),
    )
    op.create_index(
        "ix_source_snapshots_article",
        "source_snapshots",
        ["project_id", "article_id", "captured_at"],
    )
    op.create_table(
        "invocations",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        *[
            _uuid(name, False)
            for name in ("project_id", "article_id", "source_snapshot_id", "group_id")
        ],
        *[
            _uuid(name)
            for name in ("parent_id", "search_id", "inference_job_id", "benchmark_run_id")
        ],
        sa.Column("variant", sa.String()),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("call_kind", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("envelope_sha256", sa.String(64), nullable=False),
        sa.Column("request_envelope", JSONB(), nullable=False),
        sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("response_capture", JSONB()),
        sa.Column("reconciliation", JSONB()),
        sa.UniqueConstraint(
            "id", "source_snapshot_id", "article_id", "group_id", name="uq_invocations_result"
        ),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "article_id",
            "source_snapshot_id",
            "group_id",
            name="uq_invocations_parent_scope",
        ),
        sa.UniqueConstraint("group_id", "sequence", "attempt", name="uq_invocations_attempt"),
        _scope_fk(
            "fk_invocations_article_scope",
            ["article_id", "project_id"],
            "articles",
            ["id", "project_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_invocations_source_scope",
            ["source_snapshot_id", "project_id", "article_id"],
            "source_snapshots",
            ["id", "project_id", "article_id"],
        ),
        _scope_fk(
            "fk_invocations_search_scope",
            ["search_id", "project_id"],
            "searches",
            ["id", "project_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_invocations_job_scope",
            ["inference_job_id", "project_id"],
            "inference_jobs",
            ["id", "project_id"],
        ),
        _scope_fk(
            "fk_invocations_run_scope",
            ["benchmark_run_id", "project_id"],
            "benchmark_runs",
            ["id", "project_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_invocations_parent_scope",
            ["parent_id", "project_id", "article_id", "source_snapshot_id", "group_id"],
            "invocations",
            ["id", "project_id", "article_id", "source_snapshot_id", "group_id"],
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_invocations_version"),
        sa.CheckConstraint(
            "length(canonical_bytes) <= 67108864", name="ck_invocations_canonical_size"
        ),
        sa.CheckConstraint(
            "encode(sha256(canonical_bytes),'hex') = envelope_sha256 AND "
            "convert_from(canonical_bytes,'UTF8')::jsonb = request_envelope",
            name="ck_invocations_canonical_integrity",
        ),
        sa.CheckConstraint("sequence >= 0 AND attempt >= 1", name="ck_invocations_sequence"),
        sa.CheckConstraint("stage IN ('prefilter','main')", name="ck_invocations_stage"),
        sa.CheckConstraint(
            "call_kind IN ('chat','systemone_relevance','systemone_detail',"
            "'systemone_combined','onnx')",
            name="ck_invocations_call_kind",
        ),
        sa.CheckConstraint(
            "inference_job_id IS NULL OR benchmark_run_id IS NULL", name="ck_invocations_job_kind"
        ),
        sa.CheckConstraint(
            "(outcome IN ('prepared','outcome_unknown') AND completed_at IS NULL "
            "AND response_capture IS NULL AND reconciliation IS NULL) OR "
            "(outcome IN ('succeeded','failed') AND completed_at IS NOT NULL "
            "AND completed_at >= prepared_at AND response_capture IS NOT NULL)",
            name="ck_invocations_outcome",
        ),
    )
    for suffix, columns in (
        ("source", ["source_snapshot_id"]),
        ("search", ["project_id", "search_id"]),
        ("job", ["inference_job_id"]),
        ("run", ["benchmark_run_id"]),
        ("parent", ["parent_id"]),
    ):
        op.create_index(f"ix_invocations_{suffix}", "invocations", columns)
    for table in _LINK_TABLES:
        _add_links(table)
    _guards()


def _guards():
    op.execute("""
        CREATE FUNCTION seip_source_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'source snapshots are immutable'; END $$;
    """)
    op.execute("""
        CREATE TRIGGER source_snapshots_immutable BEFORE UPDATE ON source_snapshots
        FOR EACH ROW EXECUTE FUNCTION seip_source_immutable();
    """)
    frozen_new = ",".join(f"NEW.{name}" for name in _FROZEN)
    frozen_old = ",".join(f"OLD.{name}" for name in _FROZEN)
    op.execute(f"""
        CREATE FUNCTION seip_invocation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_search uuid;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.group_id::text, 0));
            IF NEW.outcome <> 'prepared' THEN
              RAISE EXCEPTION 'invocation must start prepared';
            END IF;
            IF NEW.inference_job_id IS NOT NULL THEN
              SELECT j.search_id INTO parent_search FROM inference_jobs j
              WHERE j.id = NEW.inference_job_id FOR SHARE;
              IF NOT FOUND OR parent_search IS DISTINCT FROM NEW.search_id THEN
                RAISE EXCEPTION 'invocation job search mismatch';
              END IF;
            END IF;
            IF NEW.benchmark_run_id IS NOT NULL THEN
              SELECT r.search_id INTO parent_search FROM benchmark_runs r
              WHERE r.id = NEW.benchmark_run_id FOR SHARE;
              IF NOT FOUND OR parent_search IS DISTINCT FROM NEW.search_id THEN
                RAISE EXCEPTION 'invocation benchmark search mismatch';
              END IF;
            END IF;
            IF EXISTS (SELECT 1 FROM invocations i WHERE i.group_id = NEW.group_id AND
              ROW(i.project_id,i.article_id,i.source_snapshot_id,i.search_id,
                  i.inference_job_id,i.benchmark_run_id) IS DISTINCT FROM
              ROW(NEW.project_id,NEW.article_id,NEW.source_snapshot_id,NEW.search_id,
                  NEW.inference_job_id,NEW.benchmark_run_id)) THEN
                RAISE EXCEPTION 'invocation group scope mismatch';
            END IF;
          ELSE
            IF ROW({frozen_new}) IS DISTINCT FROM ROW({frozen_old})
              OR OLD.outcome NOT IN ('prepared','outcome_unknown')
              OR (OLD.outcome = 'prepared' AND
                  (NEW.outcome NOT IN ('succeeded','failed','outcome_unknown')
                   OR NEW.reconciliation IS NOT NULL))
              OR (OLD.outcome = 'outcome_unknown' AND
                  (NEW.outcome NOT IN ('succeeded','failed') OR NEW.reconciliation IS NULL
                   OR coalesce(NEW.response_capture->>'origin','')
                      NOT IN ('driver_raw','driver_parsed')
                   OR coalesce(NEW.reconciliation->>'actor_ref','') = ''
                   OR coalesce(NEW.reconciliation->>'reason','') = ''
                   OR coalesce(NEW.reconciliation->>'recorded_at','') = '')) THEN
                RAISE EXCEPTION 'invalid invocation mutation';
            END IF;
          END IF;
          IF NEW.outcome IN ('succeeded','failed') THEN
            IF jsonb_typeof(NEW.response_capture) IS DISTINCT FROM 'object'
              OR coalesce(NEW.response_capture->>'origin','') NOT IN
                 ('driver_raw','driver_parsed','unavailable')
              OR coalesce(NEW.response_capture->>'raw_availability','') NOT IN
                 ('retained','not_captured','unavailable')
              OR (NEW.response_capture->>'origin' <> 'driver_raw' AND
                  (NEW.response_capture->>'raw_availability' = 'retained'
                   OR NEW.response_capture->>'raw_base64' IS NOT NULL
                   OR NEW.response_capture->>'raw_sha256' IS NOT NULL))
              OR (NEW.response_capture->>'origin' = 'driver_parsed' AND
                  coalesce(NEW.response_capture->'parsed','null'::jsonb) = 'null'::jsonb)
              OR (NEW.response_capture->>'origin' = 'driver_raw' AND
                  (coalesce(NEW.response_capture->>'raw_availability','') <> 'retained'
                   OR NEW.response_capture->>'raw_base64' IS NULL
                   OR NEW.response_capture->>'raw_sha256' IS NULL))
              OR (NEW.response_capture->>'origin' = 'unavailable' AND
                  (coalesce(NEW.response_capture->>'reason','') = ''
                   OR coalesce(NEW.response_capture->'parsed','null'::jsonb) <> 'null'::jsonb
                   OR coalesce(NEW.response_capture->'raw_base64','null'::jsonb) <> 'null'::jsonb))
              OR (NEW.response_capture->>'raw_availability' = 'unavailable' AND
                  coalesce(NEW.response_capture->>'reason','') = '') THEN
              RAISE EXCEPTION 'invalid actual-response capture';
            END IF;
            IF NEW.response_capture->>'origin' = 'driver_raw' AND
               encode(sha256(decode(NEW.response_capture->>'raw_base64','base64')),'hex')
                 IS DISTINCT FROM NEW.response_capture->>'raw_sha256' THEN
              RAISE EXCEPTION 'actual response digest mismatch';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER invocations_guard BEFORE INSERT OR UPDATE ON invocations
        FOR EACH ROW EXECUTE FUNCTION seip_invocation_guard();
    """)
    op.execute("""
        CREATE FUNCTION seip_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF TG_TABLE_NAME = 'human_annotations' THEN
              IF ROW(NEW.provenance_project_id,NEW.source_snapshot_id,NEW.source_view_id,
                     NEW.source_view_sha256) IS DISTINCT FROM
                 ROW(OLD.provenance_project_id,OLD.source_snapshot_id,OLD.source_view_id,
                     OLD.source_view_sha256) THEN
                RAISE EXCEPTION 'provenance cannot be retargeted or backfilled';
              END IF;
            ELSE
              IF ROW(NEW.provenance_project_id,NEW.source_snapshot_id,NEW.invocation_id,
                     NEW.invocation_group_id) IS DISTINCT FROM
                 ROW(OLD.provenance_project_id,OLD.source_snapshot_id,OLD.invocation_id,
                     OLD.invocation_group_id) THEN
                RAISE EXCEPTION 'provenance cannot be retargeted or backfilled';
              END IF;
            END IF;
            IF OLD.source_snapshot_id IS NOT NULL AND
               ROW(NEW.article_id,NEW.search_id) IS DISTINCT FROM ROW(OLD.article_id,OLD.search_id)
            THEN RAISE EXCEPTION 'bound source scope is immutable'; END IF;
            IF TG_TABLE_NAME = 'benchmark_predictions' AND OLD.source_snapshot_id IS NOT NULL THEN
              IF NEW.benchmark_run_id IS DISTINCT FROM OLD.benchmark_run_id THEN
                RAISE EXCEPTION 'bound benchmark scope is immutable';
              END IF;
            END IF;
          END IF;
          IF TG_TABLE_NAME = 'human_annotations' THEN
            IF NEW.source_snapshot_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM source_snapshots s, jsonb_array_elements(s.payload->'views') v
              WHERE s.id = NEW.source_snapshot_id AND v->>'id' = NEW.source_view_id::text
                AND v->>'sha256' = NEW.source_view_sha256 AND v->>'availability' = 'retained'
            ) THEN RAISE EXCEPTION 'review view is unavailable or mismatched'; END IF;
          ELSIF NEW.invocation_id IS NOT NULL THEN
            IF NOT EXISTS (SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id
              AND i.search_id IS NOT DISTINCT FROM NEW.search_id) THEN
                RAISE EXCEPTION 'producing invocation search mismatch';
            END IF;
            IF TG_TABLE_NAME = 'benchmark_predictions' THEN
              IF NOT EXISTS (SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id
                AND i.benchmark_run_id = NEW.benchmark_run_id) THEN
                  RAISE EXCEPTION 'producing invocation benchmark mismatch';
              END IF;
            ELSIF EXISTS (SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id
                          AND i.benchmark_run_id IS NOT NULL) THEN
              RAISE EXCEPTION 'benchmark invocation cannot produce a normal prediction';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
    """)
    for table in _LINK_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_binding BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION seip_binding_guard()"
        )
    op.execute("""
        CREATE FUNCTION seip_parent_scope_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(NEW.project_id,NEW.search_id)
             IS DISTINCT FROM ROW(OLD.project_id,OLD.search_id) THEN
            IF TG_TABLE_NAME = 'inference_jobs' THEN
              IF EXISTS (SELECT 1 FROM invocations i WHERE i.inference_job_id = OLD.id) THEN
                RAISE EXCEPTION 'captured invocation parent scope is immutable';
              END IF;
            ELSIF EXISTS (SELECT 1 FROM invocations i WHERE i.benchmark_run_id = OLD.id) THEN
              RAISE EXCEPTION 'captured invocation parent scope is immutable';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
    """)
    for table in ("inference_jobs", "benchmark_runs"):
        op.execute(
            f"CREATE TRIGGER {table}_provenance_scope BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION seip_parent_scope_guard()"
        )


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM source_snapshots)")).scalar():
        raise RuntimeError(
            "refusing to discard retained provenance; export and explicitly delete first"
        )
    for table in ("inference_jobs", "benchmark_runs"):
        op.execute(f"DROP TRIGGER {table}_provenance_scope ON {table}")
    op.execute("DROP FUNCTION seip_parent_scope_guard()")
    for table in _LINK_TABLES:
        op.execute(f"DROP TRIGGER {table}_binding ON {table}")
    op.execute("DROP FUNCTION seip_binding_guard()")
    op.execute("DROP TRIGGER invocations_guard ON invocations")
    op.execute("DROP FUNCTION seip_invocation_guard()")
    op.execute("DROP TRIGGER source_snapshots_immutable ON source_snapshots")
    op.execute("DROP FUNCTION seip_source_immutable()")
    for table in reversed(_LINK_TABLES):
        for suffix in ("provenance_article", "source", "provenance_search"):
            op.drop_constraint(f"fk_{table}_{suffix}", table, type_="foreignkey")
        op.drop_constraint(f"ck_{table}_source_scope", table, type_="check")
        op.drop_index(f"ix_{table}_source", table_name=table)
        if table == "human_annotations":
            op.drop_constraint("ck_human_annotations_source_view", table, type_="check")
            columns = ["source_view_id", "source_view_sha256"]
        else:
            op.drop_constraint(f"fk_{table}_invocation", table, type_="foreignkey")
            op.drop_constraint(f"ck_{table}_invocation_scope", table, type_="check")
            op.drop_index(f"ix_{table}_invocation", table_name=table)
            columns = ["invocation_id", "invocation_group_id"]
        if table == "benchmark_predictions":
            op.drop_constraint("fk_benchmark_predictions_provenance_run", table, type_="foreignkey")
            columns += ["search_id"]
        for column in columns + ["source_snapshot_id", "provenance_project_id"]:
            op.drop_column(table, column)
    op.drop_table("invocations")
    op.drop_table("source_snapshots")
    for table in reversed(_SCOPE_TABLES):
        op.drop_constraint(f"uq_{table}_id_project", table, type_="unique")
