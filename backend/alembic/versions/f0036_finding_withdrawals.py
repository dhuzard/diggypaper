"""Append-only finding withdrawals (retire a revision superseded by another finding).

Revision ID: f0036
Revises: f0035
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "f0036"
down_revision = "f0035"
branch_labels = None
depends_on = None

# Frozen copy of app.db.models.FINDING_WITHDRAWAL_REASONS: a new reason is a new migration.
_REASONS = "'superseded-extraction', 'duplicate'"


def upgrade():
    # Target of the (revision_id, finding_id) foreign key; id is already unique.
    op.create_unique_constraint(
        "uq_finding_revisions_scope", "finding_revisions", ["id", "finding_id"]
    )
    op.create_table(
        "finding_withdrawals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("article_id", UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("superseded_by_finding_id", UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "withdrawn_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("withdrawn_by_ref", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("revision_id", name="uq_finding_withdrawals_revision"),
        sa.ForeignKeyConstraint(
            ["finding_id", "project_id", "article_id"],
            ["findings.id", "findings.project_id", "findings.article_id"],
            ondelete="CASCADE",
            name="fk_finding_withdrawals_finding_scope",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_finding_id", "project_id", "article_id"],
            ["findings.id", "findings.project_id", "findings.article_id"],
            ondelete="CASCADE",
            name="fk_finding_withdrawals_successor_scope",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "finding_id"],
            ["finding_revisions.id", "finding_revisions.finding_id"],
            ondelete="CASCADE",
            name="fk_finding_withdrawals_revision_scope",
        ),
        sa.CheckConstraint(f"reason IN ({_REASONS})", name="ck_finding_withdrawals_reason"),
        sa.CheckConstraint(
            "superseded_by_finding_id <> finding_id", name="ck_finding_withdrawals_not_self"
        ),
        sa.CheckConstraint("length(trim(rationale)) > 0", name="ck_finding_withdrawals_rationale"),
        sa.CheckConstraint(
            "length(withdrawn_by_ref) = 64", name="ck_finding_withdrawals_withdrawn_by_ref"
        ),
    )
    op.create_index(
        "ix_finding_withdrawals_finding", "finding_withdrawals", ["project_id", "finding_id"]
    )
    # Append-only; only nulling the actor FK (account deletion) may update a row.
    op.execute("""
        CREATE FUNCTION finding_withdrawals_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (OLD.withdrawn_by_id IS NOT NULL AND NEW.withdrawn_by_id IS NULL AND
              (to_jsonb(NEW) - 'withdrawn_by_id') = (to_jsonb(OLD) - 'withdrawn_by_id'))
          THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'finding_withdrawals are append-only';
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER finding_withdrawals_append_only BEFORE UPDATE ON finding_withdrawals "
        "FOR EACH ROW EXECUTE FUNCTION finding_withdrawals_guard()"
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM finding_withdrawals)")).scalar():
        raise RuntimeError("refusing to discard finding withdrawals")
    op.execute("DROP TRIGGER finding_withdrawals_append_only ON finding_withdrawals")
    op.execute("DROP FUNCTION finding_withdrawals_guard()")
    op.drop_index("ix_finding_withdrawals_finding", table_name="finding_withdrawals")
    op.drop_table("finding_withdrawals")
    op.drop_constraint("uq_finding_revisions_scope", "finding_revisions", type_="unique")
