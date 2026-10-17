"""Offline replay rejects inconsistent artifacts and bounds quote emission claims."""

import base64
import copy
import hashlib
import json
import uuid
from pathlib import Path

import pytest

from app.portable_replay import ReplayError, main, validate_manifest, validate_manifest_bytes
from app.provenance import (
    InvocationV1,
    ResponseCapture,
    SourceSnapshotV1,
    canonical_json,
    digest,
    prepare_invocation,
)
from app.quote_checks import check_prediction_quotes

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures/provenance_v1.json").read_text(encoding="utf-8")
)
ADVERSARIAL = json.loads(
    (Path(__file__).parents[1] / "fixtures/portable_replay_adversarial_v1.json").read_text(
        encoding="utf-8"
    )
)
EXAMPLE = Path(__file__).parents[3] / "docs/examples/seip-portable-replay-v1.json"


def frozen(envelope):
    raw = canonical_json(envelope)
    return {
        "id": envelope["id"],
        "canonical_base64": base64.b64encode(raw).decode(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def replace_envelope(entry, envelope):
    entry.update(frozen(envelope))


def replace_raw_response(actual, raw):
    response = ResponseCapture(
        origin="driver_raw",
        raw_availability="retained",
        raw_base64=base64.b64encode(raw).decode(),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
    ).model_dump(mode="json")
    actual["invocations"][0]["response_capture"] = response
    actual["invocations"][0]["response_sha256"] = digest(response)


def chat_response(decision):
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(decision, ensure_ascii=False)}}]},
        ensure_ascii=False,
    ).encode("utf-8")


def manifest(quote="Mice α."):
    source = SourceSnapshotV1.model_validate(FIXTURE["snapshot"])
    search_id = "00000000-0000-0000-0000-000000000077"
    call_payload = copy.deepcopy(FIXTURE["invocations"][4]["envelope"])
    call_payload["search_id"] = search_id
    call = InvocationV1.model_validate(call_payload)
    response = ResponseCapture(
        origin="driver_raw",
        raw_availability="retained",
        raw_base64=base64.b64encode(b'{"ok":true}').decode(),
        raw_sha256=hashlib.sha256(b'{"ok":true}').hexdigest(),
    ).model_dump(mode="json")
    checks = json.loads(json.dumps(check_prediction_quotes(call, {"evidence_quotes": [quote]})))
    return {
        "schema_version": "seip-portable-replay-v1",
        "canonical_encoding": "seip-json-v1",
        "sources": [frozen(source.model_dump(mode="json"))],
        "invocations": [
            {
                **frozen(call.model_dump(mode="json")),
                "outcome": "succeeded",
                "response_capture": response,
                "response_sha256": digest(response),
            }
        ],
        "results": [
            {
                "id": str(uuid.UUID("00000000-0000-0000-0000-000000000099")),
                "project_id": str(source.project_id),
                "article_id": str(source.article_id),
                "search_id": search_id,
                "capture_state": "captured",
                "source_snapshot_ref": str(source.id),
                "invocation_ref": str(call.id),
                "reason": None,
                "quote_state": "recorded",
                "quote_reason": None,
                "quotation_field": {"present": True, "value": [quote]},
                "quote_checks": checks,
                "quote_checks_sha256": digest(checks),
            }
        ],
    }


def test_replays_example_from_retained_canonical_bytes():
    actual = manifest()
    assert validate_manifest(actual) == {
        "sources": 1,
        "invocations": 1,
        "results": 1,
        "status": "bounded_occurrence_replay",
        "quotation_emission": "unverified",
        "quotation_emission_by_result": {actual["results"][0]["id"]: "unverified"},
    }
    assert validate_manifest_bytes(json.dumps(actual).encode()) == validate_manifest(actual)
    assert actual["results"][0]["quote_checks"]["items"][0]["result"]["status"] == "exact"


def test_checked_in_example_and_cli(capsys):
    assert validate_manifest_bytes(EXAMPLE.read_bytes())["status"] == "bounded_occurrence_replay"
    assert main([str(EXAMPLE)]) == 0
    assert json.loads(capsys.readouterr().out)["results"] == 1


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"first","schema_version":"second"}',
        b'{"value":NaN}',
        b"[]",
    ],
)
def test_rejects_ambiguous_or_non_object_manifest_json(raw):
    with pytest.raises(ReplayError):
        validate_manifest_bytes(raw)


def test_replays_normalized_occurrence_without_upgrading_to_support():
    actual = manifest("Mice\u00a0α.")
    assert actual["results"][0]["quote_checks"]["items"][0]["result"]["status"] == ("normalized")
    assert validate_manifest(actual)["status"] == "bounded_occurrence_replay"
    assert "support" not in json.dumps(actual)


