"""Pure tests; runnable without the application/database fixtures."""

import json
import random
import unicodedata
from dataclasses import asdict
from pathlib import Path

import pytest

from app.quote_matcher import (
    ArticleInputSpan,
    ArticleQuoteMatcher,
    TargetArticleInput,
    _nfc_with_offsets,
    _normalize_with_offsets,
    normalize_quote_text,
)

FIXTURES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "quote_matcher_v1.json").read_text(encoding="utf-8")
)


def span(
    text,
    *,
    path="messages/1/content",
    input_start=0,
    view="abstract",
    view_start=0,
    component=None,
    component_start=None,
):
    return ArticleInputSpan(
        text,
        path,
        input_start,
        "snapshot-A",
        view,
        view_start,
        component if component is not None else view,
        component_start if component_start is not None else view_start,
    )


def source(text):
    return TargetArticleInput("invocation-A", (span(text),))


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture["name"])
def test_contract_fixtures(fixture):
    result = ArticleQuoteMatcher(
        source(fixture["text"]), allow_normalized=fixture["normalized"]
    ).match(fixture["quote"])
    assert result.status == fixture["status"]
    if "start" in fixture:
        candidate = result.candidates[0]
        assert (candidate.view_start, candidate.view_end) == (fixture["start"], fixture["end"])
        assert (candidate.input_start, candidate.input_end) == (fixture["start"], fixture["end"])
        retained_slice = fixture["text"][candidate.view_start : candidate.view_end]
        if result.match_mode == "exact":
            assert retained_slice == fixture["quote"]
        else:
            assert normalize_quote_text(retained_slice) == normalize_quote_text(fixture["quote"])
    assert result.input_occurrence_count == fixture.get("count", int(bool(result.candidates)))
    assert "support" not in asdict(result)
    assert "adjudication" not in asdict(result)


@pytest.mark.parametrize("quote", [None, 42, False, [], {}, b"quote", "", " \t\n"])
def test_invalid_quote(quote):
    result = ArticleQuoteMatcher(source("quote")).match(quote)
    assert result.status == "invalid"
    assert result.quote is quote


def test_oversized_quote_is_invalid_before_source_availability():
    result = ArticleQuoteMatcher(None, max_quote_codepoints=3).match("😀😀😀😀")
    assert result.status == "invalid"
    assert result.reason == "quote_too_long"


def test_unavailable_and_known_empty_and_legacy_are_distinct():
    assert ArticleQuoteMatcher(None).match("x").status == "source_unavailable"
    assert ArticleQuoteMatcher(TargetArticleInput("empty", ())).match("x").status == "not_found"
    legacy = ArticleQuoteMatcher(None).match("x", checked=False)
    assert legacy.status == "not_checked"
    assert legacy.input_ref is None


def test_example_and_instruction_text_are_not_search_inputs():
    payload = "Instruction: report verbatim. Example: fabricated quote. Article: real quote."
    start = payload.index("real quote")
    captured = TargetArticleInput("invocation-A", (span("real quote", input_start=start),))
    matcher = ArticleQuoteMatcher(captured, allow_normalized=True)
    assert matcher.match("fabricated quote").status == "not_found"
    assert matcher.match("report verbatim").status == "not_found"
    actual = matcher.match("real quote")
    assert actual.candidates[0].input_start == start
    assert actual.candidates[0].view_start == 0


def test_only_frozen_source_is_used_after_mutable_article_changes():
    current_article = {"abstract": "old captured quote"}
    frozen = source(current_article["abstract"])
    current_article["abstract"] = "new current quote"
    matcher = ArticleQuoteMatcher(frozen)
    assert matcher.match("old captured quote").status == "exact"
    assert matcher.match("new current quote").status == "not_found"


