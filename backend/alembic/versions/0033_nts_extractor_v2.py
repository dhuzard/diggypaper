"""model_registry : extracteur NTS v2 (énoncés courts), à côté de la v1 inchangée

v2 words each finding in one short machine sentence (origin ``ai-paraphrase``) instead of
stating the quoted passage; quotes, windows and typed values are those of v1. The v1 row is
kept as is: its recorded predictions, invocations and proposer stay attributable to it.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-22 15:00:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "nts-deterministic-extractor-v2"
_IDENTIFIER = "diggypaper.nts.extract/2.0.0"


def upgrade() -> None:
    # Platform model (owner NULL), like v1 in 0031; idempotent.
    op.get_bind().execute(
        sa.text(
            "INSERT INTO model_registry (id, name, backend_type, model_identifier, ram_usage_mb, "
            "is_active) SELECT CAST(:id AS uuid), CAST(:name AS varchar), 'DETERMINISTIC', "
            "CAST(:ident AS varchar), 0, true "
            "WHERE NOT EXISTS (SELECT 1 FROM model_registry WHERE name = CAST(:name2 AS varchar))"
        ),
        {"id": str(uuid.uuid4()), "name": _NAME, "ident": _IDENTIFIER, "name2": _NAME},
    )


def downgrade() -> None:
    # Predictions keep their model_used string; only the registry entry goes.
    op.get_bind().execute(
        sa.text("DELETE FROM model_registry WHERE name = CAST(:name AS varchar)"), {"name": _NAME}
    )
