"""Signed acknowledgement of the exact source view returned by the review GET."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings
from app.provenance import SourceSnapshotV1, review_binding

PURPOSE = "seip-review-source-v1"
TOKEN_LIFETIME = timedelta(hours=12)


def issue_review_token(
    snapshot: SourceSnapshotV1, view_id: uuid.UUID, search_id: uuid.UUID, user_id: uuid.UUID
) -> str:
    binding = review_binding(snapshot, view_id)
    now = datetime.now(UTC)
    claims = {
        "purpose": PURPOSE,
        "project": str(snapshot.project_id),
        "article": str(snapshot.article_id),
        "search": str(search_id),
        "user": str(user_id),
        "snapshot": str(binding["source_snapshot_id"]),
        "view": str(binding["source_view_id"]),
        "sha256": binding["source_view_sha256"],
        "iat": now,
        "exp": now + TOKEN_LIFETIME,
    }
    return jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")


def parse_review_token(
    token: str, *, project_id: uuid.UUID, article_id: uuid.UUID,
    search_id: uuid.UUID, user_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    if len(token) > 4096:
        raise ValueError("invalid source view token")
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        expected = {
            "purpose": PURPOSE,
            "project": str(project_id),
            "article": str(article_id),
            "search": str(search_id),
            "user": str(user_id),
        }
        if any(claims.get(key) != value for key, value in expected.items()):
            raise ValueError("source view token scope mismatch")
        sha256 = claims["sha256"]
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise ValueError("invalid source view digest")
        return uuid.UUID(claims["snapshot"]), uuid.UUID(claims["view"]), sha256
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid source view token") from exc
