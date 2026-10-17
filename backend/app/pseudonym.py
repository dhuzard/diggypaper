"""The single pseudonymous reviewer identity policy.

Two layers, one policy:

* ``anonymized_actor_ref(email)`` is the durable, salted ``sha256(salt + email)``
  digest **persisted** with retained records (annotations' ``annotator_hash``,
  support assessments' ``actor_ref``, gold revisions' ``review_actor_ref`` /
  ``recorded_by_ref``) and copied onto the account at deletion. It is the
  account-deletion anchor only: it is never exported in a crate nor returned by
  the API, because it would link the same person across unrelated projects.
* ``reviewer_reference(project_id, digest)`` derives the **project-scoped**
  pseudonym from that stored digest. Every exported or API-exposed reviewer
  reference (annotation ``reviewer_ref``, assessment ``actor_ref``, gold
  ``review_actor_ref`` / ``recorded_by_ref``) uses it, so one person has one
  reference across annotations, assessments and gold within a project, two
  projects yield unrelated references, and the reference survives account
  deletion because the stored digest does.
"""

import hashlib
import json

from app.core.config import get_settings


def anonymized_actor_ref(email: str) -> str:
    return hashlib.sha256((get_settings().anon_salt + email).encode("utf-8")).hexdigest()


def reviewer_reference(project_id, annotator_hash: str | None) -> str | None:
    """Project-scoped pseudonym from the salted digest retained on account deletion."""
    if not annotator_hash:
        return None
    payload = json.dumps(
        ["diggypaper-reviewer", str(project_id), annotator_hash],
        sort_keys=True,
        ensure_ascii=False,
    )
    return "reviewer-" + hashlib.sha256(payload.encode()).hexdigest()
