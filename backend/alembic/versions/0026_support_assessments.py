"""Append-only human support assessments scoped to exact evidence links.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evidence_support_assessments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "prediction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("article_predictions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("article_id", UUID(as_uuid=True), nullable=False),
        sa.Column("graph_revision_ref", UUID(as_uuid=True), nullable=False),
        sa.Column("answer_ref", UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_ref", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "actor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("actor_ref", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("protocol_version", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('supports','contradicts','insufficient','unclear','not_assessed')",
            name="ck_support_assessments_status",
        ),
        sa.CheckConstraint(
            "length(rationale) <= 5000 AND "
            "(status = 'not_assessed' OR length(trim(rationale)) > 0)",
            name="ck_support_assessments_rationale",
        ),
        sa.CheckConstraint(
            "protocol_version = 'seip-support-v1'",
            name="ck_support_assessments_protocol",
        ),
    )
    op.create_index(
        "ix_support_assessments_prediction_created",
        "evidence_support_assessments",
        ["prediction_id", "created_at"],
    )
    op.execute("""
        CREATE FUNCTION seip_support_assessment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE graph jsonb;
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            RAISE EXCEPTION 'support assessments are append-only';
          END IF;
          SELECT p.evidence_graph_json INTO graph
            FROM article_predictions p WHERE p.id = NEW.prediction_id
            AND p.article_id = NEW.article_id AND p.provenance_project_id = NEW.project_id
            FOR SHARE;
          IF graph IS NULL OR
             coalesce(graph->'envelope'->>'state','') <> 'structured' OR
             coalesce(graph->'envelope'->'scope'->>'revision_ref','')
               <> NEW.graph_revision_ref::text OR
             NEW.actor_ref <> NEW.actor_id::text OR
             NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(graph->'envelope'->'links') AS link
               WHERE link->>'answer_ref' = NEW.answer_ref::text
                 AND link->>'evidence_ref' = NEW.evidence_ref::text
             ) THEN
            RAISE EXCEPTION 'support assessment requires an exact retained answer/evidence link';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER evidence_support_assessments_guard
        BEFORE INSERT OR UPDATE OR DELETE ON evidence_support_assessments FOR EACH ROW
        EXECUTE FUNCTION seip_support_assessment_guard();
    """)


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM evidence_support_assessments)"))
        .scalar()
    ):
        raise RuntimeError("refusing to discard human support assessments")
    op.execute("DROP TRIGGER evidence_support_assessments_guard ON evidence_support_assessments")
    op.execute("DROP FUNCTION seip_support_assessment_guard()")
    op.drop_index(
        "ix_support_assessments_prediction_created", table_name="evidence_support_assessments"
    )
    op.drop_table("evidence_support_assessments")
