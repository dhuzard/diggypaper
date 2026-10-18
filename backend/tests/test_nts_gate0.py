"""Gate 0 acquisition logging with a faked network (no real ALURES data is involved)."""

import hashlib

from app.nts import gate0


def test_blocked_network_is_logged_and_nothing_is_fabricated(tmp_path, monkeypatch):
    monkeypatch.setattr(gate0, "fetch", lambda url, timeout=45.0: (None, None, "URLError: 403"))
    report = gate0.acquire(tmp_path / "alures")
    assert report["obtained_any_dataset"] is False and report["datasets"] == []
    (attempt,) = report["attempts"]
    assert (
        attempt["ok"] is False
        and attempt["error"] == "URLError: 403"
        and attempt["kind"] == "landing-page"
    )
    assert not list((tmp_path / "alures").glob("*"))


def test_discovered_csv_is_downloaded_with_a_sha256_receipt(tmp_path, monkeypatch):
    csv_bytes = b"NTS ID,Version\nNTS-ES-000001,1\n"
    page = b'<a href="/d/alures_nts_projects.csv">x</a><a href="/d/unrelated.csv">y</a>'

    def fake(url, timeout=45.0):
        return (200, page if url.endswith(".html") else csv_bytes, None)

    monkeypatch.setattr(gate0, "fetch", fake)
    report = gate0.acquire(tmp_path / "alures", "https://example.org/alures/datasets.html")
    assert [a["kind"] for a in report["attempts"]] == [
        "landing-page",
        "dataset",
    ]  # unrelated.csv skipped
    (ds,) = report["datasets"]
    assert ds["sha256"] == hashlib.sha256(csv_bytes).hexdigest() and ds["rows"] == 1
    assert ds["source_url"] == "https://example.org/d/alures_nts_projects.csv"
