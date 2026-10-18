"""Redistribution rights for source-evidence packet exports.

Quotations leave Diggypaper only on an explicit, attributable rights declaration for the
article: a LEAD records the licence, the redistribution decision, the basis for it and a
public URL where the licence is stated. Declarations are append-only revisions; the latest
one applies. Without a declaration, or with any decision other than ``allowed`` under a
licence in :data:`REDISTRIBUTABLE_LICENSES`, quotations stay withheld (hash only).

The allowlist is deliberately small: licences that permit redistribution and adaptation
for any purpose. Non-commercial or no-derivatives licences are excluded. Extending it is a
code and migration change (the database CHECK constraint repeats this list), never a
per-project setting.
"""

from __future__ import annotations

from typing import Any

REDISTRIBUTABLE_LICENSES: tuple[str, ...] = (
    "CC0-1.0",
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "CC-BY-3.0",
    "CC-BY-SA-3.0",
)
REDISTRIBUTION_DECISIONS: tuple[str, ...] = ("allowed", "metadata-only", "restricted")
# Licences a LEAD may choose for packet metadata in a public export.
PACKET_METADATA_LICENSES: tuple[str, ...] = ("CC0-1.0", "CC-BY-4.0")
ACCESS_LEVELS: tuple[str, ...] = ("restricted", "public")


def quotes_redistributable(rights: dict[str, Any] | None) -> bool:
    """True only for an ``allowed`` declaration under an allowlisted licence."""
    return bool(
        rights
        and rights.get("redistribution") == "allowed"
        and rights.get("license_id") in REDISTRIBUTABLE_LICENSES
    )


def sql_in(values: tuple[str, ...]) -> str:
    """SQL literal list for CHECK constraints (values are fixed constants, never user input)."""
    return ", ".join(f"'{v}'" for v in values)
