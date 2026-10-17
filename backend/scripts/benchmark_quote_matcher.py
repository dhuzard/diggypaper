"""Offline microbenchmark: run from backend with ``python -m scripts.benchmark_quote_matcher``.

Timing and tracemalloc are separate runs; allocation tracking distorts latency.
Source/quote construction is excluded. Cold batch includes lazy normalization;
warm batch reuses the same matcher. Peak is Python-traced allocations (not RSS).
"""

import argparse
import gc
import json
import platform
import tracemalloc
from collections import Counter
from statistics import median
from time import perf_counter

from app.quote_matcher import (
    ALGORITHM_VERSION,
    NORMALIZATION_VERSION,
    ArticleInputSpan,
    ArticleQuoteMatcher,
    TargetArticleInput,
)


def fixture(size: int, mode: str) -> tuple[TargetArticleInput, list[str]]:
    if mode == "admitted_near_budget":
        block = "a" + "\u0344" * 127
        codepoints = 1_100_000
        text = block * (codepoints // len(block)) + block[: codepoints % len(block)]
        quotes = ["ä\u0301"] * 100
    elif mode in ("canonical_reordering_quote", "canonical_reordering_source"):
        text = "a" + "\u0315" * 50_000 + "\u0300" * 8_001
        quote = "à" + "\u0300" * 8_000 if mode.endswith("quote") else "à"
        quotes = [quote] * 100
    elif mode == "rejected_normalized":
        text = "\u0344" * (size // 2)
        quotes = ["\u0308\u0301" * (500 + index) + "\u0308" for index in range(100)]
    elif mode == "repeated":
        text = "a" * size
        quotes = ["a" * length for length in range(1, 101)]
    else:
        lines = [f"Observation {index:03}: alpha beta café.\n" for index in range(100)]
        if mode == "normalized_whitespace":
            lines = [line.replace("alpha beta", "alpha\t\r\n beta") for line in lines]
        if mode in ("normalized_unicode", "repeated_spans"):
            lines = [line.replace("café", "cafe\u0301") for line in lines]
        body = "".join(lines)
        # Fixed UTF-8 byte budget, without cutting a multibyte character.
        text = body + ("Methods and observations. " * size)[: size - len(body.encode("utf-8"))]
        quotes = [f"Observation {index:03}: alpha beta café." for index in range(100)]
    span_count = 10 if mode == "repeated_spans" else 1
    captured = TargetArticleInput(
        "benchmark-invocation",
        tuple(
            ArticleInputSpan(
                text, f"messages/{index}/content", 0, "snapshot", "view", 0, "abstract", 0
            )
            for index in range(span_count)
        ),
    )
    return captured, quotes


def batch(matcher: ArticleQuoteMatcher, quotes: list[str]):
    return [matcher.match(quote) for quote in quotes]


def measure(size: int, mode: str) -> dict:
    source, quotes = fixture(size, mode)
    cold_times = []
    warm_times = []
    for _ in range(3):
        gc.collect()
        matcher = ArticleQuoteMatcher(
            source, allow_normalized=True, max_candidates=1 if mode == "rejected_normalized" else 32
        )
        started = perf_counter()
        results = batch(matcher, quotes)
        cold_times.append((perf_counter() - started) * 1000)
        started = perf_counter()
        batch(matcher, quotes)
        warm_times.append((perf_counter() - started) * 1000)
    del matcher
    gc.collect()
    tracemalloc.start()
    memory_matcher = ArticleQuoteMatcher(
        source, allow_normalized=True, max_candidates=1 if mode == "rejected_normalized" else 32
    )
    memory_results = batch(memory_matcher, quotes)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(memory_results) == 100
    assert len(source.spans[0].text.encode("utf-8")) == size
    return {
        "utf8_bytes": size,
        "span_count": len(source.spans),
        "total_utf8_bytes": sum(len(span.text.encode("utf-8")) for span in source.spans),
        "codepoints": len(source.spans[0].text),
        "mode": mode,
        "quotes": 100,
        "cold_median_ms": round(median(cold_times), 3),
        "warm_median_ms": round(median(warm_times), 3),
        "peak_python_mib": round(peak / 1024**2, 3),
        "statuses": dict(Counter(result.status for result in results)),
        "match_modes": dict(Counter(result.match_mode for result in results if result.match_mode)),
        "max_retained_candidates": max(len(result.candidates) for result in results),
        "max_candidate_examinations": max(result.candidate_examinations for result in results),
        "max_validation_codepoints": max(result.validation_codepoints for result in results),
        "resource_limit_count": sum(result.reason == "resource_limit" for result in results),
        "incomplete_count": sum(not result.counts_complete for result in results),
        "resource_limit_kinds": dict(
            Counter(result.resource_limit_kind for result in results if result.resource_limit_kind)
        ),
        "retained_normalized_maps": len(memory_matcher._normalized),
        "normalized_source_codepoints": memory_matcher._normalized_source_codepoints,
        "normalized_decomposed_codepoints": memory_matcher._normalized_decomposed_codepoints,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-limits-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "algorithm_version": ALGORITHM_VERSION,
                "normalization_version": NORMALIZATION_VERSION,
                "platform": platform.platform(),
                "timing_repetitions": 3,
                "memory": "tracemalloc peak; source/quote construction excluded; not RSS",
            }
        )
    )
    if not args.resource_limits_only:
        for size in (100_000, 1_000_000):
            for mode in ("exact", "normalized_whitespace", "normalized_unicode", "repeated"):
                print(json.dumps(measure(size, mode)), flush=True)
    for size in (20_000, 100_000, 1_000_000):
        print(json.dumps(measure(size, "rejected_normalized")), flush=True)
    for mode in ("canonical_reordering_quote", "canonical_reordering_source"):
        print(json.dumps(measure(116_003, mode)), flush=True)
    print(json.dumps(measure(1_000_000, "repeated_spans")), flush=True)
    print(json.dumps(measure(2_191_406, "admitted_near_budget")), flush=True)


if __name__ == "__main__":
    main()
