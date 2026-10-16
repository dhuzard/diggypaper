"""Pure answer/evidence graph contract; no persistence or support assessment.

ValidationContext is authoritative server input, never reconstructed from a
submitted graph. Validation preserves claims or rejects them; it repairs none.
"""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

from app.schema import STANDARD_KEYS, validate_decision

Ref = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")]
LocalRef = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")]
FieldKey = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
CanonicalId = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")]
DefinitionRef = Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Quote = Annotated[StrictStr, Field(max_length=16_384)]
AnswerValue = StrictBool | Annotated[StrictStr, Field(max_length=16_384)]
MatchStatus = Literal[
    "exact",
    "normalized",
    "not_found",
    "ambiguous",
    "invalid",
    "source_unavailable",
    "not_checked",
    "incomplete",
]
_ID_NAMESPACE = UUID("c78ee78a-681f-5f94-93c6-fd53fc1653be")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


def canonical_json(value: object) -> bytes:
    """Version-1 encoding: sorted keys, compact UTF-8, no Unicode normalization."""
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class FieldSpec(_FrozenModel):
    key: FieldKey
    label: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    type: Literal["boolean", "choice", "text"]
    options: Annotated[
        tuple[Annotated[StrictStr, Field(max_length=1_000)], ...], Field(max_length=256)
    ] = ()
    primary: StrictBool = False
    metric: Literal["accuracy", "recall", "precision", "f1"] | None = None

    @model_validator(mode="after")
    def valid_field(self):
        if self.key in STANDARD_KEYS:
            raise ValueError("reserved_field_key")
        if self.type == "choice":
            if len(self.options) < 2 or len(set(self.options)) != len(self.options):
                raise ValueError("choice_requires_distinct_options")
            if any(not option for option in self.options):
                raise ValueError("empty_choice_option")
        elif self.options:
            raise ValueError("options_require_choice")
        if (self.primary or self.metric is not None) and self.type == "text":
            raise ValueError("text_cannot_be_primary")
        if self.metric is not None and not self.primary:
            raise ValueError("metric_requires_primary")
        return self


class FrozenFieldDefinition(_FrozenModel):
    schema_version: Literal["field-definition-v1"] = "field-definition-v1"
    question: Annotated[StrictStr, Field(max_length=20_000)]
    fields: Annotated[tuple[FieldSpec, ...], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def unique_fields(self):
        if len({field.key for field in self.fields}) != len(self.fields):
            raise ValueError("duplicate_field_key")
        if sum(field.primary for field in self.fields) > 1:
            raise ValueError("multiple_primary_fields")
        return self

    @property
    def identity(self) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(self.model_dump(mode="json"))).hexdigest()


class EnvelopeScope(_FrozenModel):
    project_ref: Ref
    article_ref: Ref
    record_kind: Literal["prediction", "benchmark_prediction", "annotation"]
    record_ref: Ref
    revision_ref: Ref


class AnswerRecord(_FrozenModel):
    answer_ref: CanonicalId
    field_key: FieldKey
    value: AnswerValue


class EvidenceRecord(_FrozenModel):
    """A model quote with source context, not an occurrence or support claim."""

    evidence_ref: CanonicalId
    local_ref: LocalRef
    quote: Quote
    origin: Literal["captured_input_context", "legacy_unversioned"]
    source_binding_ref: Ref | None = None
    quote_match_ref: Ref | None = None

    @model_validator(mode="after")
    def valid_origin(self):
        self.quote.encode("utf-8")
        if self.origin == "legacy_unversioned":
            if self.source_binding_ref is not None or self.quote_match_ref is not None:
                raise ValueError("legacy_cannot_claim_source_or_match")
        elif self.source_binding_ref is None:
            raise ValueError("captured_input_context_requires_binding")
        return self


class EvidenceLink(_FrozenModel):
    answer_ref: CanonicalId
    evidence_ref: CanonicalId


