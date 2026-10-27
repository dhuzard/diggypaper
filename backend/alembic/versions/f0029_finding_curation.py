"""Versioned source-paper findings with evidence links and independent reviews.

Revision ID: f0029
Revises: f0028
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "f0029"
down_revision = "f0028"
branch_labels = None
depends_on = None

_CASCADE = {"ondelete": "CASCADE"}


def upgrade():
    op.create_table(
        "findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("article_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "project_id", "article_id", name="uq_findings_scope"),
        sa.ForeignKeyConstraint(
            ["article_id", "project_id"],
            ["articles.id", "articles.project_id"],
            ondelete="CASCADE",
            name="fk_findings_article_scope",
        ),
    )
    op.create_index("ix_findings_article", "findings", ["project_id", "article_id"])
    op.create_table(
        "finding_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "finding_id",
            UUID(as_uuid=True),
            sa.ForeignKey("findings.id", **_CASCADE),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_json", JSONB(none_as_null=True), nullable=False),
        sa.Column(
            "proposer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("proposer_ref", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("finding_id", "revision", name="uq_finding_revisions_number"),
        sa.CheckConstraint("revision >= 1", name="ck_finding_revisions_number"),
        sa.CheckConstraint("length(proposer_ref) = 64", name="ck_finding_revisions_proposer_ref"),
    )
    op.create_index("ix_finding_revisions_finding", "finding_revisions", ["finding_id", "revision"])
    op.create_table(
        "finding_evidence",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "revision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("finding_revisions.id", **_CASCADE),
            nullable=False,
        ),
        sa.Column(
            "prediction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("article_predictions.id", **_CASCADE),
            nullable=False,
        ),
        sa.Column("answer_ref", UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_ref", UUID(as_uuid=True), nullable=False),
        sa.UniqueConstraint(
            "revision_id", "prediction_id", "evidence_ref", name="uq_finding_evidence_link"
        ),
    )
    op.create_index("ix_finding_evidence_revision", "finding_evidence", ["revision_id"])
    op.create_table(
        "finding_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "revision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("finding_revisions.id", **_CASCADE),
            nullable=False,
        ),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("judgments_json", JSONB(none_as_null=True), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("protocol_version", sa.String(40), nullable=False),
        sa.Column(
            "reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewer_ref", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("revision_id", "reviewer_ref", name="uq_finding_reviews_reviewer"),
        sa.CheckConstraint("decision IN ('approve','reject')", name="ck_finding_reviews_decision"),
        sa.CheckConstraint("length(reviewer_ref) = 64", name="ck_finding_reviews_reviewer_ref"),
        sa.CheckConstraint(
            "protocol_version = 'seip-support-v1'", name="ck_finding_reviews_protocol"
        ),
    )
    for table, actor in (
        ("finding_revisions", "proposer_id"),
        ("finding_reviews", "reviewer_id"),
        ("finding_evidence", None),
    ):
        # Append-only; only nulling the actor FK (account deletion) may update a row.
        allowed = (
            f"(OLD.{actor} IS NOT NULL AND NEW.{actor} IS NULL AND "
            f"(to_jsonb(NEW) - '{actor}') = (to_jsonb(OLD) - '{actor}'))"
            if actor
            else "false"
        )
        op.execute(f"""
            CREATE FUNCTION {table}_guard() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF {allowed} THEN RETURN NEW; END IF;
              RAISE EXCEPTION '{table} are append-only';
            END $$;
        """)
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_guard()"
        )


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM findings)")).scalar():
        raise RuntimeError("refusing to discard curated findings")
    for table in ("finding_reviews", "finding_evidence", "finding_revisions"):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
        op.execute(f"DROP FUNCTION {table}_guard()")
    op.drop_table("finding_reviews")
    op.drop_index("ix_finding_evidence_revision", table_name="finding_evidence")
    op.drop_table("finding_evidence")
    op.drop_index("ix_finding_revisions_finding", table_name="finding_revisions")
    op.drop_table("finding_revisions")
    op.drop_index("ix_findings_article", table_name="findings")
    op.drop_table("findings")