def test_legacy_and_unavailable_states_are_explicit():
    actual = manifest()
    item = actual["results"][0]
    item.update(
        capture_state="legacy_unversioned",
        source_snapshot_ref=None,
        invocation_ref=None,
        reason="historical prediction has no captured call",
        quote_state="legacy_unversioned",
        quote_reason=None,
        quotation_field=None,
        quote_checks=None,
        quote_checks_sha256=None,
    )
    assert validate_manifest(actual)["results"] == 1
    item.update(
        capture_state="source_unavailable",
        quote_state="unavailable",
        reason="source could not be retained",
        quote_reason="input unavailable",
    )
    assert validate_manifest(actual)["results"] == 1
    item["quote_reason"] = None
    with pytest.raises(ReplayError, match="quote unavailability reason"):
        validate_manifest(actual)


@pytest.mark.parametrize(
    "case",
    [
        "invocation_hash",
        "response_hash",
        "dangling_source",
        "input_origin",
        "unbound_result",
        "duplicate_result",
    ],
)
def test_adversarial_manifest_is_rejected(case):
    actual = manifest()
    item = actual["results"][0]
    if case == "invocation_hash":
        actual["invocations"][0]["sha256"] = "0" * 64
    elif case == "response_hash":
        actual["invocations"][0]["response_capture"]["raw_sha256"] = "0" * 64
        actual["invocations"][0]["response_sha256"] = digest(
            actual["invocations"][0]["response_capture"]
        )
    elif case == "dangling_source":
        actual["sources"] = []
    elif case == "input_origin":
        envelope = json.loads(base64.b64decode(actual["invocations"][0]["canonical_base64"]))
        target = next(s for s in envelope["input_spans"] if s["origin"] == "target")
        target["source_start"] += 1
        replace_envelope(actual["invocations"][0], envelope)
    elif case == "unbound_result":
        actual["invocations"][0].update(
            outcome="prepared", response_capture=None, response_sha256=None
        )
    elif case == "duplicate_result":
        actual["results"].append(copy.deepcopy(item))
    with pytest.raises(ReplayError):
        validate_manifest(actual)


@pytest.mark.parametrize("case", ADVERSARIAL["cases"], ids=lambda case: case["name"])
def test_adversarial_fixture_is_rejected(case):
    actual = manifest()
    target = actual
    for part in case["path"][:-1]:
        target = target[part]
    target[case["path"][-1]] = case["value"]
    if case.get("recompute_quote_checks_sha256"):
        result = actual["results"][0]
        result["quote_checks_sha256"] = digest(result["quote_checks"])
    with pytest.raises(ReplayError):
        validate_manifest(actual)


def test_rejects_size_before_parsing(monkeypatch):
    monkeypatch.setattr("app.portable_replay.MAX_MANIFEST_BYTES", 8)
    with pytest.raises(ReplayError, match="byte bound"):
        validate_manifest_bytes(b"not-json-but-larger-than-eight")


def test_missing_quote_field_and_scorer_non_producer_states():
    actual = manifest()
    item = actual["results"][0]
    call = InvocationV1.model_validate(FIXTURE["invocations"][4]["envelope"])
    checks = json.loads(json.dumps(check_prediction_quotes(call, {})))
    item.update(
        quotation_field={"present": False, "value": None},
        quote_checks=checks,
        quote_checks_sha256=digest(checks),
    )
    assert validate_manifest(actual)["results"] == 1
    item["quotation_field"] = {"present": False, "value": ["fabricated"]}
    with pytest.raises(ReplayError, match="missing quotation field"):
        validate_manifest(actual)


def test_input_occurrence_does_not_prove_producer_emitted_quote():
    actual = manifest()
    response = actual["invocations"][0]["response_capture"]
    assert json.loads(base64.b64decode(response["raw_base64"])) == {"ok": True}
    assert actual["results"][0]["quotation_field"]["value"] == ["Mice α."]
    result = validate_manifest(actual)
    assert result["status"] == "bounded_occurrence_replay"
    assert result["quotation_emission"] == "unverified"


def test_exact_legacy_chat_quote_list_emission_is_verified():
    actual = manifest()
    replace_raw_response(actual, chat_response({"evidence_quotes": ["Mice α."]}))
    result = validate_manifest(actual)
    assert result["quotation_emission"] == "verified"
    assert result["quotation_emission_by_result"][actual["results"][0]["id"]] == "verified"


