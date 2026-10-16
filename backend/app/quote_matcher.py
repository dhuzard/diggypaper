"""Pure, bounded quotation matching against explicitly captured article input.

This module proves string occurrence only. It makes no scientific-support,
answer-agreement, or adjudication decision and never reads mutable article data.
"""

import re
import unicodedata
from array import array
from dataclasses import dataclass
from typing import Literal

ALGORITHM_VERSION = "article-input-quote-v1.1"
NORMALIZATION_VERSION = f"nfc-whitespace-v1/ucd-{unicodedata.unidata_version}"
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
MatchMode = Literal["exact", "normalized"]
ResourceLimitKind = Literal["candidate_examinations", "validation_codepoints"]
_WHITESPACE = re.compile(r"\s+")
_ASCII_RUNS = re.compile(r"[\x00-\x7f]+|[^\x00-\x7f]+")


@dataclass(frozen=True)
class ArticleInputSpan:
    """One contiguous target-article span captured while rendering an invocation.

    ``text`` must be the *effective* supplied text, including any truncation.
    Offsets address one origin component, its retained view and the string at
    ``payload_path``. Each span must map contiguously to that same component. The
    caller supplies origin metadata; this module cannot authenticate its truth.
    Examples, instructions and synthetic headings must not be passed here.
    """

    text: str
    payload_path: str
    input_start: int
    snapshot_ref: str
    view_ref: str
    view_start: int
    component_ref: str
    component_start: int

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("article span text must be a string")
        for name in ("payload_path", "snapshot_ref", "view_ref", "component_ref"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be a nonempty string")
        for name in ("input_start", "view_start", "component_start"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative code-point offset")


@dataclass(frozen=True)
class TargetArticleInput:
    """Frozen invocation reference and only its known target-article spans.

    An empty tuple means a known input contained no article text; ``None`` at
    the matching boundary means the captured input is unavailable instead.
    """

    input_ref: str
    spans: tuple[ArticleInputSpan, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.input_ref, str) or not self.input_ref:
            raise ValueError("input_ref must be a nonempty string")
        if not isinstance(self.spans, tuple) or any(
            not isinstance(span, ArticleInputSpan) for span in self.spans
        ):
            raise ValueError("spans must be a tuple of ArticleInputSpan")
        # Overlapping origin assertions could manufacture duplicate occurrences.
        ordered = sorted(self.spans, key=lambda span: (span.payload_path, span.input_start))
        for left, right in zip(ordered, ordered[1:], strict=False):
            if (
                left.payload_path == right.payload_path
                and left.input_start + len(left.text) > right.input_start
            ):
                raise ValueError("article spans in one payload string must not overlap")


@dataclass(frozen=True)
class QuoteCandidate:
    """Zero-based half-open Unicode code-point offsets, never UTF-16 units."""

    payload_path: str
    input_start: int
    input_end: int
    snapshot_ref: str
    view_ref: str
    view_start: int
    view_end: int
    component_ref: str
    component_start: int
    component_end: int

    @property
    def source_location(self) -> tuple[str, str, int, int]:
        return self.snapshot_ref, self.component_ref, self.component_start, self.component_end


@dataclass(frozen=True)
class QuoteMatch:
    """Occurrence evidence with explicit search completeness.

    ``candidates_truncated`` covers both the accepted-candidate cap and a work
    limit, including a stop with zero accepted candidates. Inspect ``status``,
    ``counts_complete`` and ``resource_limit_kind`` to interpret that flag.
    """

    quote: object
    status: MatchStatus
    input_ref: str | None
    candidates: tuple[QuoteCandidate, ...] = ()
    match_mode: MatchMode | None = None
    algorithm_version: str = ALGORITHM_VERSION
    normalization_version: str | None = None
    candidates_truncated: bool = False
    input_occurrence_count: int = 0
    distinct_source_location_count: int = 0
    counts_complete: bool = False
    unique_input_occurrence: bool | None = None
    unique_source_location: bool | None = None
    reason: str | None = None
    resource_limit_kind: ResourceLimitKind | None = None
    candidate_examinations: int = 0
    validation_codepoints: int = 0
    candidate_examination_limit: int | None = None
    validation_codepoint_limit: int | None = None


def normalize_quote_text(text: str) -> str:
    """NFC, then replace each Unicode whitespace run with one ASCII space.

    No trimming, case-folding, punctuation repair, hyphen repair or fuzzy match.
    """
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text))


@dataclass
class _MappedText:
    text: str
    starts: array
    ends: array


