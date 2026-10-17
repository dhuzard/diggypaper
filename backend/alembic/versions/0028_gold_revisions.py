"""Retain immutable gold revisions across refresh.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gold_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "gold_id",
            UUID(as_uuid=True),
            sa.ForeignKey("gold_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("ground_truth_json", JSONB(), nullable=False),
        sa.Column("answer_keys_json", JSONB(), nullable=False),
        sa.Column("answer_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("source_annotation_ref", UUID(as_uuid=True)),
        sa.Column("source_snapshot_ref", UUID(as_uuid=True)),
        sa.Column("source_view_ref", UUID(as_uuid=True)),
        sa.Column("source_view_sha256", sa.String(64)),
        sa.Column("review_actor_ref", sa.String(64)),
        sa.Column("review_status", sa.String(30)),
        sa.Column("review_blind", sa.Boolean()),
        sa.Column("review_created_at", sa.DateTime(timezone=True)),
        sa.Column("assessment_refs_json", JSONB(), nullable=False),
        sa.Column("recorded_by_ref", sa.String(64)),
        sa.Column("provenance_state", sa.String(25), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("gold_id", "sequence", name="uq_gold_revisions_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_gold_revisions_sequence"),
        sa.CheckConstraint(
            "provenance_state IN ('unqualified','legacy_unqualified')",
            name="ck_gold_revisions_provenance",
        ),
    )
    op.execute("""
        CREATE FUNCTION seip_gold_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'gold revisions are immutable';
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER gold_revisions_immutable BEFORE UPDATE ON gold_revisions
        FOR EACH ROW EXECUTE FUNCTION seip_gold_revision_immutable();
    """)
    op.execute("""
        CREATE FUNCTION seip_gold_revision_delete_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM gold_articles WHERE id = OLD.gold_id) THEN
            RAISE EXCEPTION 'gold revisions require owner deletion';
          END IF;
          RETURN OLD;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER gold_revisions_delete_guard BEFORE DELETE ON gold_revisions
        FOR EACH ROW EXECUTE FUNCTION seip_gold_revision_delete_guard();
    """)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM gold_revisions)")).scalar():
        raise RuntimeError("refusing to discard gold revision history")
    op.execute("DROP TRIGGER gold_revisions_delete_guard ON gold_revisions")
    op.execute("DROP FUNCTION seip_gold_revision_delete_guard()")
    op.execute("DROP TRIGGER gold_revisions_immutable ON gold_revisions")
    op.execute("DROP FUNCTION seip_gold_revision_immutable()")
    op.drop_table("gold_revisions")
