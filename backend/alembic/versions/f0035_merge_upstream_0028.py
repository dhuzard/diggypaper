"""Jonction des deux chaînes de migrations : fourche (f0022 → f0034) et amont (0022 → 0028)

Revision ID: f0035
Revises: f0034, 0028
Create Date: 2026-10-27 12:00:00.000000

Les migrations de la fourche (provenance SEIP, constats, NTS, connecteur) portent l'identifiant
``f00XX`` ; celles de l'amont BENETNATH/diggypaper gardent ``00XX``. Les deux chaînes partent de
``0021`` et sont indépendantes ; cette révision vide les réunit en une seule tête.

Base déjà migrée par la fourche avant cette jonction (``alembic_version`` entre ``0022`` et
``0034`` **et** table ``source_snapshots`` présente) : ``alembic stamp --purge f00XX`` avec l'ancien
numéro, puis ``alembic upgrade head``. Base venue de l'amont : ``alembic upgrade head`` suffit.
"""

from collections.abc import Sequence

revision: str = "f0035"
down_revision: str | Sequence[str] | None = ("f0034", "0028")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
