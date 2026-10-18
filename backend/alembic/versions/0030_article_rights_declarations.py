"""Append-only article redistribution rights declarations for packet exports.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

# Frozen copies of app.rights at the time of this migration: a later change to the allowlist
# is a new migration, never a silent edit of this one.
_DECISIONS = "'allowed', 'metadata-only', 'restricted'"
_LICENSES = "'CC0-1.0', 'CC-BY-4.0', 'CC-BY-SA-4.0', 'CC-BY-3.0', 'CC-BY-SA-3.0'"


def upgrade():
    op.create_table(
        "article_rights_declarations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("article_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("license_id", sa.String(64), nullable=False),
        sa.Column("redistribution", sa.String(16), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("evidence_url", sa.Text(), nullable=True),
        sa.Column(
            "declared_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("declared_by_ref", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("article_id", "revision", name="uq_article_rights_revision"),
        sa.ForeignKeyConstraint(
            ["article_id", "project_id"],
            ["articles.id", "articles.project_id"],
            ondelete="CASCADE",
            name="fk_article_rights_article_scope",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_article_rights_revision"),
        sa.CheckConstraint(
            f"redistribution IN ({_DECISIONS})", name="ck_article_rights_redistribution"
        ),
        sa.CheckConstraint("length(trim(basis)) > 0", name="ck_article_rights_basis"),
        sa.CheckConstraint(
            f"redistribution <> 'allowed' OR (license_id IN ({_LICENSES}) "
            "AND evidence_url IS NOT NULL AND evidence_url LIKE 'https://%')",
            name="ck_article_rights_allowed",
        ),
        sa.CheckConstraint("length(declared_by_ref) = 64", name="ck_article_rights_declared_by"),
    )
    op.create_index(
        "ix_article_rights_article",
        "article_rights_declarations",
        ["project_id", "article_id", "revision"],
    )
    # Append-only; only nulling the actor FK (account deletion) may update a row.
    op.execute("""
        CREATE FUNCTION article_rights_declarations_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (OLD.declared_by_id IS NOT NULL AND NEW.declared_by_id IS NULL AND
              (to_jsonb(NEW) - 'declared_by_id') = (to_jsonb(OLD) - 'declared_by_id'))
          THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'article_rights_declarations are append-only';
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER article_rights_declarations_append_only BEFORE UPDATE ON "
        "article_rights_declarations FOR EACH ROW EXECUTE FUNCTION "
        "article_rights_declarations_guard()"
    )


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM article_rights_declarations)")).scalar():
        raise RuntimeError("refusing to discard article rights declarations")
    op.execute(
        "DROP TRIGGER article_rights_declarations_append_only ON article_rights_declarations"
    )
    op.execute("DROP FUNCTION article_rights_declarations_guard()")
    op.drop_index("ix_article_rights_article", table_name="article_rights_declarations")
    op.drop_table("article_rights_declarations")
