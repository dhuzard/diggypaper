"""Fixed vocabularies of ORAtlas connector grants and their audit log (Diggypaper #53).

Constants only, so the ORM CHECK constraints and the service share one definition. Migration
0034 holds frozen copies: changing a value here is a new migration, never a silent edit.
"""

from __future__ import annotations

from datetime import timedelta

# The four scopes of #53. A grant names each one explicitly; nothing is implied by another.
GRANT_SCOPES = (
    "list_project",
    "list_artifacts",
    "read_selected_metadata",
    "obtain_authorized_export",
)
# Project metadata a lead may disclose under a grant; needs read_selected_metadata.
DISCLOSABLE_FIELDS = ("title", "description")
REVOCATION_REASONS = (
    "lead-revoked",
    "holder-revoked",
    "membership-removed",
    "project-deleted",
    "account-deleted",
    "account-anonymized",
)
AUDIT_EVENTS = ("grant-created", "grant-revoked", "access-granted", "access-refused")
# Internal refusal reasons. A caller always sees the same refusal, whatever the reason.
REFUSAL_REASONS = (
    "unknown-credential",
    "revoked",
    "expired",
    "project-mismatch",
    "scope-not-granted",
    "holder-not-member",
    "holder-inactive",
)
MAX_GRANT_TTL = timedelta(days=90)
CREDENTIAL_PREFIX = "dpg_"
