"""The existing durable, salted reviewer pseudonym used for retained records."""

import hashlib

from app.core.config import get_settings


def anonymized_actor_ref(email: str) -> str:
    return hashlib.sha256((get_settings().anon_salt + email).encode("utf-8")).hexdigest()