def test_repeated_insertion_is_one_source_location_and_multiple_input_occurrences():
    captured = TargetArticleInput(
        "invocation-A",
        (
            span("same passage", input_start=10, view_start=100),
            span("same passage", input_start=50, view_start=100),
        ),
    )
    result = ArticleQuoteMatcher(captured).match("passage")
    assert result.status == "exact"
    assert result.input_occurrence_count == 2
    assert result.distinct_source_location_count == 1
    assert result.unique_input_occurrence is False
    assert result.unique_source_location is True
    assert [candidate.input_start for candidate in result.candidates] == [15, 55]
    assert [candidate.view_start for candidate in result.candidates] == [105, 105]


def test_identical_text_in_distinct_views_is_ambiguous():
    captured = TargetArticleInput(
        "invocation-A",
        (
            span("same", path="messages/0/content", view="title"),
            span("same", path="messages/1/content", view="abstract"),
        ),
    )
    result = ArticleQuoteMatcher(captured).match("same")
    assert result.status == "ambiguous"
    assert result.match_mode == "exact"
    assert result.distinct_source_location_count == 2
    assert result.unique_source_location is False


def test_same_component_passage_in_two_views_is_one_document_location():
    captured = TargetArticleInput(
        "invocation-A",
        (
            span(
                "same passage",
                path="messages/0/content",
                view="abstract-only",
                view_start=0,
                component="abstract",
                component_start=0,
            ),
            span(
                "same passage",
                path="messages/1/content",
                view="full-article",
                view_start=600,
                component="abstract",
                component_start=0,
            ),
        ),
    )
    result = ArticleQuoteMatcher(captured).match("passage")
    assert result.status == "exact"
    assert result.input_occurrence_count == 2
    assert result.distinct_source_location_count == 1
    assert result.unique_source_location is True
    assert result.unique_input_occurrence is False
    assert [candidate.view_start for candidate in result.candidates] == [5, 605]
    assert [candidate.component_start for candidate in result.candidates] == [5, 5]


def test_repeated_component_text_at_different_component_offsets_is_ambiguous():
    captured = TargetArticleInput(
        "invocation-A",
        (
            span("same", input_start=0, component="abstract", component_start=10),
            span("same", input_start=20, component="abstract", component_start=80),
        ),
    )
    assert ArticleQuoteMatcher(captured).match("same").status == "ambiguous"


def test_exact_pass_wins_without_normalized_candidates():
    result = ArticleQuoteMatcher(source("café cafe\u0301"), allow_normalized=True).match("café")
    assert result.status == "exact"
    assert result.input_occurrence_count == 1
    assert result.normalization_version is None


def test_normalized_repetition_is_ambiguous_with_original_offsets():
    result = ArticleQuoteMatcher(source("cafe\u0301 cafe\u0301"), allow_normalized=True).match(
        "café"
    )
    assert result.status == "ambiguous"
    assert result.match_mode == "normalized"
    assert [(item.view_start, item.view_end) for item in result.candidates] == [(0, 5), (6, 11)]


def test_candidate_expansion_is_bounded_for_adversarial_repetitions():
    result = ArticleQuoteMatcher(source("a" * 1_000_000), max_candidates=3).match("aa")
    assert len(result.candidates) == 3
    assert result.status == "ambiguous"
    assert result.input_occurrence_count == 4  # Includes the overflow witness.
    assert result.distinct_source_location_count == 4
    assert result.candidates_truncated is True
    assert result.counts_complete is False


def test_truncation_cannot_claim_unique_source_location():
    captured = TargetArticleInput(
        "invocation-A",
        (
            span("x", input_start=0),
            span("x", input_start=2),
            span("x", input_start=4, view="other-view"),
        ),
    )
    result = ArticleQuoteMatcher(captured, max_candidates=1).match("x")
    assert result.status == "exact"  # Observed existence, with uniqueness unknown.
    assert result.distinct_source_location_count == 1
    assert result.input_occurrence_count == 2
    assert result.counts_complete is False
    assert result.unique_source_location is None
    assert result.unique_input_occurrence is False


def test_exactly_the_cap_does_not_mark_truncation():
    result = ArticleQuoteMatcher(source("xxx"), max_candidates=3).match("x")
    assert len(result.candidates) == 3
    assert result.counts_complete is True
    assert result.candidates_truncated is False


