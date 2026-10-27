# Captured article-input quotation matcher v1.2

> Status update 2026-10-17: persistence (`app/quote_checks.py`, migration `f0023`), API exposure (`quote_checks` in the article GET) and UI (`QuoteOccurrencePanel.tsx`) are now on `main`. No frontend UTF-16 adapter exists yet. See the [merge audit](../audits/2026-10-17-seip-merge-audit.md).

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
`unique_input_occurrence`, `unique_source_location`, optional `reason`,
`resource_limit_kind`, `candidate_examinations`, `validation_codepoints`,
`candidate_examination_limit`, `validation_codepoint_limit`, `combining_run_limit`,
`normalized_source_codepoints`, `normalized_decomposed_codepoints`,
`normalized_source_codepoint_limit` and `normalized_decomposed_codepoint_limit`.
Dataclasses can be serialized with `dataclasses.asdict`; callers own safe
serialization of arbitrary non-string invalid objects. This module neither
adds support/adjudication fields nor interprets an answer. For example, finding
`reduce injury` inside `did not reduce injury` proves occurrence only.

| Status | Meaning |
|---|---|
| `exact` | At least one exact occurrence, with no observed distinct-source ambiguity. |
| `normalized` | No exact occurrence; at least one occurrence after the enabled normalization, with no observed distinct-source ambiguity. |
| `ambiguous` | At least two observed distinct source locations; `match_mode` retains exact/normalized provenance. |
| `not_found` | The enabled pass(es) completed and found no valid occurrence in any supplied target span. |
| `invalid` | Non-string, empty/whitespace-only, or over the configured code-point limit. |
| `source_unavailable` | A valid quote was checked with no captured source input (`source=None`). |
| `not_checked` | Caller explicitly passed `checked=False`, including for legacy quotations. |
| `incomplete` | A search was attempted but a work limit stopped it before any valid occurrence was established; this is not a negative match result. |

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
The default examination limit is 128 hits per pass, and the default normalized
validation-work limit is 262,144 code-point units per pass. Both budgets are
shared across all spans searched by that pass. Every substring hit consumes one
examination, including hits rejected by original-slice validation. A normalized
hit is charged twice its normalized needle length before scanning/copying the
two offset maps, then its covering original-slice length before copying and
normalizing that slice. Work that would exceed the configured budget is not
performed. Counters and configured limits are retained in each search result.
Source normalization/map construction has separate cumulative admission budgets
of 1,100,000 original and 2,200,000 canonically decomposed code points per matcher.
The original size is checked before decomposition, and decomposed size before
map construction. Repeated spans each consume budget because each receives its
own map. Cached maps are charged once. Empty spans are skipped. Counters report
the admitted cache, including maps retained from previous quotes on that matcher.
One ordinary retained 1 MB view fits these defaults; multiple large views can
produce incomplete results. Exact matching does not allocate maps and remains
available even when normalized admission is refused.

Before whole-string NFC or map construction, a linear preflight also limits
contiguous canonically decomposed combining-mark runs to 256. It uses NFD only
on individual code points and does not sort a whole run. Counting decomposed
marks prevents composition-excluded characters such as U+0344 from hiding
expansion. The quote is admitted before its whole-string NFC call, and each
source span is admitted before normalization/map allocation. Every contiguous
candidate slice inherits its admitted source's combining-run bound, so native
NFC canonical reordering cannot receive an unadmitted long run. This addresses
the quadratic native-NFC case `a + U+0315*50000 + U+0300*8001`. The standalone
`normalize_quote_text` transformation helper does not itself apply admission;
untrusted matching uses `ArticleQuoteMatcher`.

The accepted-candidate cap stops search after observing one additional valid
occurrence beyond the cap. Only the capped candidates are retained, while counts
include the overflow witness.
`candidates_truncated=True` and `counts_complete=False` mean both counts are
lower bounds. `candidates_truncated=True` also means a work limit stopped the
search, including when zero candidates were accepted. It does not guarantee
that omitted valid candidates exist. `reason='resource_limit'` and
`resource_limit_kind`
distinguish a work-limit stop from an accepted-candidate cap. Downstream
integration must inspect `counts_complete` and `status`, not infer completeness
from candidate count or the truncation flag alone.

| Resource limit kind | Refused work |
|---|---|
| `candidate_examinations` | Additional substring-hit examination. |
| `validation_codepoints` | Additional per-hit map scans or original-slice normalization. |
| `quote_combining_run` | Whole-string NFC of an unadmitted quote. |
| `source_combining_run` | Normalization/map construction for a span with an unadmitted run. |
| `normalized_source_codepoints` | Map allocation exceeding cumulative original-text admission. |
| `normalized_decomposed_codepoints` | Map allocation exceeding cumulative decomposition admission. |

With zero accepted hits, a work-limit stop returns `incomplete`; it never returns
`not_found` or the caller-skipped/legacy state `not_checked`. Accepted hits retain
`exact`, `normalized` or `ambiguous` existence status, plus the resource-limit
metadata and incomplete counts. Uniqueness flags are `False` after two relevant
observations,
`True` only after a complete one-location search, and `None` when unknown or
inapplicable. A capped `exact`/`normalized` result with
`unique_source_location=None` asserts existence only. Never infer uniqueness
from status alone, candidate count, or a lower-bound source count of one.
Non-search outcomes have `counts_complete=False`.

