"""Persist only source-occurrence observations from validated target input origins."""

import json
from pathlib import Path

from app.provenance import InvocationV1
from app.quote_checks import MAX_QUOTES, check_prediction_quotes, target_article_input

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures/provenance_v1.json").read_text("utf-8"))


def call(case: int) -> InvocationV1:
    return InvocationV1.model_validate(FIXTURE["invocations"][case]["envelope"])


def test_example_only_text_never_becomes_target_match():
    actual = call(4)
    source = target_article_input(actual)
    assert all(span.payload_path == '["messages",1,"content"]' for span in source.spans)
    checks = check_prediction_quotes(
        actual,
        {"evidence_quotes": ["Example:", "Mice α.", "Mice\u00a0α.", "mice α.", 3]},
    )
    assert [item["result"]["status"] for item in checks["items"]] == [
        "not_found",
        "exact",
        "normalized",
        "not_found",
        "invalid",
    ]
    assert checks["items"][1]["result"]["candidates"][0]["component_ref"] == (
        '["sections",0,"text"]'
    )
    assert checks["items"][1]["result"]["candidates"][0]["component_start"] == 0
    assert "support" not in json.dumps(checks)


def test_repeat_insertions_have_distinct_input_occurrences_but_one_location():
    checks = check_prediction_quotes(call(2), {"evidence_quotes": ["Mice α."]})
    match = checks["items"][0]["result"]
    assert match["input_occurrence_count"] > 1
    assert match["distinct_source_location_count"] == 1
    assert match["unique_input_occurrence"] is False
    assert match["unique_source_location"] is True


def test_missing_invalid_empty_and_oversized_lists_are_explicit():
    actual = call(4)
    assert check_prediction_quotes(actual, {})["state"] == "missing_quote_field"
    assert check_prediction_quotes(actual, {"evidence_quotes": "not a list"})["state"] == (
        "invalid_quote_list"
    )
    assert check_prediction_quotes(actual, {"evidence_quotes": []})["state"] == ("empty_quote_list")
    checks = check_prediction_quotes(
        actual, {"evidence_quotes": ["not present"] * (MAX_QUOTES + 1)}
    )
    assert checks["state"] == "incomplete"
    assert checks["list_complete"] is False and checks["overflow_count"] == 1
    assert len(checks["items"]) == MAX_QUOTES
    assert len({item["match_ref"] for item in checks["items"]}) == MAX_QUOTES


def test_refs_are_stable_for_same_invocation_and_distinct_for_retries():
    first = call(4)
    a = check_prediction_quotes(first, {"evidence_quotes": ["Mice α."]})
    b = check_prediction_quotes(first, {"evidence_quotes": ["Mice α."]})
    other = call(0)
    c = check_prediction_quotes(other, {"evidence_quotes": ["Mice α."]})
    assert a == b
    assert a["items"][0]["match_ref"] != c["items"][0]["match_ref"]