@pytest.mark.parametrize(
    "response",
    [
        chat_response({"evidence_quotes": []}),
        chat_response({"evidence_quotes": ["changed"]}),
        b'{"choices":',
        b'{"choices":[{"message":{"content":"not JSON"}}]}',
        b'{"choices":[{"message":{"content":{"evidence_quotes":["Mice \\u03b1."]}}}]}',
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"evidence_quotes":["Mice α."],"evidence_quotes":["Mice α."]}'
                            )
                        }
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        chat_response({"evidence_quotes": ["Mice α."]}).replace(
            b'{"choices":', b'{"overflow":1e999,"choices":', 1
        ),
        json.dumps(
            {
                "choices": [
                    {"message": {"content": '{"evidence_quotes":["Mice α."],"overflow":1e999}'}}
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8"),
    ],
    ids=[
        "input_quote_absent_from_response",
        "changed_emitted_quote",
        "malformed_response_body",
        "invalid_decision_json",
        "structured_content_uncovered",
        "duplicate_decision_key",
        "outer_overflow_number",
        "nested_decision_overflow_number",
    ],
)
def test_unmatched_or_uncovered_chat_response_stays_unverified(response):
    actual = manifest()
    replace_raw_response(actual, response)
    assert validate_manifest(actual)["quotation_emission"] == "unverified"


def test_duplicate_claimed_quote_stays_unverified_against_single_emission():
    actual = manifest()
    result = actual["results"][0]
    quotes = ["Mice α.", "Mice α."]
    call = InvocationV1.model_validate(FIXTURE["invocations"][4]["envelope"])
    checks = json.loads(json.dumps(check_prediction_quotes(call, {"evidence_quotes": quotes})))
    result["quotation_field"]["value"] = quotes
    result["quote_checks"] = checks
    result["quote_checks_sha256"] = digest(checks)
    replace_raw_response(actual, chat_response({"evidence_quotes": ["Mice α."]}))
    assert validate_manifest(actual)["quotation_emission"] == "unverified"


def test_unavailable_raw_chat_response_stays_unverified():
    actual = manifest()
    response = ResponseCapture(
        origin="unavailable", raw_availability="unavailable", reason="raw excluded"
    ).model_dump(mode="json")
    actual["invocations"][0]["response_capture"] = response
    actual["invocations"][0]["response_sha256"] = digest(response)
    assert validate_manifest(actual)["quotation_emission"] == "unverified"


def test_chat_shaped_response_cannot_verify_scorer_emission():
    actual = manifest()
    source = SourceSnapshotV1.model_validate(FIXTURE["snapshot"])
    values = copy.deepcopy(FIXTURE["invocations"][0]["envelope"])
    for key in ("project_id", "article_id", "source_snapshot_id", "request_sha256"):
        values.pop(key)
    values.update(
        call_kind="onnx",
        search_id=actual["results"][0]["search_id"],
        quotation_producer="not_produced",
        source_view_ids=[],
        input_spans=[],
        request={
            "feeds": {"features": {"shape": [1], "dtype": "int64", "values": [1]}},
            "output_names": None,
        },
    )
    call = prepare_invocation(source, **values)
    replace_envelope(actual["invocations"][0], call.model_dump(mode="json"))
    replace_raw_response(actual, chat_response({"evidence_quotes": ["Mice α."]}))
    item = actual["results"][0]
    item.update(
        invocation_ref=str(call.id),
        quote_state="not_produced",
        quotation_field=None,
        quote_checks=None,
        quote_checks_sha256=None,
    )
    assert validate_manifest(actual)["quotation_emission_by_result"][item["id"]] == "unverified"


def test_emitted_quote_cannot_verify_cross_scope_result():
    actual = manifest()
    replace_raw_response(actual, chat_response({"evidence_quotes": ["Mice α."]}))
    actual["results"][0]["article_id"] = "00000000-0000-0000-0000-0000000000ff"
    with pytest.raises(ReplayError, match="scope mismatch"):
        validate_manifest(actual)


def test_emitted_quote_cannot_verify_cross_search_result():
    actual = manifest()
    replace_raw_response(actual, chat_response({"evidence_quotes": ["Mice α."]}))
    actual["results"][0]["search_id"] = "00000000-0000-0000-0000-0000000000ff"
    with pytest.raises(ReplayError, match="search scope mismatch"):
        validate_manifest(actual)


@pytest.mark.parametrize("search_id", [None, "absent"])
def test_emission_requires_explicit_nonnull_result_search(search_id):
    actual = manifest()
    replace_raw_response(actual, chat_response({"evidence_quotes": ["Mice α."]}))
    if search_id == "absent":
        actual["results"][0].pop("search_id")
    else:
        actual["results"][0]["search_id"] = None
    assert validate_manifest(actual)["quotation_emission"] == "unverified"


def test_aggregate_stays_unverified_when_another_result_is_legacy():
    actual = manifest()
    replace_raw_response(actual, chat_response({"evidence_quotes": ["Mice α."]}))
    legacy = copy.deepcopy(actual["results"][0])
    legacy.update(
        id="00000000-0000-0000-0000-000000000098",
        capture_state="legacy_unversioned",
        source_snapshot_ref=None,
        invocation_ref=None,
        reason="historical result has no retained invocation",
        quote_state="legacy_unversioned",
        quotation_field=None,
        quote_checks=None,
        quote_checks_sha256=None,
    )
    actual["results"].append(legacy)
    result = validate_manifest(actual)
    assert result["quotation_emission_by_result"] == {
        actual["results"][0]["id"]: "verified",
        legacy["id"]: "unverified",
    }
    assert result["quotation_emission"] == "unverified"
