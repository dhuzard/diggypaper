"""The NTS extractor as a deterministic inference backend.

Registered in ``model_registry`` with ``backend_type = DETERMINISTIC`` (migrations 0031 and 0033
seed ``nts-deterministic-extractor-v1`` and ``-v2``), it runs inside the ordinary inference job: the
platform captures the source snapshot, prepares the invocation from the rendered messages, and
checks every quote against the retained input exactly as it does for a chat model. The only
difference is that no HTTP request leaves the process: the "response" is this program's JSON,
recorded verbatim with its sha256 in the invocation.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.inference import InferenceError
from app.nts.extract import Statements, propose
from app.nts.fields import decision_from_extraction, is_nts_project
from app.nts.source import freeze_snapshot
from app.provenance import SourceSnapshotV1
from app.schema import project_fields

# Every released version stays runnable with its own output, so a recorded run can be redone.
# v1 states each finding as its quoted passage; v2 words it in one short machine sentence.
EXTRACTORS: dict[str, tuple[str, Statements]] = {
    "nts-deterministic-extractor-v1": ("diggypaper.nts.extract/1.0.0", "verbatim"),
    "nts-deterministic-extractor-v2": ("diggypaper.nts.extract/2.0.0", "short"),
}
MODEL_NAMES = tuple(EXTRACTORS)
MODEL_NAME = "nts-deterministic-extractor-v2"  # the version new runs use
MODEL_IDENTIFIER = EXTRACTORS[MODEL_NAME][0]
BACKEND = "DETERMINISTIC"


def is_deterministic(model) -> bool:
    if model is None:
        return False
    backend = getattr(model.backend_type, "value", model.backend_type)
    return str(backend) == BACKEND


def extract_decision(
    snapshot: SourceSnapshotV1, view_id: uuid.UUID, statements: Statements = "verbatim"
) -> dict[str, Any]:
    """Pure extraction over a platform snapshot; raises when the view is not retained."""
    frozen = freeze_snapshot(snapshot, view_id)
    extraction = propose(frozen, str(snapshot.article_id), statements)
    return decision_from_extraction(extraction)


async def run_traced(
    model, project, snapshot: SourceSnapshotV1, view_id: uuid.UUID, rendered, trace
):
    """Produce the decision under the same provenance discipline as a chat call."""
    if not is_nts_project(project_fields(project)):
        raise InferenceError("deterministic_extractor_requires_nts_fields")
    version = EXTRACTORS.get(model.name)
    if version is None or version[0] != model.model_identifier:
        raise InferenceError("deterministic_extractor_unknown_version")
    await trace.before_dispatch({"model": model.model_identifier, "messages": rendered.messages})
    try:
        decision = extract_decision(snapshot, view_id, version[1])
        raw = json.dumps(decision, sort_keys=True, ensure_ascii=False).encode("utf-8")
    except Exception as exc:  # recorded as a failed call, never as a silent empty prediction
        await trace.on_http_response(json.dumps({"error": type(exc).__name__}).encode(), 500)
        raise InferenceError(f"deterministic_extractor_failed:{type(exc).__name__}") from exc
    await trace.on_http_response(raw, 200)
    return decision
