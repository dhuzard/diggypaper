"""Régression F-0 : ``app.tasks.benchmark`` doit s'importer sans le module Unix ``resource``."""

import importlib
import sys


def test_benchmark_imports_without_resource(monkeypatch):
    # ``None`` dans sys.modules fait lever ImportError à ``import resource``.
    monkeypatch.setitem(sys.modules, "resource", None)
    try:
        import app.tasks.benchmark as benchmark

        benchmark = importlib.reload(benchmark)
        assert benchmark.resource is None
        assert benchmark._peak_ram_mb() is None
    finally:
        monkeypatch.undo()
        importlib.reload(benchmark)  # restaure le comportement natif de la plateforme


def test_peak_ram_matches_platform():
    import app.tasks.benchmark as benchmark

    value = benchmark._peak_ram_mb()
    if benchmark.resource is None:
        assert value is None
    else:
        assert isinstance(value, float) and value > 0
