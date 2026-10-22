"""Pure builder of a research-project-descriptor/0.1.0 for one Diggypaper project.

No database, clock, environment or randomness: the same input yields identical bytes. A
descriptor is a privacy-minimal, read-only statement that the holder is an active member of
one project, plus immutable references (type, version, digest) to that project's exported
artifacts. It grants nothing (``access_effect: none``): an ORAtlas connector still has to
prove a live, LEAD-controlled grant and fetch each artifact through an authorized export.

Fail-closed defaults (Diggypaper #53): the title and description are project metadata and are
``withheld`` unless an explicit disclosure decision is passed, and a disclosure needs a grant
reference. The project is always ``private`` and every processing and publication policy is
``not-authorized`` / ``not-released`` in 0.1.0. A global ADMIN who is not a member gets no
descriptor: a platform role is not project membership. No member list, email, user id, count,
full text, evidence or prompt appears in a descriptor.

It is not exposed through any route yet. Grants are app.connector_grants; the connector
endpoints that issue a descriptor through a grant are a later #53 slice.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.models import ProjectMemberRole

SCHEMA_VERSION = "research-project-descriptor/0.1.0"
PACKET_SCHEMA_VERSION = "source-evidence-packet/0.1.0"
NA = "not-available"
MAX_TTL = timedelta(days=7)
DISCLOSABLE = ("title", "description")
POLICY = {
    "cross_project_processing": "not-authorized",
    "external_model_processing": "not-authorized",
    "full_text": "not-included",
    "publication": "not-released",
}
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_RECIPIENT = re.compile(r"^recipient:[a-z0-9][a-z0-9-]{0,62}$")
_GRANT = re.compile(r"^grant:[0-9a-f]{32}$")
_DECISION = re.compile(r"^decision:[0-9a-f]{32}$")
# Same leak rule as the contract validator (README section 3).
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_BARE_DIGEST = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def canonical_bytes(doc: Mapping[str, Any]) -> bytes:
    return (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("descriptor timestamps must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _project_link(project_id: uuid.UUID, *parts: str) -> str:
    return "/".join(("", "projects", str(project_id), *parts))


def packet_artifact(project_id: uuid.UUID, packet_files: Mapping[str, bytes]) -> dict[str, Any]:
    """Reference to one source-evidence-packet/0.1.0, bound to its exact manifest bytes."""
    manifest_bytes = packet_files["manifest.json"]
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("schema_version") != PACKET_SCHEMA_VERSION:
        raise ValueError("a descriptor references source-evidence-packet/0.1.0 only")
    return {
        "access_level": manifest["access_scope"]["level"],
        "deep_link": _project_link(project_id, "findings"),
        "digest": "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        "packet_id": manifest["packet_id"],
        "retrieval": "authorized-export-required",
        "schema_version": PACKET_SCHEMA_VERSION,
        "type": "source-evidence-packet",
    }


def build_project_descriptor(
    *,
    project_id: uuid.UUID,
    holder_role: ProjectMemberRole | str | None,
    issued_at: datetime,
    expires_at: datetime,
    recipient_ref: str,
    producer_commit: str,
    artifacts: Iterable[Mapping[str, Any]] = (),
    grant_ref: str | None = None,
    disclosed: Mapping[str, tuple[str, str]] | None = None,
    creation_method: str = "producer-export",
) -> bytes:
    """Canonical descriptor bytes; ``disclosed`` maps a field to ``(value, decision_ref)``."""
    if holder_role is None:
        raise ValueError("only an active project member receives a descriptor")
    role = ProjectMemberRole(str(holder_role).upper())
    if not (_HEX40.match(producer_commit) or producer_commit == "synthetic-fixture"):
        raise ValueError("a descriptor needs the 40-hex producer commit")
    if not _RECIPIENT.match(recipient_ref):
        raise ValueError(f"invalid recipient_ref {recipient_ref!r}")
    if grant_ref is not None and not _GRANT.match(grant_ref):
        raise ValueError(f"invalid grant_ref {grant_ref!r}")
    issued, expires = _utc(issued_at), _utc(expires_at)
    if not issued < expires <= issued + MAX_TTL:
        raise ValueError("expires_at must be after issued_at and at most 7 days later")

    metadata: dict[str, dict[str, str]] = {name: {"state": "withheld"} for name in DISCLOSABLE}
    for name, (value, decision_ref) in (disclosed or {}).items():
        if name not in DISCLOSABLE:
            raise ValueError(f"{name!r} is not disclosable metadata")
        if grant_ref is None:
            # Title and description are private metadata: never disclosed without a grant.
            raise ValueError("disclosing project metadata needs a grant_ref")
        if not _DECISION.match(decision_ref):
            raise ValueError(f"invalid decision_ref {decision_ref!r}")
        if not value.strip():
            raise ValueError(f"disclosed {name} is empty")
        if _EMAIL.search(value) or _BARE_DIGEST.search(value):
            raise ValueError(f"disclosed {name} contains an email or a bare digest")
        metadata[name] = {"decision_ref": decision_ref, "state": "disclosed", "value": value}

    refs = [dict(a) for a in artifacts]
    own_prefix = _project_link(project_id)
    digests = [a["digest"] for a in refs]
    if len(set(digests)) != len(digests):
        raise ValueError("duplicate artifact digest")
    for a in refs:
        if a["deep_link"] != own_prefix and not a["deep_link"].startswith(own_prefix + "/"):
            raise ValueError("an artifact deep link must stay inside its project")
    refs.sort(key=lambda a: (a["type"], a["digest"]))

    doc = {
        "access_effect": "none",
        "artifacts": refs,
        "audience": {"grant_ref": grant_ref or NA, "recipient_ref": recipient_ref},
        "creation_method": creation_method,
        "expires_at": _ts(expires),
        "holder": {"membership": "active", "role": role.value.lower()},
        "issued_at": _ts(issued),
        "policy": dict(POLICY),
        "producer": {"commit": producer_commit, "name": "diggypaper"},
        "project": {
            "deep_link": own_prefix,
            "description": metadata["description"],
            "project_id": str(project_id),
            "title": metadata["title"],
            "visibility": "private",
        },
        "schema_version": SCHEMA_VERSION,
        "scope": "private-descriptor",
    }
    return canonical_bytes(doc)
