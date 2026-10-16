"""Pure graph contract tests, independent of database/API conftest."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.evidence_links import (
    EvidenceEnvelopeV1,
    ValidationContext,
    build_envelope,
    legacy_envelope,
    legacy_quote_projection,
    preserve_legacy_round_trip,
    validate_envelope,
)

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "evidence_links_v1.json").read_text(encoding="utf-8")
)
FOREIGN_ID = "00000000-0000-0000-0000-000000000000"


def context(edit=None):
    payload = deepcopy(FIXTURE["context"])
    if edit:
        edit(payload)
    return ValidationContext.model_validate(payload)


def build(ctx=None, passages=None, links=None, previous=None):
    return build_envelope(
        ctx or context(),
        deepcopy(FIXTURE["passages"] if passages is None else passages),
        deepcopy(FIXTURE["links"] if links is None else links),
        previous=previous,
    )


def test_many_to_many_fixture_and_resume_have_stable_server_ids():
    envelope = build()
    assert envelope.model_dump(mode="json") == FIXTURE["expected_envelope"]
    assert len(envelope.evidence) == 2
    assert len(envelope.links) == 3
    assert sum(link.answer_ref == envelope.answers[0].answer_ref for link in envelope.links) == 2
    assert (
        sum(link.evidence_ref == envelope.evidence[1].evidence_ref for link in envelope.links) == 2
    )
    assert build(previous=envelope) == envelope
    assert validate_envelope(json.loads(envelope.model_dump_json()), context()) == envelope


@pytest.mark.parametrize(
    "path,value,error",
    [
        (("scope", "project_ref"), "other-project", "scope_mismatch"),
        (("scope", "article_ref"), "other-article", "scope_mismatch"),
        (("scope", "record_ref"), "other-record", "scope_mismatch"),
        (("scope", "revision_ref"), "other-revision", "scope_mismatch"),
        (("field_definition_ref",), "sha256:" + "0" * 64, "field_definition_mismatch"),
        (("answers", 0, "field_key"), "unknown", "answer_fields_mismatch"),
        (("answers", 0, "answer_ref"), FOREIGN_ID, "noncanonical_answer_ref"),
        (("answers", 0, "value"), "COHORT", "answer_value_mismatch"),
        (("evidence", 0, "evidence_ref"), FOREIGN_ID, "noncanonical_evidence_ref"),
        (("evidence", 0, "source_binding_ref"), "unknown", "unknown_source_binding"),
        (("evidence", 0, "quote_match_ref"), "unknown", "unknown_quote_match"),
        (("evidence", 0, "quote"), "Invented quote", "quote_match_quote_mismatch"),
        (("links", 0, "answer_ref"), FOREIGN_ID, "dangling_link"),
        (("links", 0, "evidence_ref"), FOREIGN_ID, "dangling_link"),
    ],
)
def test_invalid_submitted_claims_are_rejected_without_repair(path, value, error):
    payload = deepcopy(FIXTURE["expected_envelope"])
    cursor = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    unchanged = deepcopy(payload)
    with pytest.raises(ValueError, match=error):
        validate_envelope(payload, context())
    assert payload == unchanged


@pytest.mark.parametrize(
    "collection,error",
    [
        ("answers", "duplicate_answer_ref"),
        ("evidence", "duplicate_evidence_ref"),
        ("links", "duplicate_link"),
    ],
)
def test_duplicate_ids_or_edges_are_rejected(collection, error):
    payload = deepcopy(FIXTURE["expected_envelope"])
    payload[collection].append(deepcopy(payload[collection][0]))
    with pytest.raises(ValueError, match=error):
        validate_envelope(payload, context())


def test_duplicate_local_passage_id_with_changed_quote_is_rejected():
    passages = deepcopy(FIXTURE["passages"])
    duplicate = deepcopy(passages[0])
    duplicate["quote"] = "Different claim for the same local ID"
    passages.append(duplicate)
    with pytest.raises(ValueError, match="duplicate_local_ref"):
        build(passages=passages)


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("project_ref", "other-project", "source_binding_scope_mismatch"),
        ("article_ref", "other-article", "source_binding_scope_mismatch"),
        ("input_kind", "review_view", "source_binding_input_kind_mismatch"),
    ],
)
def test_source_receipts_must_match_scope_and_producer_kind(field, value, error):
    ctx = context(lambda payload: payload["source_bindings"][0].update({field: value}))
    with pytest.raises(ValueError, match=error):
        build(ctx)


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("project_ref", "other-project", "quote_match_scope_mismatch"),
        ("article_ref", "other-article", "quote_match_scope_mismatch"),
        ("snapshot_ref", "other-snapshot", "quote_match_source_or_input_mismatch"),
        ("input_ref", "other-input", "quote_match_source_or_input_mismatch"),
    ],
)
def test_match_receipt_cannot_reference_another_source_or_input(field, value, error):
    ctx = context(lambda payload: payload["quote_matches"][0].update({field: value}))
    with pytest.raises(ValueError, match=error):
        build(ctx)


def test_matching_receipts_for_an_unrelated_input_still_fail_allowed_input_scope():
    def edit(payload):
        payload["source_bindings"][0]["input_ref"] = "unrelated-call"
        for match in payload["quote_matches"]:
            match["input_ref"] = "unrelated-call"

    with pytest.raises(ValueError, match="input_not_allowed_for_record"):
        build(context(edit))


@pytest.mark.parametrize(
    "status",
    [
        "exact",
        "normalized",
        "not_found",
        "ambiguous",
        "invalid",
        "source_unavailable",
        "not_checked",
        "incomplete",
    ],
)
def test_link_validity_does_not_upgrade_any_match_status_to_support(status):
    ctx = context(lambda payload: payload["quote_matches"][0].update(status=status))
    envelope = build(ctx)
    assert envelope.evidence[0].quote_match_ref == "match-A"
    assert "support" not in envelope.model_dump()
    assert "assessment" not in envelope.evidence[0].model_dump()


def test_captured_input_can_have_no_match_and_still_makes_no_occurrence_claim():
    passages = deepcopy(FIXTURE["passages"])
    passages[0]["quote_match_ref"] = None
    envelope = build(passages=passages)
    assert envelope.evidence[0].origin == "captured_input"
    assert envelope.evidence[0].quote_match_ref is None


def test_annotation_receipts_can_bind_a_distinct_retained_review_input():
    def edit(payload):
        payload["scope"]["record_kind"] = "annotation"
        payload["allowed_input_refs"] = ["review-view-B"]
        payload["source_bindings"][0].update(input_kind="review_view", input_ref="review-view-B")
        for match in payload["quote_matches"]:
            match["input_ref"] = "review-view-B"

    assert build(context(edit)).scope.record_kind == "annotation"


@pytest.mark.parametrize(
    "field,value",
    [
        ("key", "renamed_design"),
        ("label", "Changed label"),
        ("options", ["RCT", "CASE"]),
    ],
)
def test_dynamic_field_definition_changes_cannot_retarget_old_evidence(field, value):
    def edit(payload):
        payload["field_definition"]["fields"][0][field] = value
        if field == "key":
            payload["answers"][0]["field_key"] = value

    with pytest.raises(ValueError, match="field_definition_mismatch"):
        validate_envelope(FIXTURE["expected_envelope"], context(edit))


def test_field_identity_is_exact_versioned_utf8_with_no_unicode_normalization():
    left = context(lambda payload: payload["field_definition"].update(question="café"))
    right = context(lambda payload: payload["field_definition"].update(question="cafe\u0301"))
    assert left.field_definition.identity != right.field_definition.identity
    assert (
        context().field_definition.identity == FIXTURE["expected_envelope"]["field_definition_ref"]
    )


def test_changed_answer_requires_new_revision_and_cannot_reuse_old_ids():
    previous = build()
    changed = context(lambda payload: payload["answers"][1].update(value=False))
    with pytest.raises(ValueError, match="immutable_revision_conflict"):
        build(changed, previous=previous)

    def edit(payload):
        payload["answers"][1]["value"] = False
        payload["scope"]["revision_ref"] = "revision-2"

    revised = build(context(edit), previous=previous)
    assert {answer.answer_ref for answer in previous.answers}.isdisjoint(
        answer.answer_ref for answer in revised.answers
    )
    assert {record.evidence_ref for record in previous.evidence}.isdisjoint(
        record.evidence_ref for record in revised.evidence
    )
    returned = build(
        context(lambda payload: payload["scope"].update(revision_ref="revision-3")),
        previous=revised,
    )
    assert returned.answers[1].value is True
    assert returned.answers[1].answer_ref != previous.answers[1].answer_ref


def test_changed_quote_cannot_reuse_an_immutable_revision():
    passages = deepcopy(FIXTURE["passages"])
    passages[0].update(quote="Changed claim", quote_match_ref=None)
    with pytest.raises(ValueError, match="immutable_revision_conflict"):
        build(passages=passages, previous=build())


@pytest.mark.parametrize("change", ["support", "assessment", "adjudication"])
def test_assessment_fields_and_producer_asserted_server_ids_are_forbidden(change):
    payload = deepcopy(FIXTURE["expected_envelope"])
    payload["evidence"][0][change] = "SUPPORTED"
    with pytest.raises(ValueError):
        validate_envelope(payload, context())
    passages = deepcopy(FIXTURE["passages"])
    passages[0]["evidence_ref"] = FOREIGN_ID
    with pytest.raises(ValueError):
        build(passages=passages)


def test_legacy_conversion_preserves_order_duplicates_empty_quotes_and_no_bindings():
    envelope = legacy_envelope(context().scope, FIXTURE["legacy_quotes"])
    assert envelope.model_dump(mode="json") == FIXTURE["expected_legacy"]
    assert legacy_quote_projection(envelope) == FIXTURE["legacy_quotes"]
    assert envelope.field_definition_ref is None
    assert envelope.answers == envelope.links == ()
    assert len({record.evidence_ref for record in envelope.evidence}) == 4
    assert all(
        record.source_binding_ref is None and record.quote_match_ref is None
        for record in envelope.evidence
    )


def test_mixed_structured_and_unlinked_legacy_evidence_preserves_both():
    passages = deepcopy(FIXTURE["passages"])
    passages.append({"local_ref": "legacy", "quote": "old quote", "origin": "legacy_unversioned"})
    envelope = build(passages=passages)
    assert legacy_quote_projection(envelope) == [
        "Mice were randomized.",
        "Allocation was blinded.",
        "old quote",
    ]
    links = deepcopy(FIXTURE["links"]) + [{"field_key": "design", "local_ref": "legacy"}]
    with pytest.raises(ValueError, match="legacy_evidence_cannot_gain_answer_links"):
        build(passages=passages, links=links)


def test_old_client_projection_echo_or_omission_never_erases_structured_graph():
    existing = build()
    assert preserve_legacy_round_trip(existing) is existing
    assert preserve_legacy_round_trip(existing, None) is existing
    assert preserve_legacy_round_trip(existing, legacy_quote_projection(existing)) is existing
    for changed in ([], ["changed"], list(reversed(legacy_quote_projection(existing)))):
        with pytest.raises(ValueError, match="legacy_update_conflict"):
            preserve_legacy_round_trip(existing, changed)


def test_explicit_legacy_downgrade_is_rejected_even_with_new_revision():
    previous = build()
    scope = context(lambda payload: payload["scope"].update(revision_ref="revision-2")).scope
    with pytest.raises(ValueError, match="structured_downgrade_forbidden"):
        legacy_envelope(scope, [], previous=previous)


@pytest.mark.parametrize("bad", [None, {}, "quote", [42], [True], ["x" * 16_385]])
def test_invalid_legacy_lists_are_rejected_without_truncation(bad):
    with pytest.raises(ValueError):
        legacy_envelope(context().scope, bad)


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_frozen_boolean_answers_are_not_coerced(value):
    with pytest.raises(ValueError):
        context(lambda payload: payload["answers"][1].update(value=value))


def test_draft_links_and_schema_version_cannot_be_guessed():
    for link in (
        {"field_key": "unknown", "local_ref": "p-blinding"},
        {"field_key": "design", "local_ref": "typo"},
    ):
        with pytest.raises(ValueError, match="unknown_local_link_target"):
            build(links=[link])
    payload = deepcopy(FIXTURE["expected_envelope"])
    del payload["schema_version"]
    with pytest.raises(ValueError):
        validate_envelope(payload, context())


def test_json_schema_snapshot_matches_generated_contract():
    schema = json.loads(
        (
            Path(__file__).parents[3] / "docs" / "schemas" / "evidence-envelope-v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema == EvidenceEnvelopeV1.model_json_schema()
    assert schema["additionalProperties"] is False


def test_envelope_keeps_frozen_and_extra_forbidden_configuration():
    assert EvidenceEnvelopeV1.model_config["extra"] == "forbid"
    assert EvidenceEnvelopeV1.model_config["frozen"] is True
    envelope = build()
    with pytest.raises(ValueError, match="frozen_instance"):
        envelope.state = "legacy_unlinked"
    assert isinstance(envelope.evidence, tuple)
    assert isinstance(context().field_definition.fields, tuple)


def test_empty_allowed_input_refs_fail_closed_but_legacy_needs_no_input_binding():
    with pytest.raises(ValueError, match="input_not_allowed_for_record"):
        build(context(lambda payload: payload.update(allowed_input_refs=[])))
    legacy = legacy_envelope(context().scope, FIXTURE["legacy_quotes"])
    assert legacy.state == "legacy_unlinked"
    assert ValidationContext(scope=legacy.scope, field_definition=None).allowed_input_refs == ()


@pytest.mark.parametrize(
    "collection,error",
    [
        ("source_bindings", "duplicate_source_binding_receipt"),
        ("quote_matches", "duplicate_quote_match_receipt"),
    ],
)
def test_duplicate_trusted_receipt_ids_cannot_be_resolved_by_first_match(collection, error):
    def edit(payload):
        duplicate = deepcopy(payload[collection][0])
        duplicate["snapshot_ref"] = "contradictory-snapshot"
        payload[collection].append(duplicate)

    with pytest.raises(ValueError, match=error):
        context(edit)


def test_legacy_evidence_cannot_claim_source_capture():
    payload = deepcopy(FIXTURE["expected_legacy"])
    payload["evidence"][0]["source_binding_ref"] = "binding-A"
    with pytest.raises(ValueError, match="legacy_cannot_claim_source_or_match"):
        validate_envelope(payload, context())


def test_other_records_previous_revision_is_rejected():
    other = build(context(lambda payload: payload["scope"].update(record_ref="prediction-B")))
    with pytest.raises(ValueError, match="previous_record_scope_mismatch"):
        build(previous=other)


def test_graph_size_limit_rejects_without_truncation():
    passages = [deepcopy(FIXTURE["passages"][0])] * 513
    with pytest.raises(ValueError, match="graph_limit_exceeded"):
        build(passages=passages)
    assert len(passages) == 513
