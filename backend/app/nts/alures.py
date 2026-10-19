"""Official ALURES bulk-export handling: receipts, schema discovery and exact-ID joins.

Nothing about the CSV layout is assumed. :func:`discover_schema` reports what the files
contain; :func:`load_table` takes explicit column names (or the discovered ones). The join is
exact on the normalized NTS identifier. Near matches are *candidates* and are never accepted.
Planned (authorized) animal numbers are maxima for a whole project, not usage.
"""

# ruff: noqa: E501

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.nts.identifiers import normalize_nts

_NTS_FULL = re.compile(r"^NTS[-\s]?([A-Za-z]{2})[-\s]?(\d{3,9})$", re.IGNORECASE)
_HREF_CSV = re.compile(r"""href=["']([^"']+\.(?:csv|zip)(?:\?[^"']*)?)["']""", re.IGNORECASE)

LINK_STATES = (
    "confirmed-exact",  # one normalized ID, one record chain, version rule evaluated
    "confirmed-exact-amended",  # exact ID, but ALURES holds a version newer than the cited one
    "confirmed-exact-cited-version-absent",  # exact ID, cited version not in the export
    "ambiguous",  # same ID with conflicting records (country/title) or several versions without a cited one
    "duplicate-rows",  # exact ID, several identical rows (kept once, reported)
    "candidate",  # near match only; never accepted
    "unmatched",  # ID reported by the article, absent from the export
    "alures-unavailable",  # no ALURES dataset was obtainable in this run
)


@dataclass(frozen=True)
class DatasetReceipt:
    file_name: str
    source_url: str
    retrieved_at: str
    sha256: str
    bytes: int
    rows: int
    http_status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def receipt_for(
    path: Path, source_url: str, retrieved_at: str, http_status: int | None = None
) -> DatasetReceipt:
    data = Path(path).read_bytes()
    text = data.decode("utf-8-sig", errors="replace")
    rows = max(0, sum(1 for _ in csv.reader(io.StringIO(text))) - 1)
    return DatasetReceipt(
        file_name=Path(path).name,
        source_url=source_url,
        retrieved_at=retrieved_at,
        sha256=hashlib.sha256(data).hexdigest(),
        bytes=len(data),
        rows=rows,
        http_status=http_status,
    )


def csv_links(html: str, base_url: str) -> list[str]:
    """Absolute CSV/ZIP links on a dataset landing page (no scraping beyond href)."""
    from urllib.parse import urljoin

    return sorted({urljoin(base_url, m) for m in _HREF_CSV.findall(html)})


def _dialect(header_line: str) -> type[csv.Dialect]:
    """Pick the delimiter from the header only and keep RFC 4180 quoting.

    ``csv.Sniffer`` on a short sample of the real ALURES export (long multi-line quoted text
    with doubled quotes) infers ``doublequote=False`` and misaligns a third of the rows, which
    silently drops the NTS identifier column below the detection threshold.
    """
    delimiter = max(",;\t|", key=header_line.count)

    class _Dialect(csv.excel):
        pass

    _Dialect.delimiter = delimiter
    return _Dialect


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    csv.field_size_limit(max(csv.field_size_limit(), 1 << 28))
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text), dialect=_dialect(text.split("\n", 1)[0]))
    return list(reader.fieldnames or []), [dict(r) for r in reader]


def discover_schema(path: Path) -> dict[str, Any]:
    """Observed columns and which look like NTS identifiers, versions, countries, species, counts."""
    fields, rows = _read(path)
    cols: dict[str, Any] = {}
    for name in fields:
        values = [(r.get(name) or "").strip() for r in rows]
        non_empty = [v for v in values if v]
        nts = sum(1 for v in non_empty if _NTS_FULL.match(v))
        numeric = sum(1 for v in non_empty if re.fullmatch(r"-?\d+(?:[.,]\d+)?", v))
        cols[name] = {
            "non_empty": len(non_empty),
            "distinct": len(set(non_empty)),
            "nts_id_fraction": round(nts / len(non_empty), 4) if non_empty else 0.0,
            "numeric_fraction": round(numeric / len(non_empty), 4) if non_empty else 0.0,
            "examples": sorted(set(non_empty))[:3],
        }
    id_candidates = [n for n, c in cols.items() if c["nts_id_fraction"] >= 0.9]
    low = {n: n.lower() for n in fields}
    return {
        "file": Path(path).name,
        "row_count": len(rows),
        "columns": cols,
        "id_column_candidates": id_candidates,
        "version_column_candidates": [n for n, low_name in low.items() if "version" in low_name],
        "country_column_candidates": [
            n for n, low_name in low.items() if "country" in low_name or "member" in low_name
        ],
        "species_column_candidates": [n for n, low_name in low.items() if "species" in low_name],
        "count_column_candidates": [
            n
            for n, low_name in low.items()
            if re.search(r"number|count|animals|total|qty|quantity", low_name)
        ],
    }


@dataclass
class NtsRecord:
    nts_id: str
    version: str | None
    country: str | None
    title: str | None
    row_numbers: list[int]
    raw_ids: list[str]
    attributes: dict[str, str] = field(default_factory=dict)