def _nfc_with_offsets(text: str) -> _MappedText:
    if unicodedata.is_normalized("NFC", text):
        return _MappedText(text, array("Q", range(len(text))), array("Q", range(1, len(text) + 1)))

    # Canonical decomposition/order followed by Unicode canonical composition.
    # Provenance moves with decomposed code points; composing merges their ranges.
    # Sorting a combining run avoids quadratic insertion on adversarial marks.
    chars: list[str] = []
    starts = array("Q")
    ends = array("Q")
    starter: int | None = None
    last_class = 0

    def emit(char: str, start: int, end: int) -> None:
        nonlocal starter, last_class
        combining_class = unicodedata.combining(char)
        if starter is not None and (last_class < combining_class or last_class == 0):
            joined = unicodedata.normalize("NFC", chars[starter] + char)
            if len(joined) == 1:
                chars[starter] = joined
                starts[starter] = min(starts[starter], start)
                ends[starter] = max(ends[starter], end)
                return
        if combining_class == 0:
            starter = len(chars)
        chars.append(char)
        starts.append(start)
        ends.append(end)
        last_class = combining_class

    run: list[tuple[str, int, int]] = []
    for match in _ASCII_RUNS.finditer(text):
        chunk = match.group()
        if chunk.isascii():
            for token in sorted(run, key=lambda item: unicodedata.combining(item[0])):
                emit(*token)
            run.clear()
            # ASCII characters neither decompose nor canonically compose with
            # a previous starter. Keep the last one available for following marks.
            chars.extend(chunk)
            starts.extend(range(match.start(), match.end()))
            ends.extend(range(match.start() + 1, match.end() + 1))
            starter = len(chars) - 1
            last_class = 0
            continue
        for offset, char in enumerate(chunk, match.start()):
            for decomposed in unicodedata.normalize("NFD", char):
                if unicodedata.combining(decomposed) == 0:
                    for token in sorted(run, key=lambda item: unicodedata.combining(item[0])):
                        emit(*token)
                    run.clear()
                    emit(decomposed, offset, offset + 1)
                else:
                    run.append((decomposed, offset, offset + 1))
    for token in sorted(run, key=lambda item: unicodedata.combining(item[0])):
        emit(*token)
    return _MappedText("".join(chars), starts, ends)


def _normalize_with_offsets(text: str) -> _MappedText:
    nfc = _nfc_with_offsets(text)
    collapsed = _WHITESPACE.sub(" ", nfc.text)
    if collapsed == nfc.text:
        return nfc
    chars: list[str] = []
    starts = array("Q")
    ends = array("Q")
    cursor = 0
    for match in _WHITESPACE.finditer(nfc.text):
        chars.extend((nfc.text[cursor : match.start()], " "))
        starts.extend(nfc.starts[cursor : match.start()])
        starts.append(nfc.starts[match.start()])
        ends.extend(nfc.ends[cursor : match.start()])
        ends.append(nfc.ends[match.end() - 1])
        cursor = match.end()
    chars.append(nfc.text[cursor:])
    starts.extend(nfc.starts[cursor:])
    ends.extend(nfc.ends[cursor:])
    return _MappedText("".join(chars), starts, ends)


