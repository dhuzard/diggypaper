"""Require a completed producing call for every invocation-bound prediction.

Revision ID: 0024
Revises: 0023

An invocation reference must never make a prepared, failed, or uncertain call
look like the producer of a decision, even when no quote checks are present.
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in ("article_predictions", "benchmark_predictions"):
        bad = bind.execute(
            sa.text(
                f"SELECT EXISTS (SELECT 1 FROM {table} p JOIN invocations i "
                "ON i.id = p.invocation_id WHERE p.invocation_id IS NOT NULL "
                "AND i.outcome <> 'succeeded')"
            )
        ).scalar()
        if bad:
            raise RuntimeError(f"{table} contains a non-succeeded producing invocation")
    op.execute("""
        CREATE FUNCTION seip_prediction_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.invocation_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id
              AND i.outcome = 'succeeded'
          ) THEN
            RAISE EXCEPTION 'prediction requires a succeeded producing invocation';
          END IF;
          RETURN NEW;
        END $$;
    """)
    for table in ("article_predictions", "benchmark_predictions"):
        op.execute(
            f"CREATE TRIGGER {table}_result_guard BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION seip_prediction_result_guard()"
        )


def downgrade():
    for table in ("benchmark_predictions", "article_predictions"):
        op.execute(f"DROP TRIGGER {table}_result_guard ON {table}")
    op.execute("DROP FUNCTION seip_prediction_result_guard()")