def test_matching_does_not_bridge_disjoint_origin_spans():
    captured = TargetArticleInput(
        "invocation-A",
        (
            span("alpha", input_start=0),
            span("beta", input_start=6, view_start=20),
        ),
    )
    assert ArticleQuoteMatcher(captured).match("alpha beta").status == "not_found"


def test_nfc_offset_composition_agrees_with_unicode_database():
    rng = random.Random(319)
    alphabet = list("abc😀 \t") + [chr(value) for value in range(0x300, 0x370)]
    alphabet += list("\u1100\u1161\u11a8\u0344\u0958\u0f73\u212b")
    for _ in range(2_000):
        text = "".join(rng.choices(alphabet, k=rng.randrange(1, 40)))
        mapped = _nfc_with_offsets(text)
        assert mapped.text == unicodedata.normalize("NFC", text)
        assert len(mapped.starts) == len(mapped.ends) == len(mapped.text)
        assert all(
            0 <= start < end <= len(text)
            for start, end in zip(mapped.starts, mapped.ends, strict=True)
        )


def test_normalized_offset_map_differential_and_candidate_round_trip():
    rng = random.Random(977)
    alphabet = list("abc😀 \t\r\n\u00a0\u2028")
    alphabet += list("\u1100\u1161\u11a8\u0344\u0958\u0f73\u212b")
    alphabet += [chr(value) for value in range(0x300, 0x370)]
    texts = [
        "",
        "a\u0315\u0300\t\n각",
        "\u1100\u1161\u11a8",
        "\u0344\u0344",
        "a" + "\u0315\u0300\u0327" * 1_000,
        "\u1100\u1161\u11a8" * 1_000,
        "\t\r\n\u00a0\u2028 " * 1_000,
        "😀 cafe\u0301\t\r\n beta",
        "😀 café beta",
    ]
    texts.extend("".join(rng.choices(alphabet, k=rng.randrange(1, 60))) for _ in range(1_000))
    for text in texts:
        mapped = _normalize_with_offsets(text)
        assert mapped.text == normalize_quote_text(text)
        assert len(mapped.starts) == len(mapped.ends) == len(mapped.text)
        assert all(
            0 <= start < end <= len(text)
            for start, end in zip(mapped.starts, mapped.ends, strict=True)
        )
        if not mapped.text:
            continue
        start = rng.randrange(len(mapped.text))
        end = rng.randrange(start + 1, len(mapped.text) + 1)
        quote = mapped.text[start:end]
        captured = TargetArticleInput("differential", (span(text, input_start=7, view_start=13),))
        result = ArticleQuoteMatcher(captured, allow_normalized=True).match(quote)
        for candidate in result.candidates:
            assert candidate.input_start - 7 == candidate.view_start - 13
            assert candidate.input_end - 7 == candidate.view_end - 13
            original_slice = text[candidate.view_start - 13 : candidate.view_end - 13]
            assert normalize_quote_text(original_slice) == normalize_quote_text(quote)


@pytest.mark.parametrize(
    "name,value",
    [
        ("max_candidates", 0),
        ("max_candidates", True),
        ("max_quote_codepoints", -1),
    ],
)
def test_invalid_configuration(name, value):
    with pytest.raises(ValueError):
        ArticleQuoteMatcher(None, **{name: value})


def test_invalid_origin_maps_are_rejected():
    with pytest.raises(ValueError, match="overlap"):
        TargetArticleInput("invocation-A", (span("abc"), span("abc", input_start=2)))
    with pytest.raises(ValueError, match="tuple"):
        TargetArticleInput("invocation-A", [span("abc")])
    with pytest.raises(ValueError, match="code-point"):
        span("abc", view_start=-1)


def test_lazy_normalization_cache_is_reused():
    matcher = ArticleQuoteMatcher(source("alpha\t beta cafe\u0301"), allow_normalized=True)
    matcher.match("alpha beta")
    cached = matcher._normalized[0]
    matcher.match("café")
    assert matcher._normalized[0] is cached
