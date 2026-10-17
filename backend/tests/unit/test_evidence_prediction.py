"""The normal prediction adapter never invents answer support from global quotes."""

import uuid
from types import SimpleNamespace

from app.evidence_links import EvidenceEnvelopeV1, FrozenFieldDefinition
from app.evidence_prediction import prediction_envelope

PROJECT = uuid.uuid4()
ARTICLE = uuid.uuid4()
PREDICTION = uuid.uuid4()
INVOCATION = uuid.uuid4()
SNAPSHOT = uuid.uuid4()


def _graph(decision, checks=None):
    return prediction_envelope(
        prediction_id=PREDICTION,
        project_id=PROJECT,
        article_id=ARTICLE,
        project=SimpleNamespace(
            question="Were mice randomized?",
            fields=[{"key": "randomized", "label": "Randomized", "type": "boolean"}],
        ),
        decision=decision,
        invocation_id=INVOCATION,
        source_snapshot_id=SNAPSHOT,
        quote_checks=checks
        if checks is not None
        else {
            "items": [
                {
                    "quote_index": 0,
                    "match_ref": str(uuid.uuid4()),
                    "result": {
                        "quote": "Mice were randomized.",
                        "input_ref": str(INVOCATION),
                        "status": "exact",
                    },
                }
            ]
        },
    )


def test_valid_answers_remain_unlinked_without_explicit_mapping():
    result = _graph({"randomized": True, "evidence_quotes": ["Mice were randomized."]})
    envelope = EvidenceEnvelopeV1.model_validate(result["envelope"])
    definition = FrozenFieldDefinition.model_validate(result["field_definition"])
    assert envelope.field_definition_ref == definition.identity
    assert envelope.scope.record_ref == str(PREDICTION)
    assert len(envelope.answers) == len(envelope.evidence) == 1
    assert envelope.links == ()
    assert envelope.evidence[0].quote_match_ref is not None


def test_not_found_quote_retains_context_without_occurrence_claim():
    checks = {
        "items": [
            {
                "quote_index": 0,
                "match_ref": str(uuid.uuid4()),
                "result": {
                    "quote": "Mice were randomized.",
                    "input_ref": str(INVOCATION),
                    "status": "not_found",
                },
            }
        ]
    }
    result = _graph({"randomized": True, "evidence_quotes": ["Mice were randomized."]}, checks)
    envelope = EvidenceEnvelopeV1.model_validate(result["envelope"])
    assert envelope.evidence[0].origin == "captured_input_context"
    assert envelope.evidence[0].quote_match_ref == checks["items"][0]["match_ref"]
    assert envelope.links == ()


def test_overflow_quote_has_context_but_no_quote_match_receipt():
    result = _graph(
        {
            "randomized": True,
            "evidence_quotes": ["Mice were randomized.", "Unassessed overflow quote"],
        }
    )
    envelope = EvidenceEnvelopeV1.model_validate(result["envelope"])
    assert [item.origin for item in envelope.evidence] == [
        "captured_input_context",
        "captured_input_context",
    ]
    assert envelope.evidence[0].quote_match_ref is not None
    assert envelope.evidence[1].quote_match_ref is None
    assert envelope.links == ()


def test_explicit_field_quote_mapping_creates_only_claimed_edge():
    result = _graph(
        {
            "randomized": True,
            "evidence_quotes": ["Mice were randomized."],
            "evidence_links": [{"field_key": "randomized", "quote_index": 0}],
        }
    )
    envelope = EvidenceEnvelopeV1.model_validate(result["envelope"])
    assert len(envelope.links) == 1
    assert envelope.links[0].answer_ref == envelope.answers[0].answer_ref
    assert envelope.links[0].evidence_ref == envelope.evidence[0].evidence_ref


def test_invalid_mapping_and_tampered_quote_check_fail_closed():
    base = {"randomized": True, "evidence_quotes": ["Mice were randomized."]}
    assert _graph({**base, "evidence_links": [{"field_key": "other", "quote_index": 0}]}) is None
    bad_index = {**base, "evidence_links": [{"field_key": "randomized", "quote_index": True}]}
    assert _graph(bad_index) is None
    tampered = {
        "items": [
            {
                "quote_index": 0,
                "match_ref": str(uuid.uuid4()),
                "result": {
                    "quote": "different",
                    "input_ref": str(INVOCATION),
                    "status": "exact",
                },
            }
        ]
    }
    assert _graph(base, tampered) is None


def test_incomplete_answers_preserve_quotes_as_legacy_unlinked():
    result = _graph({"evidence_quotes": ["Mice were randomized."]})
    envelope = EvidenceEnvelopeV1.model_validate(result["envelope"])
    assert envelope.state == "legacy_unlinked"
    assert envelope.answers == envelope.links == ()
    assert result["field_definition"] is None


def test_invalid_quote_list_yields_no_graph():
    assert _graph({"randomized": True, "evidence_quotes": "Mice were randomized."}) is None
