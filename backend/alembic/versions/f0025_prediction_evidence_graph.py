"""Retain frozen answer/evidence envelopes for new normal chat predictions.

Revision ID: f0025
Revises: f0024
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "f0025"
down_revision = "f0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("article_predictions", sa.Column("evidence_graph_json", JSONB()))
    op.create_check_constraint(
        "ck_article_predictions_evidence_graph_scope",
        "article_predictions",
        "evidence_graph_json IS NULL OR ("
        "quote_checks_json IS NOT NULL AND "
        "coalesce(evidence_graph_json->'envelope'->>'schema_version','') = "
        "'evidence-envelope-v1' AND "
        "coalesce(evidence_graph_json->'envelope'->'scope'->>'record_kind','') = "
        "'prediction' AND "
        "coalesce(evidence_graph_json->'envelope'->'scope'->>'record_ref','') = id::text "
        "AND coalesce(evidence_graph_json->'envelope'->'scope'->>'revision_ref','') "
        "= id::text AND "
        "coalesce(evidence_graph_json->'envelope'->'scope'->>'project_ref','') "
        "= provenance_project_id::text AND "
        "coalesce(evidence_graph_json->'envelope'->'scope'->>'article_ref','') "
        "= article_id::text)",
    )
    op.execute("""
        CREATE FUNCTION seip_evidence_graph_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND (
            NEW.evidence_graph_json IS DISTINCT FROM OLD.evidence_graph_json OR
            (OLD.evidence_graph_json IS NOT NULL AND (
              NEW.decision_json IS DISTINCT FROM OLD.decision_json OR
              NEW.evidence_quotes IS DISTINCT FROM OLD.evidence_quotes OR
              NEW.quote_checks_json IS DISTINCT FROM OLD.quote_checks_json
            ))
          ) THEN
            RAISE EXCEPTION 'prediction evidence graph and source output are immutable';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER article_predictions_evidence_graph_immutable
        BEFORE INSERT OR UPDATE ON article_predictions FOR EACH ROW
        EXECUTE FUNCTION seip_evidence_graph_immutable();
    """)


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM article_predictions "
                "WHERE evidence_graph_json IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("refusing to discard persisted answer/evidence graphs")
    op.execute("DROP TRIGGER article_predictions_evidence_graph_immutable ON article_predictions")
    op.execute("DROP FUNCTION seip_evidence_graph_immutable()")
    op.drop_constraint(
        "ck_article_predictions_evidence_graph_scope", "article_predictions", type_="check"
    )
    op.drop_column("article_predictions", "evidence_graph_json")
