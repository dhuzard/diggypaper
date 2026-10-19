"""Compact per-case table: what is confirmed, what is planned, what is reported, what is comparable."""

# ruff: noqa: E501

from __future__ import annotations

import re
from typing import Any

_SPECIES = re.compile(r"\b(rats?|mice|mouse)\b", re.IGNORECASE)
_STRAIN = re.compile(
    r"\b(Wistar|Sprague[- ]Dawley|C57BL/6[JN]?|db/db|OF1|CD1|BALB/c)\b", re.IGNORECASE
)


def _reported(obs: list[dict[str, Any]], rec: dict[str, Any]) -> str:
    art = [o for o in obs if o["source_kind"] == "article" and o["number"] != "not reported"]
    if not art:
        return "not reported"
    parts = []
    total = next((o for o in art if o["count_scope"] == "article-total"), None)
    if total:
        parts.append(f"article total {total['number']} (stated)")
    seen = set()
    for o in art:
        if o["count_scope"] == "cohort-total":
            label = o["cohort_label"].split(" (controls)")[0][:46] + (
                " controls" if "(controls)" in o["cohort_label"] else ""
            )
            tag = "derived" if o["derived"] else "stated"
            key = (o["cohort_label"], o["number"])
            if key not in seen:
                seen.add(key)
                parts.append(f"{label.strip()}: {o['number']} ({tag})")
        elif o["count_scope"] == "additional-to-group":
            key = ("add", o["cohort_label"], o["sex"], o["number"])
            if key not in seen:
                seen.add(key)
                parts.append(f"+{o['number']} {o['sex']} added")
    if rec.get("status") not in (None, "no-article-total", "exact"):
        parts.append(
            f"{rec['unattributed_animals']} of the total not attributed to a stated cohort"
        )
    return "; ".join(parts)


def rows(res: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    comps = {(c["case_id"], c["nts_id"]): c for c in res["comparisons"]}
    for c in res["cases"]:
        spec, frozen = c["spec"], c["frozen"]
        cid = spec["case_id"]
        obs = [o for o in res["observations"] if o["case_id"] == cid]
        scoped = " ".join(
            str(lk["applies_to"] or "")
            for lk in res["links"]
            if lk["case_id"] == cid and lk["identifier_system"] == "ALURES-NTS"
        )
        basis = scoped if _SPECIES.search(scoped) else frozen.view_text
        names = {"rats": "rat", "rat": "rat", "mice": "mouse", "mouse": "mouse"}
        species = sorted({names[w.lower()] for w in _SPECIES.findall(basis)})
        strains = sorted({m for m in _STRAIN.findall(basis)})
        nts_links = [
            lk
            for lk in res["links"]
            if lk["case_id"] == cid and lk["identifier_system"] == "ALURES-NTS"
        ]
        for lk in nts_links:
            cmp = comps.get((cid, lk["identifier_normalized"]))
            planned = cmp["planned"] if cmp else "not reported"
            out.append(
                {
                    "case": cid,
                    "doi": frozen.source["identifiers"].get("doi") or "not reported",
                    "nts": (lk["identifier_normalized"] or "none reported")
                    + (
                        f" v{lk['version_cited']}"
                        if lk["version_cited"]
                        else " (version not cited)"
                        if lk["identifier_normalized"]
                        else ""
                    ),
                    "species": ", ".join(species + [f"({s})" for s in strains]) or "not reported",
                    "planned": planned
                    if isinstance(planned, str)
                    else "; ".join(
                        f"{p['authorized_number']} (project-wide total, species not split)"
                        if p["species"] == "not reported"
                        else f"{p['species']}: {p['authorized_number']}"
                        for p in planned
                    ),
                    "reported": _reported(obs, c["ex"]["reconciliation"]),
                    "comparability": (cmp["comparability"] + ": " + "; ".join(cmp["reasons"][:2]))
                    if cmp
                    else "not applicable (no NTS ID reported)",
                    "match": lk["link_state"],
                }
            )
    return out


def markdown(table: list[dict[str, str]]) -> str:
    head = "| Case | DOI | NTS ID / version | Species (strain) | Planned animals (NTS) | Reported animals (article) | Count comparability | Match status |"
    lines = [head, "|---|---|---|---|---|---|---|---|"]
    for r in table:
        lines.append(
            "| "
            + " | ".join(
                r[k].replace("|", "/")
                for k in (
                    "case",
                    "doi",
                    "nts",
                    "species",
                    "planned",
                    "reported",
                    "comparability",
                    "match",
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"