class EvidenceEnvelopeV1(_FrozenModel):
    model_config = ConfigDict(
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:diggypaper:evidence-envelope:v1",
        }
    )
    schema_version: Literal["evidence-envelope-v1"]
    state: Literal["structured", "legacy_unlinked"]
    scope: EnvelopeScope
    field_definition_ref: DefinitionRef | None
    answers: Annotated[tuple[AnswerRecord, ...], Field(max_length=128)]
    evidence: Annotated[tuple[EvidenceRecord, ...], Field(max_length=512)]
    links: Annotated[tuple[EvidenceLink, ...], Field(max_length=4_096)]


class SourceBindingReceipt(_FrozenModel):
    """Trusted adapter receipt, resolved from retained #2 records, not model claims."""

    binding_ref: Ref
    project_ref: Ref
    article_ref: Ref
    snapshot_ref: Ref
    input_ref: Ref
    input_kind: Literal["invocation", "review_view"]


class QuoteMatchReceipt(_FrozenModel):
    """Trusted reference to an actual #3 result, including unchecked/incomplete states."""

    match_ref: Ref
    project_ref: Ref
    article_ref: Ref
    snapshot_ref: Ref
    input_ref: Ref
    quote: Quote
    status: MatchStatus


class ExpectedAnswer(_FrozenModel):
    field_key: FieldKey
    value: AnswerValue


class ValidationContext(_FrozenModel):
    scope: EnvelopeScope
    field_definition: FrozenFieldDefinition | None
    answers: Annotated[tuple[ExpectedAnswer, ...], Field(max_length=128)] = ()
    allowed_input_refs: Annotated[tuple[Ref, ...], Field(max_length=128)] = ()
    source_bindings: Annotated[tuple[SourceBindingReceipt, ...], Field(max_length=512)] = ()
    quote_matches: Annotated[tuple[QuoteMatchReceipt, ...], Field(max_length=512)] = ()

    @model_validator(mode="after")
    def valid_context(self):
        _index(self.answers, "field_key", "duplicate_expected_field")
        _index(self.source_bindings, "binding_ref", "duplicate_source_binding_receipt")
        _index(self.quote_matches, "match_ref", "duplicate_quote_match_receipt")
        if len(set(self.allowed_input_refs)) != len(self.allowed_input_refs):
            raise ValueError("duplicate_allowed_input_ref")
        if self.field_definition is None:
            if self.answers:
                raise ValueError("unversioned_fields_cannot_bind_answers")
        else:
            fields = self.field_definition.fields
            values = {answer.field_key: answer.value for answer in self.answers}
            if set(values) != {field.key for field in fields}:
                raise ValueError("expected_answers_must_match_frozen_fields")
            errors = validate_decision([field.model_dump() for field in fields], values)
            if errors:
                raise ValueError("invalid_expected_answer: " + "; ".join(errors))
        return self


class PassageDraft(_FrozenModel):
    """Producer-local passage claim; origin never establishes occurrence."""

    local_ref: LocalRef
    quote: Quote
    origin: Literal["captured_input_context", "legacy_unversioned"] = "captured_input_context"
    source_binding_ref: Ref | None = None
    quote_match_ref: Ref | None = None


class LinkDraft(_FrozenModel):
    field_key: FieldKey
    local_ref: LocalRef


def _index(items, attribute: str, error: str) -> dict:
    result = {}
    for item in items:
        key = getattr(item, attribute)
        if key in result:
            raise ValueError(error)
        result[key] = item
    return result


def _assigned_id(scope: EnvelopeScope, kind: str, local: str, definition_ref: str | None) -> str:
    payload = ["evidence-id-v1", scope.model_dump(mode="json"), kind, definition_ref, local]
    return str(uuid5(_ID_NAMESPACE, canonical_json(payload).decode("utf-8")))


def answer_id(scope: EnvelopeScope, definition_ref: str, field_key: str) -> str:
    return _assigned_id(scope, "answer", field_key, definition_ref)


def evidence_id(scope: EnvelopeScope, local_ref: str) -> str:
    return _assigned_id(scope, "evidence", local_ref, None)


