"""Frozen article source for the NTS workflow: SourceSnapshotV1 + quote-match receipts.

A *source file* (``sources/<id>.json``) holds contiguous verbatim spans of an open-access
article together with how they were obtained. This module freezes them into the real
``SourceSnapshotV1`` and checks every quotation with the real ``ArticleQuoteMatcher``. Nothing
here judges whether a passage supports a claim.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.provenance import (
    Components,
    RawArtifact,
    Section,
    SourceDescription,
    SourceSnapshotV1,
    canonical_json,
)
from app.provenance_render import composed_view
from app.quote_matcher import ArticleInputSpan, ArticleQuoteMatcher, TargetArticleInput

NAMESPACE = uuid.UUID("6f1c9a34-7e0d-5b4a-9c2e-4d5a0f6b7c11")
SECTION_KINDS = ("methods", "results")


def stable_uuid(*parts: str) -> uuid.UUID:
    """Deterministic id so a re-import of the same source is idempotent."""
    return uuid.uuid5(NAMESPACE, "|".join(parts))


@dataclass(frozen=True)
class FrozenSource:
    source: dict[str, Any]
    snapshot: SourceSnapshotV1
    view_id: uuid.UUID
    matcher: ArticleQuoteMatcher

    @property
    def view_text(self) -> str:
        view = next(v for v in self.snapshot.views if v.id == self.view_id)
        assert view.text is not None
        return view.text

    @property
    def snapshot_sha256(self) -> str:
        return self.snapshot.sha256

    def section_offset(self, index: int) -> int:
        """Start of ``sections[index].text`` inside the composed view."""
        view = next(v for v in self.snapshot.views if v.id == self.view_id)
        for span in view.origins:
            if span.component_path == ("sections", index, "text"):
                return span.start
        raise KeyError(index)


def load_source(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("source_id", "identifiers", "title", "license", "retrieval", "sections"):
        if key not in data:
            raise ValueError(f"source file {path} lacks {key!r}")
    kinds = {s["kind"] for s in data["sections"]}
    if not kinds <= set(SECTION_KINDS):
        raise ValueError(f"unsupported section kinds {sorted(kinds - set(SECTION_KINDS))}")
    return data


def freeze(source: dict[str, Any], project_id: uuid.UUID) -> FrozenSource:
    """Build the immutable snapshot (deterministic ids and timestamp) and its matcher."""
    ids = source["identifiers"]
    article_id = stable_uuid("article", source["source_id"])
    snapshot_id = stable_uuid(
        "snapshot", source["source_id"], canonical_json(source["sections"]).decode()
    )
    components = Components(
        title=source["title"],
        abstract=None,
        methods_text=None,
        sections=tuple(Section(**s) for s in source["sections"]),
    )
    view = composed_view(components, list(SECTION_KINDS))
    view = view.model_copy(update={"id": stable_uuid("view", str(snapshot_id))})
    url = source["retrieval"].get("public_url")
    description = SourceDescription(
        type="pmc-excerpt",
        public_url=url,
        url_state="retained" if url else "not_captured",
        extraction_method=source["retrieval"]["method"],
        extraction_version="nts-excerpt-v1",
        software_build="unknown",
    )
    snapshot = SourceSnapshotV1(
        id=snapshot_id,
        project_id=project_id,
        article_id=article_id,
        captured_at=datetime.fromisoformat(
            source["retrieval"]["retrieved_at"].replace("Z", "+00:00")
        ),
        pmid=ids.get("pmid"),
        pmcid=ids.get("pmcid"),
        doi=ids.get("doi"),
        source=description,
        components=components,
        raw_artifact=RawArtifact(availability="not_captured"),
        views=(view,),
    )
    spans = []
    for origin in view.origins:
        if origin.origin != "component" or origin.component_path is None:
            continue
        text = view.text[origin.start : origin.end]  # type: ignore[index]
        spans.append(
            ArticleInputSpan(
                text=text,
                payload_path='["article_text"]',
                input_start=origin.start,
                snapshot_ref=str(snapshot.id),
                view_ref=str(view.id),
                view_start=origin.start,
                component_ref=json.dumps(list(origin.component_path), separators=(",", ":")),
                component_start=origin.source_start or 0,
            )
        )
    target = TargetArticleInput(
        input_ref=str(stable_uuid("input", str(snapshot.id))), spans=tuple(spans)
    )
    return FrozenSource(
        source=source,
        snapshot=snapshot,
        view_id=view.id,
        matcher=ArticleQuoteMatcher(target, allow_normalized=True),
    )


def match_receipt(frozen: FrozenSource, quote: str) -> dict[str, Any]:
    """Quote-match receipt in the shape Finding Curation reads from stored quote checks."""
    result = frozen.matcher.match(quote)
    view_ref = start = end = None
    if result.status in ("exact", "normalized") and len(result.candidates) == 1:
        c = result.candidates[0]
        view_ref, start, end = c.view_ref, c.view_start, c.view_end
    ref = str(stable_uuid("receipt", frozen.matcher.source.input_ref, quote))  # type: ignore[union-attr]
    return {
        "ref": ref,
        "status": result.status,
        "view_ref": view_ref,
        "start": start,
        "end": end,
        "reason": result.reason,
        "distinct_source_locations": result.distinct_source_location_count,
    }
