"""Persist bounded quotation-occurrence checks for normal predictions.

Revision ID: 0023
Revises: 0022

Historical rows remain NULL: current article text cannot validate old calls.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("article_predictions", sa.Column("quote_checks_json", JSONB()))
    op.create_check_constraint(
        "ck_article_predictions_quote_checks_scope",
        "article_predictions",
        "quote_checks_json IS NULL OR ("
        "source_snapshot_id IS NOT NULL AND invocation_id IS NOT NULL AND "
        "jsonb_typeof(quote_checks_json) = 'object' AND "
        "coalesce(quote_checks_json->>'schema_version','') = 'quote-checks-v1' AND "
        "coalesce(quote_checks_json->>'input_ref','') = invocation_id::text AND "
        "coalesce(quote_checks_json->>'source_snapshot_ref','') = source_snapshot_id::text AND "
        "jsonb_typeof(quote_checks_json->'items') = 'array')",
    )
    op.execute("""
        CREATE FUNCTION seip_quote_checks_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.quote_checks_json IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM invocations i WHERE i.id = NEW.invocation_id
                AND i.outcome = 'succeeded'
            ) THEN RAISE EXCEPTION 'quotation checks require a completed producing call'; END IF;
            RETURN NEW;
          END IF;
          IF NEW.quote_checks_json IS DISTINCT FROM OLD.quote_checks_json OR
             (OLD.quote_checks_json IS NOT NULL AND
              (NEW.decision_json IS DISTINCT FROM OLD.decision_json OR
               NEW.evidence_quotes IS DISTINCT FROM OLD.evidence_quotes)) THEN
            RAISE EXCEPTION 'quotation checks and checked output are immutable';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER article_predictions_quote_checks_immutable
        BEFORE INSERT OR UPDATE ON article_predictions FOR EACH ROW
        EXECUTE FUNCTION seip_quote_checks_immutable();
    """)


def downgrade():
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM article_predictions WHERE quote_checks_json IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError("refusing to discard persisted quotation checks")
    op.execute("DROP TRIGGER article_predictions_quote_checks_immutable ON article_predictions")
    op.execute("DROP FUNCTION seip_quote_checks_immutable()")
    op.drop_constraint(
        "ck_article_predictions_quote_checks_scope", "article_predictions", type_="check"
    )
    op.drop_column("article_predictions", "quote_checks_json")
