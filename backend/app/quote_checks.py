"""Persist occurrence evidence only for a captured final producing chat call."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict

from app.provenance import InvocationV1
from app.quote_matcher import ArticleInputSpan, ArticleQuoteMatcher, TargetArticleInput

SCHEMA_VERSION = "quote-checks-v1"
MAX_QUOTES = 100


def _text_at(request: dict, path: tuple) -> str:
    value = request
    for part in path:
        value = value[part]
    if not isinstance(value, str):
        raise ValueError("target input path does not resolve to text")
    return value


def target_article_input(invocation: InvocationV1) -> TargetArticleInput:
    """Use only validated target origins; examples/templates cannot be evidence."""
    if invocation.call_kind != "chat":
        raise ValueError("chat quotation checks require an actual chat call")
    spans = []
    for origin in invocation.input_spans:
        if origin.origin != "target":
            continue
        if any(
            item is None
            for item in (
                origin.view_id,
                origin.view_start,
                origin.component_path,
                origin.source_start,
            )
        ):
            raise ValueError("target input lacks validated source offsets")
        text = _text_at(invocation.request, origin.payload_path)[origin.start : origin.end]
        spans.append(
            ArticleInputSpan(
                text=text,
                payload_path=json.dumps(list(origin.payload_path), separators=(",", ":")),
                input_start=origin.start,
                snapshot_ref=str(invocation.source_snapshot_id),
                view_ref=str(origin.view_id),
                view_start=origin.view_start,
                component_ref=json.dumps(list(origin.component_path), separators=(",", ":")),
                component_start=origin.source_start,
            )
        )
    return TargetArticleInput(input_ref=str(invocation.id), spans=tuple(spans))


def check_prediction_quotes(invocation: InvocationV1, decision: dict) -> dict:
    """Return a JSON-ready, bounded record; never assert answer support."""
    if not isinstance(decision, dict):
        raise ValueError("prediction output must be an object")
    base = {
        "schema_version": SCHEMA_VERSION,
        "source_snapshot_ref": str(invocation.source_snapshot_id),
        "input_ref": str(invocation.id),
        "producer": "chat",
        "items": [],
        "list_complete": True,
        "overflow_count": 0,
    }
    if "evidence_quotes" not in decision:
        return {**base, "state": "missing_quote_field"}
    quotes = decision["evidence_quotes"]
    if not isinstance(quotes, list):
        return {**base, "state": "invalid_quote_list"}
    if not quotes:
        return {**base, "state": "empty_quote_list"}
    source = target_article_input(invocation)
    matcher = ArticleQuoteMatcher(source, allow_normalized=True)
    checked = quotes[:MAX_QUOTES]
    items = [
        {
            "match_ref": str(uuid.uuid5(invocation.id, f"quote:{index}")),
            "quote_index": index,
            "result": asdict(matcher.match(quote)),
        }
        for index, quote in enumerate(checked)
    ]
    return {
        **base,
        "state": "incomplete" if len(quotes) > MAX_QUOTES else "checked",
        "items": items,
        "list_complete": len(quotes) <= MAX_QUOTES,
        "overflow_count": max(0, len(quotes) - MAX_QUOTES),
    }
