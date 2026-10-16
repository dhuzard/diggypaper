"""Offline microbenchmark: run from backend with ``python -m scripts.benchmark_quote_matcher``.

Timing and tracemalloc are separate runs; allocation tracking distorts latency.
Source/quote construction is excluded. Cold batch includes lazy normalization;
warm batch reuses the same matcher. Peak is Python-traced allocations (not RSS).
"""

import gc
import json
import platform
import tracemalloc
from collections import Counter
from statistics import median
from time import perf_counter

from app.quote_matcher import ArticleInputSpan, ArticleQuoteMatcher, TargetArticleInput


def fixture(size: int, mode: str) -> tuple[TargetArticleInput, list[str]]:
    if mode == "repeated":
        text = "a" * size
        quotes = ["a" * length for length in range(1, 101)]
    else:
        lines = [f"Observation {index:03}: alpha beta café.\n" for index in range(100)]
        if mode == "normalized_whitespace":
            lines = [line.replace("alpha beta", "alpha\t\r\n beta") for line in lines]
        if mode == "normalized_unicode":
            lines = [line.replace("café", "cafe\u0301") for line in lines]
        body = "".join(lines)
        # Fixed UTF-8 byte budget, without cutting a multibyte character.
        text = body + ("Methods and observations. " * size)[: size - len(body.encode("utf-8"))]
        quotes = [f"Observation {index:03}: alpha beta café." for index in range(100)]
    captured = TargetArticleInput(
        "benchmark-invocation",
        (ArticleInputSpan(text, "messages/1/content", 0, "snapshot", "view", 0, "abstract", 0),),
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
        matcher = ArticleQuoteMatcher(source, allow_normalized=True)
        started = perf_counter()
        results = batch(matcher, quotes)
        cold_times.append((perf_counter() - started) * 1000)
        started = perf_counter()
        batch(matcher, quotes)
        warm_times.append((perf_counter() - started) * 1000)
    del matcher
    gc.collect()
    tracemalloc.start()
    memory_matcher = ArticleQuoteMatcher(source, allow_normalized=True)
    memory_results = batch(memory_matcher, quotes)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(memory_results) == 100
    assert len(source.spans[0].text.encode("utf-8")) == size
    return {
        "utf8_bytes": size,
        "codepoints": len(source.spans[0].text),
        "mode": mode,
        "quotes": 100,
        "cold_median_ms": round(median(cold_times), 3),
        "warm_median_ms": round(median(warm_times), 3),
        "peak_python_mib": round(peak / 1024**2, 3),
        "statuses": dict(Counter(result.status for result in results)),
        "max_retained_candidates": max(len(result.candidates) for result in results),
    }


def main() -> None:
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "timing_repetitions": 3,
                "memory": "tracemalloc peak; source/quote construction excluded; not RSS",
            }
        )
    )
    for size in (100_000, 1_000_000):
        for mode in ("exact", "normalized_whitespace", "normalized_unicode", "repeated"):
            print(json.dumps(measure(size, mode)), flush=True)


if __name__ == "__main__":
    main()
