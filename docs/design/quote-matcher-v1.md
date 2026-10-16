# Captured article-input quotation matcher v1

Pure foundation for [SEIP issue #3](https://github.com/dhuzard/diggypaper/issues/3),
based on `9e5859c1d00921fb19f5c61146deeb39977b2df9`. This implements deterministic
matching only; task/API persistence, rendering, source capture and scientific
support assessment remain separate integration work. No legacy prediction is
bound to current article text by this module.

## Input and output

`app.quote_matcher.ArticleQuoteMatcher` takes a frozen `TargetArticleInput` with
an invocation/input reference and a tuple of `ArticleInputSpan` records. Each
span contains exact *effective* target-article text, its string `payload_path`,
`input_start`, `snapshot_ref`, `view_ref`, `view_start`, `component_ref` and
`component_start`. Source/view/component identities
are opaque references, not content hashes. Identical text from different source
identities stays distinct. Construct spans during request rendering from retained
source views, after effective truncation; never recover attribution by searching
the completed prompt. Do not supply few-shot examples, instructions, synthetic
headings or unused article components. The matcher trusts caller-supplied origin
metadata; it does not establish its authenticity or fetch a source.

Each span maps contiguous input text to the same exact retained-view and
underlying origin-component text. A span must not cross component boundaries or
synthetic headings.
Offsets are zero-based, half-open Unicode code points. Input offsets address the
string at `payload_path`; view offsets address the retained view; component
offsets address the immutable source component. A producer that
edits, strips or tokenizes input must split its origin mapping into accurately
mapped contiguous spans. Overlapping spans within one payload string are rejected.
Matches may overlap within a span. Matches never bridge separate origin spans;
this conservative boundary also excludes passages crossing synthetic headings.

```python
from dataclasses import asdict
from app.quote_matcher import ArticleInputSpan, ArticleQuoteMatcher, TargetArticleInput

captured = TargetArticleInput("invocation-1", (
    ArticleInputSpan(
        text="😀 cafe\u0301 sample",
        payload_path="messages/1/content",
        input_start=40,
        snapshot_ref="snapshot-1",
        view_ref="abstract",
        view_start=0,
        component_ref="abstract",
        component_start=0,
    ),
))
matcher = ArticleQuoteMatcher(captured, allow_normalized=True)
result = asdict(matcher.match("café sample"))
# status='normalized', input span [42,54), retained-view span [2,14).
```

`QuoteMatch` retains the original quote, `input_ref`, `algorithm_version`,
`normalization_version`, `status`, `match_mode`, bounded `candidates`,
`candidates_truncated`, `input_occurrence_count`,
`distinct_source_location_count`, `counts_complete`,
`unique_input_occurrence`, `unique_source_location`, and optional `reason`.
Dataclasses can be serialized with `dataclasses.asdict`; callers own safe
serialization of arbitrary non-string invalid objects. This module neither
adds support/adjudication fields nor interprets an answer. For example, finding
`reduce injury` inside `did not reduce injury` proves occurrence only.

| Status | Meaning |
|---|---|
| `exact` | At least one exact occurrence, with no observed distinct-source ambiguity. |
| `normalized` | No exact occurrence; at least one occurrence after the enabled normalization, with no observed distinct-source ambiguity. |
| `ambiguous` | At least two observed distinct source locations; `match_mode` retains exact/normalized provenance. |
| `not_found` | The enabled pass(es) found no valid occurrence in any supplied target span. |
| `invalid` | Non-string, empty/whitespace-only, or over the configured code-point limit. |
| `source_unavailable` | A valid quote was checked with no captured source input (`source=None`). |
| `not_checked` | Caller explicitly passed `checked=False`, including for legacy quotations. |

An empty span tuple means a known input supplied no target text, so a valid
quote returns `not_found`. Use `checked=False` for historical/unversioned rows;
never fill in their source from mutable current article text. `checked=False`
takes precedence over quote validation and does not claim an attempted check.
Scorers that never produce quotations should have an outer producer state of
`not_produced`; do not create empty quotation checks for them.

## Ambiguity and caps

A source location is `(snapshot_ref, component_ref, component_start, component_end)`. Repeating
one passage at multiple prompt locations preserves every input occurrence but
has one distinct source location, even when the passage appears at different
offsets in different retained views. Repetition at different underlying component
offsets or in different components is source-location ambiguous. The capture
adapter must provide canonical component identity; matching text alone never
deduplicates locations or establishes component identity.

The default candidate cap is 32; the default maximum quote is 16,384 code points.
Search stops after observing one additional occurrence beyond the cap. Only the
capped candidates are retained, while counts include the overflow witness.
`candidates_truncated=True` and `counts_complete=False` mean both counts are
lower bounds. Uniqueness flags are `False` after two relevant observations,
`True` only after a complete one-location search, and `None` when unknown or
inapplicable. A capped `exact`/`normalized` result with
`unique_source_location=None` asserts existence only. Never infer uniqueness
from status alone, candidate count, or a lower-bound source count of one.
Non-search outcomes have `counts_complete=False`.

## Normalization and offsets

Exact matching is case-sensitive and always runs first. If any exact match
exists, normalized alternatives are not searched or added. Optional normalized
matching is off by default. Its only transformations, in order, are:

1. Unicode NFC using the runtime Unicode database.
2. Collapse each run of Unicode whitespace (`str.isspace` / Python Unicode `\s`)
   to one ASCII space, including leading and trailing runs without trimming.

Case, punctuation, hyphens, PDF line-break artifacts and semantic paraphrases
are not repaired. `normalization_version` includes the Unicode database version
for reproducibility. Human review of normalization semantics is still required
before enabling this mode in an integration.

NFC maps follow canonical decomposition, stable combining-class ordering and
composition, including Hangul. Original code-point ranges travel with decomposed
characters and merge on composition. Whitespace collapse maps to the entire
original run. Because combining marks can reorder, a mapped covering range is
accepted only if normalizing its exact original retained slice reproduces the
normalized quote. A normalized substring that cannot be represented by a valid
contiguous original slice is rejected. No candidate receives normalized status
from matching a transformed string alone.

Normalization is cached once per frozen span/matcher and allocated lazily. Reuse
one matcher for a batch, then release it to free the cache. Maps use unsigned
64-bit arrays. A non-NFC input requires canonical processing proportional to its
length, plus sorting within combining-mark runs; candidate storage stays bounded.
The matcher does not impose a source-size limit; the capture boundary should
apply its own input limits.

Do not use these offsets directly as JavaScript string indices. Convert a
code-point offset `n` with `Array.from(text).slice(0, n).join('').length` for
UTF-16 indexing (or a linear precomputed boundary table for many ranges). For
`😀 sample`, code-point range `[2,8)` is UTF-16 range `[3,9)`. The exact retained
view text must be used for conversion; this backend foundation adds no frontend
adapter.

## Reproduction

From `backend`, with Python 3.12+ and pytest/pytest-asyncio/Ruff available:

```powershell
python -m pytest tests/unit/test_quote_matcher.py --confcutdir=tests/unit -q
ruff check app/quote_matcher.py tests/unit/test_quote_matcher.py scripts/benchmark_quote_matcher.py
ruff format --check app/quote_matcher.py tests/unit/test_quote_matcher.py scripts/benchmark_quote_matcher.py
python -m scripts.benchmark_quote_matcher
```

`--confcutdir` isolates these pure tests from the application's database/API
conftest. They also run normally as part of the complete backend suite. Fixtures
cover example exclusion, frozen old input after current-source changes,
abstract-only input, negation, exact-first behavior, invalid values, overlaps,
caps, repeated insertions/source locations, emoji, NFC/NFD, Hangul, canonical
reordering, whitespace and normalization slice round trips. Seeded differential
checks compare the custom offset map's text with Python's normalization over
representative and adversarial sequences.

The benchmark runs 100 quotes at exactly 100,000 and 1,000,000 UTF-8 bytes for
exact, whitespace-normalized, Unicode-normalized and adversarial repeated input.
Three uninstrumented runs provide median cold-batch and warm-batch latency.
Cold batches include lazy map construction; warm batches reuse maps. A separate
`tracemalloc` run reports peak Python allocations, excluding prebuilt source and
quotes. This is not process RSS or an end-to-end service/load benchmark.

Measured 2026-10-16 on Windows 11 build 26200, Python 3.12.6, pytest 9.1.1,
pytest-asyncio 1.4.0 and Ruff 0.17.1 in an isolated environment. All 48 focused
tests and the scoped Ruff check/format check passed. The application suite was
not run for this isolated foundation. Raw benchmark output is retained in
[`quote-matcher-v1-windows-python312.jsonl`](../benchmarks/quote-matcher-v1-windows-python312.jsonl).

| UTF-8 input bytes | Mode | Cold median (ms) | Warm median (ms) | Peak Python allocations (MiB) |
|---:|---|---:|---:|---:|
| 100,000 | exact | 6.639 | 6.546 | 0.057 |
| 100,000 | whitespace normalized | 107.912 | 14.677 | 4.001 |
| 100,000 | NFC normalized | 110.435 | 15.548 | 4.098 |
| 100,000 | repeated characters | 25.656 | 26.435 | 0.518 |
| 1,000,000 | exact | 50.233 | 45.284 | 0.057 |
| 1,000,000 | whitespace normalized | 1,188.570 | 83.875 | 40.146 |
| 1,000,000 | NFC normalized | 1,350.249 | 105.238 | 41.101 |
| 1,000,000 | repeated characters | 30.630 | 33.146 | 0.517 |

Each row processes 100 quotes. Exact/normalized fixture rows produced 100 of the
expected status, with one retained candidate per quote; repeated-character rows
produced 100 ambiguous results with at most 32 retained candidates. Cold
normalization remains materially more expensive than exact matching; the cache
and bounded lifetime are deliberate. These synthetic local measurements establish
behavior and cost, without claiming a production throughput or latency guarantee.
