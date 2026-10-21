"""NTS publication traceability tables: ALURES dataset receipts, publication-NTS links,
typed animal-count observations and append-only link adjudications (issue #11).

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-19 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CASCADE = {"ondelete": "CASCADE"}
_SET_NULL = {"ondelete": "SET NULL"}


def _uuid(name: str, *args, **kw) -> sa.Column:
    return sa.Column(name, UUID(as_uuid=True), *args, **kw)


def _created(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "nts_datasets",
        _uuid("id", primary_key=True),
        _uuid("project_id", sa.ForeignKey("projects.id", **_CASCADE), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("landing_url", sa.String(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.String(), nullable=False),
        _created(),
        sa.UniqueConstraint("project_id", "sha256", name="uq_nts_datasets_project_sha"),
    )
    op.create_index("ix_nts_datasets_project", "nts_datasets", ["project_id"])

    op.create_table(
        "nts_links",
        _uuid("id", primary_key=True),
        _uuid("project_id", nullable=False),
        _uuid("article_id", nullable=False),
        _uuid("prediction_id", sa.ForeignKey("article_predictions.id", **_CASCADE), nullable=False),
        _uuid("finding_id", sa.ForeignKey("findings.id", **_SET_NULL), nullable=True),
        sa.Column("quote_index", sa.Integer(), nullable=False),
        sa.Column("identifier_system", sa.String(64), nullable=False),
        sa.Column("identifier_verbatim", sa.String(200), nullable=False),
        sa.Column("identifier_normalized", sa.String(64), nullable=True),
        sa.Column("country", sa.String(8), nullable=True),
        sa.Column("version_cited", sa.String(32), nullable=True),
        sa.Column("date_cited", sa.String(64), nullable=True),
        sa.Column("alures_state", sa.String(48), nullable=False),
        sa.Column("alures_reasons", JSONB(none_as_null=True), nullable=False),
        sa.Column("alures_versions", JSONB(none_as_null=True), nullable=True),
        sa.Column("alures_current_version", sa.String(32), nullable=True),
        sa.Column("alures_row_numbers", JSONB(none_as_null=True), nullable=True),
        sa.Column("alures_candidates", JSONB(none_as_null=True), nullable=True),
        _uuid("dataset_id", sa.ForeignKey("nts_datasets.id", **_SET_NULL), nullable=True),
        sa.Column("link_state", sa.String(48), nullable=False),
        sa.Column("planned_json", JSONB(none_as_null=True), nullable=True),
        sa.Column("scope_note", sa.Text(), nullable=True),
        _created("computed_at"),
        sa.UniqueConstraint(
            "project_id",
            "prediction_id",
            "identifier_system",
            "identifier_verbatim",
            "quote_index",
            name="uq_nts_links_identity",
        ),
        sa.ForeignKeyConstraint(
            ["article_id", "project_id"],
            ["articles.id", "articles.project_id"],
            ondelete="CASCADE",
            name="fk_nts_links_article_scope",
        ),
    )
    op.create_index("ix_nts_links_project_article", "nts_links", ["project_id", "article_id"])

    op.create_table(
        "nts_count_observations",
        _uuid("id", primary_key=True),
        _uuid("project_id", nullable=False),
        _uuid("article_id", nullable=False),
        _uuid("prediction_id", sa.ForeignKey("article_predictions.id", **_CASCADE), nullable=False),
        _uuid("finding_id", sa.ForeignKey("findings.id", **_SET_NULL), nullable=True),
        sa.Column("observation_id", sa.String(120), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("count_category", sa.String(40), nullable=False),
        sa.Column("species", sa.String(120), nullable=False),
        sa.Column("strain", sa.String(120), nullable=False),
        sa.Column("sex", sa.String(32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("unit_counted", sa.String(64), nullable=False),
        sa.Column("cohort_label", sa.String(200), nullable=False),
        sa.Column("count_scope", sa.String(64), nullable=False),
        sa.Column("group_count", sa.Integer(), nullable=True),
        sa.Column("derived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("derivation", sa.Text(), nullable=True),
        sa.Column("method_section", sa.String(200), nullable=True),
        sa.Column("rule_id", sa.String(64), nullable=True),
        sa.Column("possible_overlap", JSONB(none_as_null=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("dataset_cell", JSONB(none_as_null=True), nullable=True),
        _created("computed_at"),
        sa.UniqueConstraint(
            "project_id", "prediction_id", "observation_id", name="uq_nts_count_obs_identity"
        ),
        sa.ForeignKeyConstraint(
            ["article_id", "project_id"],
            ["articles.id", "articles.project_id"],
            ondelete="CASCADE",
            name="fk_nts_count_obs_article_scope",
        ),
    )
    op.create_index(
        "ix_nts_count_obs_project_article", "nts_count_observations", ["project_id", "article_id"]
    )

    op.create_table(
        "nts_adjudications",
        _uuid("id", primary_key=True),
        _uuid("link_id", sa.ForeignKey("nts_links.id", **_CASCADE), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        _uuid("adjudicator_id", sa.ForeignKey("users.id", **_SET_NULL), nullable=True),
        sa.Column("adjudicator_ref", sa.String(64), nullable=False),
        _created(),
        sa.CheckConstraint(
            "decision IN ('confirmed','rejected','unresolved')",
            name="ck_nts_adjudications_decision",
        ),
        sa.CheckConstraint(
            "length(adjudicator_ref) = 64", name="ck_nts_adjudications_adjudicator_ref"
        ),
    )
    op.create_index("ix_nts_adjudications_link", "nts_adjudications", ["link_id"])
    # Append-only like finding reviews: an adjudication is corrected by a newer one.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION nts_adjudications_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'nts_adjudications is append-only';
            END IF;
            IF NEW.adjudicator_id IS NULL AND OLD.adjudicator_id IS NOT NULL
               AND NEW.link_id = OLD.link_id AND NEW.decision = OLD.decision
               AND NEW.rationale = OLD.rationale AND NEW.adjudicator_ref = OLD.adjudicator_ref
               AND NEW.created_at = OLD.created_at THEN
                RETURN NEW;  -- account deletion (ON DELETE SET NULL) only
            END IF;
            RAISE EXCEPTION 'nts_adjudications is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_nts_adjudications_append_only BEFORE UPDATE OR DELETE "
        "ON nts_adjudications FOR EACH ROW EXECUTE FUNCTION nts_adjudications_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_nts_adjudications_append_only ON nts_adjudications")
    op.execute("DROP FUNCTION IF EXISTS nts_adjudications_append_only()")
    op.drop_table("nts_adjudications")
    op.drop_table("nts_count_observations")
    op.drop_table("nts_links")
    op.drop_table("nts_datasets")
