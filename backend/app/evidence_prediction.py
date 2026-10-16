"""Bind a normal chat prediction to frozen answers and its captured quote checks.

Global quotation lists carry no implied field assignment. Only an explicit
field/quote-index mapping can create an answer-to-evidence edge.
"""

from __future__ import annotations

import uuid

from app.evidence_links import (
    EnvelopeScope,
    ExpectedAnswer,
    FieldSpec,
    FrozenFieldDefinition,
    QuoteMatchReceipt,
    SourceBindingReceipt,
    ValidationContext,
    build_envelope,
    legacy_envelope,
)
from app.schema import DEFAULT_QUESTION, project_fields, validate_decision


def prediction_envelope(
    *,
    prediction_id: uuid.UUID,
    project_id: uuid.UUID,
    article_id: uuid.UUID,
    project,
    decision: dict,
    invocation_id: uuid.UUID,
    source_snapshot_id: uuid.UUID,
    quote_checks: dict,
) -> dict | None:
    """Return a verified envelope, or None for invalid/unsupported raw output.

    None never repairs a model result; the original decision remains in its own
    column. A caller must expose that absence rather than synthesize links later.
    """
    scope = EnvelopeScope(
        project_ref=str(project_id),
        article_ref=str(article_id),
        record_kind="prediction",
        record_ref=str(prediction_id),
        revision_ref=str(prediction_id),
    )
    quotes = decision.get("evidence_quotes")
    if not isinstance(quotes, list) or len(quotes) > 512 or any(type(q) is not str for q in quotes):
        return None
    try:
        fields = project_fields(project)
        definition = FrozenFieldDefinition(
            question=(project.question or DEFAULT_QUESTION).strip(),
            fields=tuple(FieldSpec.model_validate(field) for field in fields),
        )
        if validate_decision(fields, decision):
            return {
                "envelope": legacy_envelope(scope, quotes).model_dump(mode="json"),
                "field_definition": None,
            }
        answers = tuple(
            ExpectedAnswer(field_key=field.key, value=decision[field.key])
            for field in definition.fields
        )
        binding_ref = str(invocation_id)
        check_items = {item["quote_index"]: item for item in quote_checks.get("items", [])}
        if len(check_items) != len(quote_checks.get("items", [])) or any(
            type(index) is not int
            or index < 0
            or index >= len(quotes)
            or item["result"]["quote"] != quotes[index]
            or item["result"]["input_ref"] != str(invocation_id)
            for index, item in check_items.items()
        ):
            return None
        matches = tuple(
            QuoteMatchReceipt(
                match_ref=item["match_ref"],
                project_ref=str(project_id),
                article_ref=str(article_id),
                snapshot_ref=str(source_snapshot_id),
                input_ref=str(invocation_id),
                quote=quotes[index],
                status=item["result"]["status"],
            )
            for index, item in check_items.items()
        )
        context = ValidationContext(
            scope=scope,
            field_definition=definition,
            answers=answers,
            allowed_input_refs=(str(invocation_id),),
            source_bindings=(
                SourceBindingReceipt(
                    binding_ref=binding_ref,
                    project_ref=str(project_id),
                    article_ref=str(article_id),
                    snapshot_ref=str(source_snapshot_id),
                    input_ref=str(invocation_id),
                    input_kind="invocation",
                ),
            ),
            quote_matches=matches,
        )
        passages = [
            {
                "local_ref": f"quote-{index}",
                "quote": quote,
                "origin": "captured_input",
                "source_binding_ref": binding_ref,
                "quote_match_ref": (
                    check_items[index]["match_ref"] if index in check_items else None
                ),
            }
            for index, quote in enumerate(quotes)
        ]
        raw_links = decision.get("evidence_links", [])
        if not isinstance(raw_links, list):
            return None
        links = []
        for raw in raw_links:
            if not isinstance(raw, dict) or set(raw) != {"field_key", "quote_index"}:
                return None
            index = raw["quote_index"]
            if type(index) is not int or index < 0 or index >= len(quotes):
                return None
            links.append({"field_key": raw["field_key"], "local_ref": f"quote-{index}"})
        return {
            "envelope": build_envelope(context, passages, links).model_dump(mode="json"),
            "field_definition": definition.model_dump(mode="json"),
        }
    except (KeyError, TypeError, ValueError):
        return None