The algorithm version is `article-input-quote-v1.2`. Version 1.1 added incomplete
search outcomes and per-hit accounting; v1.2 additionally gates combining runs
and cumulative map admission. Limits are configurable and retained in results;
raising them changes the resource policy. These deterministic admission/work
units are not a wall-clock deadline or an exact RSS limit. Map admission bounds
allocation before construction, while the benchmark reports actual Python peaks
for the default policy.

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
The matcher applies the cumulative normalized-map admission policy above;
the capture boundary may additionally limit total invocation input size.

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

It additionally runs the rejected-normalized-hit review reproduction at 20,000,
100,000 and 1,000,000 UTF-8 bytes: repeated `U+0344` source characters with a
quote made from repeated `U+0308 U+0301` pairs plus a final `U+0308`, with an
accepted-candidate cap of one. Every matching transformed substring has an
invalid original covering slice. The 20 KB case includes the exact 500-pair
review quote plus 99 nearby quote lengths. Version 1.2 refuses these quotes at
the combining-run gate before transformed-hit validation or map allocation.
Additional rows cover the 116,003-byte native-NFC reordering reproduction with
both a long quote and a short quote exposing the source gate, ten repeated 1 MB
spans, and one admitted near-budget source. The last fixture has exactly
1,100,000 original and 2,191,406 decomposed code points, with at most 254
consecutive decomposed marks per run. `--resource-limits-only` runs the resource
and admission rows without ordinary exact/normalized rows. Results report map
counts, admitted original/decomposed totals, resource-limit kinds, examined hits,
validation work and incomplete outcomes alongside time and allocations.

Version 1.2 was measured 2026-10-16 on Windows 11 build 26200, Python 3.12.6,
pytest 9.1.1, pytest-asyncio 1.4.0 and Ruff 0.17.1 in an isolated environment.
All 72 focused tests and the scoped Ruff check/format check passed. The
application suite was not run for this isolated foundation. Raw v1.2 benchmark
output is retained in
[`quote-matcher-v1.2-windows-python312.jsonl`](../benchmarks/quote-matcher-v1.2-windows-python312.jsonl).
The superseded v1.1 measurements remain in
[`quote-matcher-v1.1-windows-python312.jsonl`](../benchmarks/quote-matcher-v1.1-windows-python312.jsonl),
and the original baseline in
[`quote-matcher-v1-windows-python312.jsonl`](../benchmarks/quote-matcher-v1-windows-python312.jsonl).

| UTF-8 input bytes | Mode | Cold median (ms) | Warm median (ms) | Peak Python allocations (MiB) |
|---:|---|---:|---:|---:|
| 100,000 | exact | 7.227 | 6.987 | 0.066 |
| 100,000 | whitespace normalized | 96.348 | 16.395 | 4.002 |
| 100,000 | NFC normalized | 104.477 | 16.130 | 4.099 |
| 100,000 | repeated characters | 29.171 | 30.093 | 0.527 |
| 1,000,000 | exact | 59.227 | 55.195 | 0.066 |
| 1,000,000 | whitespace normalized | 1,374.068 | 89.498 | 40.146 |
| 1,000,000 | NFC normalized | 1,242.123 | 112.341 | 41.102 |
| 1,000,000 | repeated characters | 18.700 | 22.202 | 0.527 |
| 20,000 | rejected normalized quote | 24.080 | 22.479 | 0.033 |
| 100,000 | rejected normalized quote | 25.929 | 28.648 | 0.033 |
| 1,000,000 | rejected normalized quote | 24.943 | 27.133 | 0.033 |
| 116,003 | reordering quote refused | 47.204 | 45.727 | 0.033 |
| 116,003 | reordering source refused | 117.023 | 187.140 | 0.145 |
| 10,000,000 | ten repeated 1 MB spans | 2,106.540 | 598.789 | 41.102 |
| 2,191,406 | admitted near-budget source | 7,644.754 | 372.445 | 181.310 |

Each row processes 100 quotes. Exact/normalized fixture rows produced 100 of the
expected status, with one retained candidate per quote; repeated-character rows
produced 100 ambiguous results with at most 32 retained candidates. Rejected
quotes and sources returned 100 `incomplete` results with `reason='resource_limit'`,
unknown uniqueness and zero maps allocated. Ten repeated 1 MB spans admitted one
map, established a normalized occurrence, and stopped before allocating the second
map: status remained `normalized`, counts were incomplete and uniqueness unknown.

The admitted near-budget source used 100% of the original-text budget and 99.6%
of the decomposition budget, retained one map and established 100 normalized-mode
matches with no resource refusal. Its repeated source locations yielded
`ambiguous` status and capped candidate counts, rather than admission failure.
Its 181.310 MiB peak demonstrates that admitted worst-shaped input remains costly;
the budget is a deterministic size/work policy, not a small fixed RSS guarantee.
Ordinary exact/normalized rows remained complete without resource-limit stops.
These synthetic local measurements establish behavior and cost, without claiming
a production throughput or latency guarantee.
