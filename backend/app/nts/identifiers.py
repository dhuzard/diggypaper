"""Deterministic extraction of ALURES NTS identifiers and national authorization numbers.

An NTS identifier (``NTS-<ISO2>-<digits>``) is the only key joined to ALURES. National or
institutional authorization numbers (APAFIS, UK project licences, Spanish regional numbers,
Romanian authority numbers) are stored with a distinct ``kind`` and are never equated with
an NTS identifier or with each other: there is no universal crosswalk.
"""

# ruff: noqa: E501  (long diagnostic strings and regexes are kept readable, not wrapped)

from __future__ import annotations

import re
from dataclasses import dataclass

# Verbatim form is kept; ``normalized`` only fixes case and separators.
_NTS = re.compile(
    r"\bNTS[\s‐-–-]?(?P<cc>[A-Za-z]{2})[\s‐-–-]?(?P<num>\d{3,9})\b",
    re.IGNORECASE,
)
_VERSION_AFTER = re.compile(r"\s*(?:,\s*)?(?:v(?:ersion)?\.?\s*(?P<v>\d+))", re.IGNORECASE)
_DATE_AFTER = re.compile(r"\s*,?\s*(?P<d>\d{1,2}\s+[A-Z][a-z]+\s+\d{4})")

ISO_TO_AUTHORITY = {"ES": "Spain", "RO": "Romania", "FR": "France", "DE": "Germany", "IT": "Italy"}


@dataclass(frozen=True)
class NtsMention:
    verbatim: str
    normalized: str
    country: str
    version_cited: str | None
    date_cited: str | None
    start: int
    end: int


@dataclass(frozen=True)
class AuthorizationMention:
    kind: str  # apafis | uk-ppl | es-regional | ro-project-authorization | ro-ethics-approval | ro-facility-authorization
    jurisdiction: str
    verbatim: str
    value: str
    start: int
    end: int


def normalize_nts(cc: str, num: str) -> str:
    return f"NTS-{cc.upper()}-{num}"


def extract_nts(text: str) -> list[NtsMention]:
    """Every NTS identifier in ``text`` with the version/date that immediately follows it."""
    found = []
    for m in _NTS.finditer(text):
        tail = text[m.end() :]
        version = _VERSION_AFTER.match(tail)
        end = m.end() + (version.end() if version else 0)
        date = _DATE_AFTER.match(text[end:]) if version else None
        found.append(
            NtsMention(
                verbatim=m.group(0),
                normalized=normalize_nts(m.group("cc"), m.group("num")),
                country=m.group("cc").upper(),
                version_cited=version.group("v") if version else None,
                date_cited=date.group("d") if date else None,
                start=m.start(),
                end=m.end(),
            )
        )
    return found


# (kind, jurisdiction, regex with group "value")
_AUTH_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("apafis", "FR", re.compile(r"\bAPAFIS\s*#?\s*(?P<value>\d+[-\w]*)", re.IGNORECASE)),
    (
        "uk-ppl",
        "GB",
        re.compile(r"\b(?:Project\s+Licen[cs]e|PPL)\s+(?:PPL\s+)?(?P<value>[A-Z0-9]{7,10})\b"),
    ),
    ("es-regional", "ES", re.compile(r"#(?P<value>\d+/\d+/\d{4}/\d+/[A-Z])\b")),
    (
        "ro-project-authorization",
        "RO",
        re.compile(
            r"project authorization was issued by [^()]{3,120}\((?P<value>\d+/\d{1,2} [A-Z][a-z]+ \d{4})\)"
        ),
    ),
    (
        "ro-ethics-approval",
        "RO",
        re.compile(
            r"approval of the [^()]{3,160}Committee[^()]{0,120}\((?P<value>\d+/\d{1,2} [A-Z][a-z]+ \d{4})\)"
        ),
    ),
    (
        "ro-facility-authorization",
        "RO",
        re.compile(r"Veterinary authorization (?P<value>\d+/\d{1,2} [A-Z][a-z]+ \d{4})"),
    ),
)


def extract_authorizations(text: str) -> list[AuthorizationMention]:
    out = []
    for kind, jurisdiction, pattern in _AUTH_PATTERNS:
        for m in pattern.finditer(text):
            out.append(
                AuthorizationMention(
                    kind=kind,
                    jurisdiction=jurisdiction,
                    verbatim=m.group(0) if kind in ("apafis", "uk-ppl") else m.group("value"),
                    value=m.group("value"),
                    start=m.start("value"),
                    end=m.end("value"),
                )
            )
    return sorted(out, key=lambda a: (a.start, a.kind))
