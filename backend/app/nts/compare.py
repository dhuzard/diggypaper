"""Planned (NTS) versus article-reported animal numbers, without manufacturing a ratio.

The NTS figure is an authorization ceiling for a whole project; an article reports animals
for some cohorts of that project. Equating them needs unique-animal matching that no text
supplies, so no percentage is ever computed here. Only conservative flags are emitted.
"""

# ruff: noqa: E501

from __future__ import annotations

from typing import Any

from app.nts.counts import CountObservation

_SPECIES_SYNONYMS = {
    "rat": {"rat", "rats", "rattus norvegicus", "rattus"},
    "mouse": {"mouse", "mice", "mus musculus"},
}


def _alures_species_matches(article_species: str, alures_species: str) -> bool:
    return alures_species.strip().lower() in _SPECIES_SYNONYMS.get(
        article_species, {article_species}
    )


def compare(
    observations: list[CountObservation],
    link: dict[str, Any],
    planned: list[dict[str, Any]] | None,
    shared_with: list[str],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    """One comparison record per article/NTS link; ``not reported`` rather than a guess."""
    species = sorted({o.species for o in observations if o.species != "unknown"})
    totals = [o for o in observations if o.count_scope == "article-total" and o.number is not None]
    out: dict[str, Any] = {
        "link_state": link["state"],
        "ratio_computed": False,
        "unique_animal_matching_established": False,
        "article_species": species or ["not reported"],
        "article_reported_total": totals[0].number if totals else "not reported",
        "planned": "not reported",
        "reported_experimental_animals": "not reported",
        "cohort_sum_reconciliation": reconciliation,
        "comparability": "not-comparable",
        "reasons": [],
        "flags": [],
    }
    exp = [
        o
        for o in observations
        if (
            o.count_category in ("experimental", "control")
            and o.count_scope == "cohort-total"
            and o.derived
        )
        or o.rule_id == "performed-in-N-species"
    ]
    if exp:
        out["reported_experimental_animals"] = {
            "value": "see per-cohort observations",
            "note": "cohort figures may overlap and are not summed here",
            "cohorts": sorted({o.cohort_label for o in exp}),
        }
    if not link["state"].startswith("confirmed"):
        out["reasons"].append(
            f"no confirmed ALURES record for this article (link state: {link['state']})"
        )
        out["planned"] = "not reported (ALURES record not confirmed in this run)"
        return out
    if shared_with:
        out["flags"].append(
            f"NTS is also cited by other publications in this run: {sorted(shared_with)}; the planned number cannot be attributed to one article"
        )
    if planned is None or not planned:
        out["reasons"].append("no species-specific animal table was supplied for this NTS")
        return out
    out["planned"] = planned
    out["reasons"] += [
        "planned figure is a project-wide authorization ceiling, not animals used",
        "no unique-animal cohort matching is established between article and NTS",
    ]
    if any(o.possible_overlap for o in observations):
        out["reasons"].append("article cohorts carry overlap flags")
    for sp in species:
        rows = [r for r in planned if _alures_species_matches(sp, str(r["species"]))]
        if not rows:
            out["flags"].append(f"article species {sp!r} has no matching species row in the NTS")
            continue
        planned_n = [
            r["authorized_number"] for r in rows if isinstance(r["authorized_number"], int)
        ]
        if totals and planned_n and totals[0].number > sum(planned_n):
            out["flags"].append(
                f"article-reported total {totals[0].number} exceeds the NTS authorized {sum(planned_n)} for {sp}; investigate (version, unit or species scope)"
            )
    return out
