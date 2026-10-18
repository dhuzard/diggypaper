"""Optional NTS research module: publication -> ALURES Non-Technical Summary traceability.

Bounded and offline-first. It reuses SourceSnapshotV1, the article quote matcher, the rights
allowlist and the source-evidence-packet/0.1.0 producer; it adds deterministic identifier and
animal-count extraction, an exact-ID ALURES join and a companion artifact. It never makes a
scientific-support, reviewer or "verified" claim on its own.
"""

COMPANION_SCHEMA = "nts-traceability-companion/0.1.0"