class ArticleQuoteMatcher:
    """Reusable matcher; lazily caches normalization once for a frozen input.

    Candidate search stops after ``max_candidates + 1`` occurrences. Counts then
    become lower bounds; unknown uniqueness is None. A matched status asserts
    existence, and only ``unique_source_location is True`` asserts uniqueness.
    Each origin span is searched independently, with overlapping matches allowed.
    Every substring hit, including rejected normalized hits, consumes examination
    budget. Per-hit mapping and original-slice normalization consume a separate
    code-point budget. Lazy full-source normalization is outside these budgets.
    """

    def __init__(
        self,
        source: TargetArticleInput | None,
        *,
        allow_normalized: bool = False,
        max_candidates: int = 32,
        max_quote_codepoints: int = 16_384,
        max_candidate_examinations: int = 128,
        max_validation_codepoints: int = 262_144,
    ) -> None:
        if source is not None and not isinstance(source, TargetArticleInput):
            raise ValueError("source must be TargetArticleInput or None")
        for name, value in (
            ("max_candidates", max_candidates),
            ("max_quote_codepoints", max_quote_codepoints),
            ("max_candidate_examinations", max_candidate_examinations),
            ("max_validation_codepoints", max_validation_codepoints),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self._source = source
        self.allow_normalized = allow_normalized
        self.max_candidates = max_candidates
        self.max_quote_codepoints = max_quote_codepoints
        self.max_candidate_examinations = max_candidate_examinations
        self.max_validation_codepoints = max_validation_codepoints
        self._normalized: dict[int, _MappedText] = {}

    @property
    def source(self) -> TargetArticleInput | None:
        return self._source

    def match(self, quote: object, *, checked: bool = True) -> QuoteMatch:
        """Match a quote, or record an explicit unchecked/legacy observation.

        Invalid values are retained as provided, including when checks are off.
        The caller owns serialization/redaction of arbitrary invalid objects.
        """
        input_ref = self.source.input_ref if self.source is not None else None
        if not checked:
            return QuoteMatch(quote, "not_checked", input_ref, reason="check_not_requested")
        if not isinstance(quote, str):
            return QuoteMatch(quote, "invalid", input_ref, reason="quote_not_string")
        if len(quote) > self.max_quote_codepoints:
            return QuoteMatch(quote, "invalid", input_ref, reason="quote_too_long")
        if not quote or quote.isspace():
            return QuoteMatch(quote, "invalid", input_ref, reason="quote_empty")
        if self.source is None:
            return QuoteMatch(
                quote, "source_unavailable", None, reason="captured_input_unavailable"
            )
        exact = self._search(quote, quote, "exact")
        if exact.input_occurrence_count or not self.allow_normalized:
            return exact
        return self._search(quote, normalize_quote_text(quote), "normalized")

    def _search(self, quote: str, needle: str, mode: MatchMode) -> QuoteMatch:
        assert self.source is not None
        found: list[QuoteCandidate] = []
        locations: set[tuple[str, str, int, int]] = set()
        count = 0
        truncated = False
        resource_limit_kind: ResourceLimitKind | None = None
        examinations = 0
        validation_codepoints = 0
        for span_index, span in enumerate(self.source.spans):
            mapped = None
            if mode == "normalized":
                if span_index not in self._normalized:
                    self._normalized[span_index] = _normalize_with_offsets(span.text)
                mapped = self._normalized[span_index]
            haystack = mapped.text if mapped is not None else span.text
            cursor = 0
            while (index := haystack.find(needle, cursor)) >= 0:
                if examinations == self.max_candidate_examinations:
                    resource_limit_kind = "candidate_examinations"
                    break
                examinations += 1
                cursor = index + 1  # Deliberately retain overlapping occurrences.
                end_index = index + len(needle)
                if mapped is None:
                    start, end = index, end_index
                else:
                    # Charge before allocating/scanning map slices. Rejected hits
                    # consume budget too; accepted-candidate caps cannot bound them.
                    mapping_work = 2 * len(needle)
                    if validation_codepoints + mapping_work > self.max_validation_codepoints:
                        resource_limit_kind = "validation_codepoints"
                        break
                    validation_codepoints += mapping_work
                    # Combining marks may reorder. A covering range is valid only
                    # if the retained original slice really normalizes to needle.
                    start = min(mapped.starts[index:end_index])
                    end = max(mapped.ends[index:end_index])
                    slice_work = end - start
                    if validation_codepoints + slice_work > self.max_validation_codepoints:
                        resource_limit_kind = "validation_codepoints"
                        break
                    validation_codepoints += slice_work
                    if normalize_quote_text(span.text[start:end]) != needle:
                        continue
                candidate = QuoteCandidate(
                    span.payload_path,
                    span.input_start + start,
                    span.input_start + end,
                    span.snapshot_ref,
                    span.view_ref,
                    span.view_start + start,
                    span.view_start + end,
                    span.component_ref,
                    span.component_start + start,
                    span.component_start + end,
                )
                count += 1
                locations.add(candidate.source_location)
                if len(found) == self.max_candidates:
                    truncated = True
                    break
                found.append(candidate)
            if truncated or resource_limit_kind is not None:
                break
        incomplete = truncated or resource_limit_kind is not None
        unique_input = False if count > 1 else (None if incomplete or not count else True)
        unique_source = False if len(locations) > 1 else (None if incomplete or not count else True)
        status: MatchStatus = "not_found"
        if count:
            status = "ambiguous" if len(locations) > 1 else mode
        elif resource_limit_kind is not None:
            status = "incomplete"
        return QuoteMatch(
            quote,
            status,
            self.source.input_ref,
            tuple(found),
            match_mode=mode if count else None,
            normalization_version=NORMALIZATION_VERSION if mode == "normalized" else None,
            candidates_truncated=incomplete,
            input_occurrence_count=count,
            distinct_source_location_count=len(locations),
            counts_complete=not incomplete,
            unique_input_occurrence=unique_input,
            unique_source_location=unique_source,
            reason="resource_limit" if resource_limit_kind is not None else None,
            resource_limit_kind=resource_limit_kind,
            candidate_examinations=examinations,
            validation_codepoints=validation_codepoints,
            candidate_examination_limit=self.max_candidate_examinations,
            validation_codepoint_limit=self.max_validation_codepoints,
        )
