"""ORAtlas connector grants and their append-only audit log (issue #53).

Additive only: two new tables, no change to existing rows, routes or exports. A grant stores
the SHA-256 of its bearer credential, never the credential. After creation a grant changes only
by one-way revocation (or the holder FK being nulled when the account is deleted). Audit events
have no FK so they outlive their grant and project, and are never updated or deleted.

Revision ID: 0034
Revises: 0033
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

# Frozen copies of app.connector_policy at the time of this migration: a later change is a new
# migration, never a silent edit of this one.
_SCOPES = "'list_project', 'list_artifacts', 'read_selected_metadata', 'obtain_authorized_export'"
_REVOCATIONS = (
    "'lead-revoked', 'holder-revoked', 'membership-removed', 'project-deleted', "
    "'account-deleted', 'account-anonymized'"
)
_REFUSALS = (
    "'unknown-credential', 'revoked', 'expired', 'project-mismatch', 'scope-not-granted', "
    "'holder-not-member', 'holder-inactive'"
)
_EVENTS = "'grant-created', 'grant-revoked', 'access-granted', 'access-refused'"


def upgrade():
    op.create_table(
        "connector_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "holder_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by_ref", sa.String(64), nullable=False),
        sa.Column("recipient_ref", sa.String(64), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("scope_list_project", sa.Boolean(), nullable=False),
        sa.Column("scope_list_artifacts", sa.Boolean(), nullable=False),
        sa.Column("scope_read_selected_metadata", sa.Boolean(), nullable=False),
        sa.Column("scope_obtain_authorized_export", sa.Boolean(), nullable=False),
        sa.Column("disclose_title", sa.Boolean(), nullable=False),
        sa.Column("disclose_description", sa.Boolean(), nullable=False),
        sa.Column("credential_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_ref", sa.String(64), nullable=True),
        sa.Column("revocation_reason", sa.String(32), nullable=True),
        sa.UniqueConstraint("credential_sha256", name="uq_connector_grants_credential"),
        sa.CheckConstraint("length(credential_sha256) = 64", name="ck_connector_grants_credential"),
        sa.CheckConstraint("length(created_by_ref) = 64", name="ck_connector_grants_created_by"),
        sa.CheckConstraint(
            "recipient_ref LIKE 'recipient:%'", name="ck_connector_grants_recipient"
        ),
        sa.CheckConstraint("length(trim(purpose)) > 0", name="ck_connector_grants_purpose"),
        sa.CheckConstraint(
            "scope_list_project OR scope_list_artifacts OR scope_read_selected_metadata "
            "OR scope_obtain_authorized_export",
            name="ck_connector_grants_scope",
        ),
        sa.CheckConstraint(
            "scope_read_selected_metadata OR NOT (disclose_title OR disclose_description)",
            name="ck_connector_grants_disclosure",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_connector_grants_expiry"),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_ref IS NULL AND revocation_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revocation_reason IS NOT NULL AND "
            f"revocation_reason IN ({_REVOCATIONS}))",
            name="ck_connector_grants_revocation",
        ),
    )
    op.create_index("ix_connector_grants_project", "connector_grants", ["project_id", "created_at"])
    op.create_table(
        "connector_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("grant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(40), nullable=True),
        sa.Column("reason", sa.String(40), nullable=True),
        sa.Column("actor_ref", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"event IN ({_EVENTS})", name="ck_connector_audit_event"),
        sa.CheckConstraint(
            f"scope IS NULL OR scope IN ({_SCOPES})", name="ck_connector_audit_scope"
        ),
        sa.CheckConstraint(
            f"reason IS NULL OR reason IN ({_REFUSALS}, {_REVOCATIONS})",
            name="ck_connector_audit_reason",
        ),
        sa.CheckConstraint(
            "actor_ref IS NULL OR length(actor_ref) = 64", name="ck_connector_audit_actor"
        ),
    )
    op.create_index(
        "ix_connector_audit_grant", "connector_audit_events", ["grant_id", "created_at"]
    )
    # A grant changes only by one-way revocation, or by nulling the holder FK.
    op.execute("""
        CREATE FUNCTION connector_grants_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          mutable CONSTANT text[] := ARRAY['holder_id','revoked_at','revoked_by_ref',
                                           'revocation_reason'];
        BEGIN
          IF (to_jsonb(NEW) - mutable) = (to_jsonb(OLD) - mutable)
             AND (NEW.holder_id IS NOT DISTINCT FROM OLD.holder_id OR NEW.holder_id IS NULL)
             AND (OLD.revoked_at IS NULL OR
                  (NEW.revoked_at, NEW.revoked_by_ref, NEW.revocation_reason)
                  IS NOT DISTINCT FROM
                  (OLD.revoked_at, OLD.revoked_by_ref, OLD.revocation_reason))
          THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'connector_grants change only by revocation';
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER connector_grants_guard BEFORE UPDATE ON connector_grants "
        "FOR EACH ROW EXECUTE FUNCTION connector_grants_guard()"
    )
    op.execute("""
        CREATE FUNCTION connector_audit_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'connector_audit_events are append-only';
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER connector_audit_events_append_only BEFORE UPDATE OR DELETE ON "
        "connector_audit_events FOR EACH ROW EXECUTE FUNCTION connector_audit_events_guard()"
    )


def downgrade():
    bind = op.get_bind()
    for table in ("connector_grants", "connector_audit_events"):
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError(f"refusing to discard {table}")
    op.execute("DROP TRIGGER connector_audit_events_append_only ON connector_audit_events")
    op.execute("DROP FUNCTION connector_audit_events_guard()")
    op.execute("DROP TRIGGER connector_grants_guard ON connector_grants")
    op.execute("DROP FUNCTION connector_grants_guard()")
    op.drop_index("ix_connector_audit_grant", table_name="connector_audit_events")
    op.drop_table("connector_audit_events")
    op.drop_index("ix_connector_grants_project", table_name="connector_grants")
    op.drop_table("connector_grants")
