"""model_registry : backend DETERMINISTIC (programme en process) + extracteur NTS enregistré

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-19 16:00:00.000000

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "'OLLAMA','LLAMA_CPP','TRANSFORMERS','ONNX_LOCAL','SYSTEMONE','OPENAI_API'"
_NTS_NAME = "nts-deterministic-extractor-v1"
_NTS_IDENTIFIER = "diggypaper.nts.extract/1.0.0"


def upgrade() -> None:
    op.execute("ALTER TABLE model_registry DROP CONSTRAINT IF EXISTS backendtype")
    op.execute(
        "ALTER TABLE model_registry ADD CONSTRAINT backendtype CHECK "
        f"(backend_type IN ({_OLD},'DETERMINISTIC'))"
    )
    # Platform model (owner NULL): visible to every project, needs no model service.
    op.get_bind().execute(
        sa.text(
            "INSERT INTO model_registry (id, name, backend_type, model_identifier, ram_usage_mb, "
            "is_active) SELECT CAST(:id AS uuid), CAST(:name AS varchar), 'DETERMINISTIC', "
            "CAST(:ident AS varchar), 0, true "
            "WHERE NOT EXISTS (SELECT 1 FROM model_registry WHERE name = CAST(:name2 AS varchar))"
        ),
        {"id": str(uuid.uuid4()), "name": _NTS_NAME, "ident": _NTS_IDENTIFIER, "name2": _NTS_NAME},
    )


def downgrade() -> None:
    op.execute("DELETE FROM model_registry WHERE backend_type = 'DETERMINISTIC'")
    op.execute("ALTER TABLE model_registry DROP CONSTRAINT IF EXISTS backendtype")
    op.execute(
        f"ALTER TABLE model_registry ADD CONSTRAINT backendtype CHECK (backend_type IN ({_OLD}))"
    )