def validate_envelope(
    payload: object, context: ValidationContext, *, previous: EvidenceEnvelopeV1 | None = None
) -> EvidenceEnvelopeV1:
    """Reject invalid claims against trusted scope, frozen answers and receipts.

    A supplied previous envelope must be the authoritative stored prior version.
    Same-revision changes fail; a byte-equivalent semantic replay is idempotent.
    """
    envelope = EvidenceEnvelopeV1.model_validate(payload)
    if envelope.scope != context.scope:
        raise ValueError("scope_mismatch")
    if envelope.state == "legacy_unlinked":
        if envelope.field_definition_ref is not None or envelope.answers or envelope.links:
            raise ValueError("legacy_must_remain_unlinked_and_unversioned")
        if any(record.origin != "legacy_unversioned" for record in envelope.evidence):
            raise ValueError("legacy_cannot_claim_captured_input_context")
    else:
        if context.field_definition is None:
            raise ValueError("structured_requires_frozen_field_definition")
        if envelope.field_definition_ref != context.field_definition.identity:
            raise ValueError("field_definition_mismatch")
    answers = _index(envelope.answers, "answer_ref", "duplicate_answer_ref")
    by_field = _index(envelope.answers, "field_key", "duplicate_answer_field")
    expected = {answer.field_key: answer.value for answer in context.answers}
    if envelope.state == "structured" and set(by_field) != set(expected):
        raise ValueError("answer_fields_mismatch")
    for answer in envelope.answers:
        if answer.answer_ref != answer_id(
            context.scope, envelope.field_definition_ref, answer.field_key
        ):
            raise ValueError("noncanonical_answer_ref")
        if type(answer.value) is not type(expected[answer.field_key]) or (
            answer.value != expected[answer.field_key]
        ):
            raise ValueError("answer_value_mismatch")
    evidence = _index(envelope.evidence, "evidence_ref", "duplicate_evidence_ref")
    _index(envelope.evidence, "local_ref", "duplicate_local_ref")
    bindings = _index(context.source_bindings, "binding_ref", "duplicate_source_binding_receipt")
    matches = _index(context.quote_matches, "match_ref", "duplicate_quote_match_receipt")
    for record in envelope.evidence:
        if record.evidence_ref != evidence_id(context.scope, record.local_ref):
            raise ValueError("noncanonical_evidence_ref")
        if record.origin == "legacy_unversioned":
            continue
        binding = bindings.get(record.source_binding_ref)
        if binding is None:
            raise ValueError("unknown_source_binding")
        scope = (context.scope.project_ref, context.scope.article_ref)
        if (binding.project_ref, binding.article_ref) != scope:
            raise ValueError("source_binding_scope_mismatch")
        if binding.input_ref not in context.allowed_input_refs:
            raise ValueError("input_not_allowed_for_record")
        expected_kind = "review_view" if context.scope.record_kind == "annotation" else "invocation"
        if binding.input_kind != expected_kind:
            raise ValueError("source_binding_input_kind_mismatch")
        if record.quote_match_ref is not None:
            match = matches.get(record.quote_match_ref)
            if match is None:
                raise ValueError("unknown_quote_match")
            if (match.project_ref, match.article_ref) != scope:
                raise ValueError("quote_match_scope_mismatch")
            if (match.snapshot_ref, match.input_ref) != (binding.snapshot_ref, binding.input_ref):
                raise ValueError("quote_match_source_or_input_mismatch")
            if match.quote != record.quote:
                raise ValueError("quote_match_quote_mismatch")
    seen_links = set()
    for link in envelope.links:
        key = (link.answer_ref, link.evidence_ref)
        if key in seen_links:
            raise ValueError("duplicate_link")
        seen_links.add(key)
        if link.answer_ref not in answers or link.evidence_ref not in evidence:
            raise ValueError("dangling_link")
        if evidence[link.evidence_ref].origin == "legacy_unversioned":
            raise ValueError("legacy_evidence_cannot_gain_answer_links")
    if previous is not None:
        before = previous.scope.model_dump(exclude={"revision_ref"})
        after = envelope.scope.model_dump(exclude={"revision_ref"})
        if before != after:
            raise ValueError("previous_record_scope_mismatch")
        if previous.state == "structured" and envelope.state == "legacy_unlinked":
            raise ValueError("structured_downgrade_forbidden")
        if previous.scope.revision_ref == envelope.scope.revision_ref and (
            canonical_json(previous.model_dump(mode="json"))
            != canonical_json(envelope.model_dump(mode="json"))
        ):
            raise ValueError("immutable_revision_conflict")
    return envelope


