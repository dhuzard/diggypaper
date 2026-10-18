"""Gate 0 acquisition of the official ALURES bulk exports, with an auditable attempt log.

Every attempt (URL, outcome, HTTP status or error class) is recorded, including failures, so a
blocked run says exactly what was tried. No row is ever synthesized when a download fails.
"""

# ruff: noqa: E501  (long diagnostic strings and regexes are kept readable, not wrapped)

from __future__ import annotations

import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.nts.alures import DatasetReceipt, csv_links, receipt_for

LANDING_URL = "https://environmental-data.ec.europa.eu/alures/datasets.html"
WANTED = ("alures_nts_projects", "alures_nts", "animals", "species", "rar", "retrospective")
USER_AGENT = "diggypaper-nts-poc/0.1 (research; contact via repository)"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url: str, timeout: float = 45.0) -> tuple[int | None, bytes | None, str | None]:
    """(http_status, body, error). Uses the process proxy and CA settings; never disables TLS checks."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only, fixed hosts)
            return resp.status, resp.read(), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HTTPError {exc.code}"
    except Exception as exc:  # network policy denial, DNS, TLS, timeout
        return None, None, f"{type(exc).__name__}: {exc}"


def acquire(
    dest: Path, landing_url: str = LANDING_URL, extra_urls: list[str] | None = None
) -> dict[str, Any]:
    """Try the landing page, then every CSV/ZIP link on it that looks like an NTS/animal/RAR table."""
    dest.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    receipts: list[DatasetReceipt] = []

    def attempt(url: str, kind: str) -> bytes | None:
        status, body, error = fetch(url)
        attempts.append(
            {
                "url": url,
                "kind": kind,
                "at": _now(),
                "http_status": status,
                "ok": body is not None,
                "error": error,
                "bytes": len(body) if body is not None else 0,
            }
        )
        return body

    page = attempt(landing_url, "landing-page")
    links = csv_links(page.decode("utf-8", errors="replace"), landing_url) if page else []
    for url in [*links, *(extra_urls or [])]:
        name = url.rsplit("/", 1)[-1].split("?")[0].lower()
        if not any(w in name for w in WANTED) and url not in (extra_urls or []):
            continue
        body = attempt(url, "dataset")
        if body is None:
            continue
        path = dest / name
        path.write_bytes(body)
        receipts.append(receipt_for(path, url, attempts[-1]["at"], attempts[-1]["http_status"]))
    return {
        "landing_url": landing_url,
        "attempts": attempts,
        "discovered_links": links,
        "datasets": [r.to_dict() for r in receipts],
        "obtained_any_dataset": bool(receipts),
    }
