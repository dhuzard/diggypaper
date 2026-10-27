"""Keep a benchmark decision and variant tied to its producing call.

Revision ID: f0026
Revises: f0025
"""

import sqlalchemy as sa

from alembic import op

revision = "f0026"
down_revision = "f0025"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    mismatch = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM benchmark_predictions p JOIN invocations i "
            "ON i.id = p.invocation_id WHERE p.invocation_id IS NOT NULL "
            "AND p.variant IS DISTINCT FROM i.variant)"
        )
    ).scalar()
    if mismatch:
        raise RuntimeError("bound benchmark variant differs from producing invocation")
    duplicate = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM benchmark_predictions "
            "WHERE invocation_id IS NOT NULL GROUP BY invocation_id HAVING count(*) > 1)"
        )
    ).scalar()
    if duplicate:
        raise RuntimeError("one benchmark invocation has multiple result rows")
    op.create_unique_constraint(
        "uq_benchmark_predictions_invocation", "benchmark_predictions", ["invocation_id"]
    )
    op.execute("""
        CREATE FUNCTION seip_benchmark_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.invocation_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id
              AND i.variant IS NOT DISTINCT FROM NEW.variant
          ) THEN
            RAISE EXCEPTION 'benchmark variant differs from producing call';
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.invocation_id IS NOT NULL AND
             ROW(NEW.id,NEW.model_name,NEW.variant,NEW.decision_json,
                 NEW.confidence_score,NEW.error,NEW.seconds) IS DISTINCT FROM
             ROW(OLD.id,OLD.model_name,OLD.variant,OLD.decision_json,
                 OLD.confidence_score,OLD.error,OLD.seconds) THEN
            RAISE EXCEPTION 'bound benchmark result is immutable';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER benchmark_predictions_result_integrity "
        "BEFORE INSERT OR UPDATE ON benchmark_predictions "
        "FOR EACH ROW EXECUTE FUNCTION seip_benchmark_result_guard()"
    )


def downgrade():
    op.execute("DROP TRIGGER benchmark_predictions_result_integrity ON benchmark_predictions")
    op.execute("DROP FUNCTION seip_benchmark_result_guard()")
    op.drop_constraint(
        "uq_benchmark_predictions_invocation", "benchmark_predictions", type_="unique"
    )