def build_envelope(
    context: ValidationContext,
    passages: list[dict],
    links: list[dict],
    *,
    previous: EvidenceEnvelopeV1 | None = None,
) -> EvidenceEnvelopeV1:
    """Assign authoritative IDs to validated producer-local passage/field claims."""
    if context.field_definition is None:
        raise ValueError("structured_requires_frozen_field_definition")
    if len(passages) > 512 or len(links) > 4_096:
        raise ValueError("graph_limit_exceeded")
    drafts = tuple(PassageDraft.model_validate(item) for item in passages)
    local = _index(drafts, "local_ref", "duplicate_local_ref")
    draft_links = tuple(LinkDraft.model_validate(item) for item in links)
    definition = context.field_definition.identity
    answers = tuple(
        AnswerRecord(
            answer_ref=answer_id(context.scope, definition, answer.field_key),
            field_key=answer.field_key,
            value=answer.value,
        )
        for answer in context.answers
    )
    answer_refs = {answer.field_key: answer.answer_ref for answer in answers}
    evidence = tuple(
        EvidenceRecord(
            evidence_ref=evidence_id(context.scope, draft.local_ref), **draft.model_dump()
        )
        for draft in drafts
    )
    resolved = []
    for link in draft_links:
        if link.field_key not in answer_refs or link.local_ref not in local:
            raise ValueError("unknown_local_link_target")
        resolved.append(
            EvidenceLink(
                answer_ref=answer_refs[link.field_key],
                evidence_ref=evidence_id(context.scope, link.local_ref),
            )
        )
    envelope = EvidenceEnvelopeV1(
        schema_version="evidence-envelope-v1",
        state="structured",
        scope=context.scope,
        field_definition_ref=definition,
        answers=answers,
        evidence=evidence,
        links=tuple(resolved),
    )
    return validate_envelope(envelope, context, previous=previous)


def legacy_envelope(
    scope: EnvelopeScope, quotes: list[str], *, previous: EvidenceEnvelopeV1 | None = None
) -> EvidenceEnvelopeV1:
    """Capture global quotes without inventing frozen fields, source or answer links."""
    if not isinstance(quotes, list) or len(quotes) > 512:
        raise ValueError("invalid_legacy_quote_list")
    records = tuple(
        EvidenceRecord(
            evidence_ref=evidence_id(scope, f"legacy-{index}"),
            local_ref=f"legacy-{index}",
            quote=quote,
            origin="legacy_unversioned",
        )
        for index, quote in enumerate(quotes)
    )
    envelope = EvidenceEnvelopeV1(
        schema_version="evidence-envelope-v1",
        state="legacy_unlinked",
        scope=scope,
        field_definition_ref=None,
        answers=(),
        evidence=records,
        links=(),
    )
    return validate_envelope(
        envelope, ValidationContext(scope=scope, field_definition=None), previous=previous
    )


def legacy_quote_projection(envelope: EvidenceEnvelopeV1) -> list[str]:
    """One string per evidence record in declared order; preserve duplicate strings."""
    return [record.quote for record in envelope.evidence]


def preserve_legacy_round_trip(
    existing: EvidenceEnvelopeV1, submitted_quotes: object = None
) -> EvidenceEnvelopeV1:
    """Preserve a validated graph on an omitted/null/unchanged old-client projection.

    Changed global quotes require an explicit revision-aware update. This helper
    never downgrades a structured graph or guesses which fields an edit targets.
    It is not an authorization, concurrency, or answer-update handler.
    """
    if submitted_quotes is None:
        return existing
    if not isinstance(submitted_quotes, list) or any(
        type(item) is not str for item in submitted_quotes
    ):
        raise ValueError("invalid_legacy_quote_list")
    if submitted_quotes != legacy_quote_projection(existing):
        raise ValueError("legacy_update_conflict_requires_explicit_revision")
    return existing
