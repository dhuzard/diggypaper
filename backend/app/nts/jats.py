"""Minimal PMC open-access JATS reader for the optional ``fetch`` command.

Only methods and results sections are kept (the NTS workflow reads nothing else) and each
paragraph becomes its own contiguous span. Entity declarations and DTD internal subsets are
refused outright; the bare external ``<!DOCTYPE ... PUBLIC "..." "...">`` that every PMC efetch
response carries is stripped before parsing (expat never fetches external DTDs).
"""

# ruff: noqa: E501

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405 (DOCTYPE/ENTITY refused before parsing)
from typing import Any

from app.article_text import classify_title

_CC = re.compile(
    r"creativecommons\.org/(?:licenses|publicdomain)/(?P<kind>[a-z-]+)/(?P<ver>[\d.]+)"
)
_SPDX = {
    "by": "CC-BY",
    "by-sa": "CC-BY-SA",
    "by-nc": "CC-BY-NC",
    "by-nd": "CC-BY-ND",
    "by-nc-nd": "CC-BY-NC-ND",
    "by-nc-sa": "CC-BY-NC-SA",
    "zero": "CC0",
    "mark": "PDM",
}


def _text(el: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _strip_doctype(xml_bytes: bytes) -> bytes:
    """Remove one external-identifier-only DOCTYPE; refuse internal subsets and entities."""
    lowered = xml_bytes.lower()
    if b"<!entity" in lowered:
        raise ValueError("DTD/entity declarations are refused")
    start = lowered.find(b"<!doctype")
    if start < 0:
        return xml_bytes
    end = lowered.find(b">", start)
    bracket = lowered.find(b"[", start)
    if end < 0 or (0 <= bracket < end) or lowered.find(b"<!doctype", end) >= 0:
        raise ValueError("DTD/entity declarations are refused")
    return xml_bytes[:start] + xml_bytes[end + 1 :]


def parse(xml_bytes: bytes, source_id: str, retrieved_at: str, public_url: str) -> dict[str, Any]:
    root = ET.fromstring(_strip_doctype(xml_bytes))  # noqa: S314
    ids = {}
    for el in root.iter("article-id"):
        ids[el.get("pub-id-type")] = _text(el)
    title_el = root.find(".//article-title")
    sections: list[dict[str, str]] = []

    def walk(sec: ET.Element, inherited: str) -> None:
        t = sec.find("title")
        title = _text(t) if t is not None else ""
        kind = classify_title(title)
        if kind == "other":
            kind = classify_title(sec.get("sec-type"))
        if kind == "other":
            kind = inherited  # a nested "Ethics statement" belongs to its enclosing Methods
        if kind in ("methods", "results"):
            for i, p in enumerate(sec.findall("p"), start=1):
                text = _text(p)
                if text:
                    sections.append(
                        {"title": f"{title} (paragraph {i})", "kind": kind, "text": text}
                    )
        for child in sec.findall("sec"):
            walk(child, kind)

    body = root.find(".//body")
    for top in body.findall("sec") if body is not None else []:
        walk(top, "other")
    license_href = next(
        (el.get("{http://www.w3.org/1999/xlink}href") for el in root.iter("license")), None
    )
    m = _CC.search(license_href or "")
    spdx = (
        f"{_SPDX.get(m.group('kind'), 'unknown')}-{m.group('ver')}"
        if m and m.group("kind") in _SPDX
        else "unknown"
    )
    if spdx in ("CC0-1.0", "CC0-1"):
        spdx = "CC0-1.0"
    return {
        "source_id": source_id,
        "identifiers": {
            "doi": ids.get("doi"),
            "pmid": ids.get("pmid"),
            "pmcid": ids.get("pmcid") or ids.get("pmc"),
        },
        "title": _text(title_el) if title_el is not None else "unknown",
        "authors": [],
        "pub_types": ["Journal Article"],
        "license": {
            "license_id": spdx,
            "url": license_href,
            "statement": None,
            "basis": "licence URL read from the PMC JATS <license> element",
            "evidence_url": public_url if spdx != "unknown" else None,
        },
        "retrieval": {
            "method": "pmc-efetch-jats",
            "retrieved_at": retrieved_at,
            "completeness": "methods+results paragraphs",
            "fidelity": "text of <p> elements of methods/results sections; tags removed, whitespace collapsed",
            "public_url": public_url,
        },
        "sections": sections,
    }