def load_table(
    path: Path,
    id_column: str,
    version_column: str | None = None,
    country_column: str | None = None,
    title_column: str | None = None,
) -> dict[str, list[NtsRecord]]:
    """Index rows by normalized NTS ID. Identical repeated rows are merged and reported."""
    _, rows = _read(path)
    seen: dict[tuple, NtsRecord] = {}
    index: dict[str, list[NtsRecord]] = defaultdict(list)
    for n, row in enumerate(rows, start=2):  # header is line 1
        raw = (row.get(id_column) or "").strip()
        m = _NTS_FULL.match(raw)
        if not m:
            continue
        nts = normalize_nts(m.group(1), m.group(2))
        version = normalize_version(row.get(version_column)) if version_column else None
        country = (row.get(country_column) or "").strip() or None if country_column else None
        title = (row.get(title_column) or "").strip() or None if title_column else None
        key = (nts, version, country, title)
        if key in seen:
            seen[key].row_numbers.append(n)
            seen[key].raw_ids.append(raw)
            continue
        rec = NtsRecord(
            nts, version, country, title, [n], [raw], {k: v for k, v in row.items() if v}
        )
        seen[key] = rec
        index[nts].append(rec)
    return dict(index)


def _vkey(v: str | None) -> tuple:
    return (0, 0) if v is None else (1, int(v)) if v.isdigit() else (1, -1)


def normalize_version(v: str | None) -> str | None:
    """``"1.0"`` (ALURES export) and ``"1"`` (article citation) are the same version."""
    v = (v or "").strip()
    if not v:
        return None
    return str(int(float(v))) if re.fullmatch(r"\d+(?:\.0+)?", v) else v


def join_exact(
    mention_norm: str,
    version_cited: str | None,
    country_expected: str | None,
    index: dict[str, list[NtsRecord]] | None,
) -> dict[str, Any]:
    """State of the exact-ID join. Never fabricates a row; fuzzy hits stay ``candidate``."""
    if index is None:
        return {
            "state": "alures-unavailable",
            "records": [],
            "reasons": ["no ALURES dataset in this run"],
        }
    version_cited = normalize_version(version_cited)
    records = index.get(mention_norm, [])
    if not records:
        cc, digits = mention_norm.split("-")[1], mention_norm.split("-")[2]
        near = sorted(
            k
            for k in index
            if k != mention_norm
            and (
                k.endswith(digits) or (k.split("-")[1] == cc and _hamming1(k.split("-")[2], digits))
            )
        )
        if near:
            return {
                "state": "candidate",
                "records": [],
                "candidates": near[:5],
                "reasons": ["no exact ID; near identifiers are candidates only and not accepted"],
            }
        return {
            "state": "unmatched",
            "records": [],
            "reasons": ["ID absent from the supplied ALURES tables"],
        }
    countries = {r.country for r in records if r.country}
    titles = {r.title for r in records if r.title}
    reasons: list[str] = []
    if len(countries) > 1 or len(titles) > 1:
        return {
            "state": "ambiguous",
            "records": [r.__dict__ for r in records],
            "reasons": ["conflicting country/title across rows for one ID"],
        }
    versions = sorted({r.version for r in records}, key=_vkey)
    current = versions[-1]
    if (
        country_expected
        and countries
        and country_expected not in {c[:2].upper() for c in countries}
        and country_expected not in countries
    ):
        reasons.append(
            f"ID country code {country_expected} differs from ALURES country {sorted(countries)}"
        )
    dup = any(len(r.row_numbers) > 1 for r in records)
    if version_cited is None:
        state = (
            "ambiguous" if len(versions) > 1 else ("duplicate-rows" if dup else "confirmed-exact")
        )
        if len(versions) > 1:
            reasons.append("article cites no version and ALURES holds several")
    elif version_cited not in versions:
        state = "confirmed-exact-cited-version-absent"
        reasons.append(f"cited version {version_cited} not in export; export has {versions}")
    elif _vkey(current) > _vkey(version_cited):
        state = "confirmed-exact-amended"
        reasons.append(
            f"ALURES current version {current} is newer than cited version {version_cited}"
        )
    else:
        state = "duplicate-rows" if dup else "confirmed-exact"
    return {
        "state": state,
        "records": [r.__dict__ for r in records],
        "versions": versions,
        "current_version": current,
        "reasons": reasons,
    }


def _hamming1(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b, strict=True)) <= 1


def planned_animals(
    index_rows: list[dict[str, str]], species_col: str, count_col: str
) -> list[dict[str, Any]]:
    """Species-specific authorized numbers exactly as written; non-numeric stays ``not reported``."""
    out = []
    for row in index_rows:
        raw = (row.get(count_col) or "").strip()
        out.append(
            {
                "species": (row.get(species_col) or "").strip() or "not reported",
                "authorized_number": (
                    int(float(raw)) if re.fullmatch(r"\d+(?:\.0+)?", raw) else "not reported"
                ),
                "raw_value": raw or None,
                "count_category": "NTS_planned_or_authorized",
                "meaning": "maximum authorized for the whole project, not animals used",
            }
        )
    return out


def rows_by_id(path: Path, id_column: str) -> dict[str, list[dict[str, str]]]:
    """All rows of a linked table grouped by normalized NTS ID (rows kept exactly as written)."""
    _, rows = _read(path)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for n, row in enumerate(rows, start=2):
        m = _NTS_FULL.match((row.get(id_column) or "").strip())
        if m:
            grouped[normalize_nts(m.group(1), m.group(2))].append({**row, "_row_number": str(n)})
    return dict(grouped)
