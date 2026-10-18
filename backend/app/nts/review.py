"""Human-review queue for NTS finding proposals.

The queue prepares work for people; it records no decision. Every proposed judgment is labelled
as an automation proposal and is kept out of ``assessments.jsonl``. Packets stay
``HUMAN_REVIEW_PENDING`` until independent reviewers act through Finding Curation.
"""

# ruff: noqa: E501  (long diagnostic strings and regexes are kept readable, not wrapped)

from __future__ import annotations

from typing import Any

PENDING = "HUMAN_REVIEW_PENDING"
LABEL = "AUTOMATION PROPOSAL - not a human assessment and not a reviewer judgment"


def uncertainties(
    kind: str,
    proposal: dict[str, Any],
    rights_ok: bool,
    link: dict[str, Any] | None,
    scope: dict[str, Any] | None,
    observations: list[dict[str, Any]],
) -> list[str]:
    out = []
    if proposal["receipt_status"] != "exact":
        out.append(f"quote match is {proposal['receipt_status']!r}, not exact")
    if not rights_ok:
        out.append(
            "quotation redistribution is not established for this article: the quote is withheld from public outputs (hash only)"
        )
    out.append(
        "source text is a contiguous excerpt transcribed from a connector rendering, not a byte-identical publisher copy"
    )
    if kind == "nts-identifier":
        state = link["alures_state"] if link else "unknown"
        out.append(f"identifier is as reported by the article; ALURES join state: {state}")
        if scope and scope.get("does_not_apply_to"):
            out.append(
                "the NTS covers only part of the article's animal work: "
                + "; ".join(scope["does_not_apply_to"])
            )
    if kind == "national-authorization":
        out.append(
            "national/institutional numbers are not NTS identifiers; no crosswalk is assumed"
        )
    if kind == "animal-counts":
        for o in observations:
            for n in o["possible_overlap"]:
                out.append(f"{o['cohort_label']}: {n}")
        if any(o["derived"] for o in observations):
            out.append(
                "includes derived totals (groups x animals); they are not numbers the article states"
            )
        out.append(
            "counts are animals per article cohort; they are not comparable to an NTS ceiling without unique-animal matching"
        )
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def proposed_judgment(kind: str, proposal: dict[str, Any]) -> dict[str, str]:
    """Narrow proposal about *this finding's own statement* only; a reviewer decides."""
    if proposal["receipt_status"] != "exact":
        return {
            "status": "unclear",
            "label": LABEL,
            "rationale": "the quotation was not matched exactly in the frozen source; the passage cannot be located",
        }
    return {
        "status": "supports",
        "label": LABEL,
        "rationale": f"the finding statement is the quoted passage itself (kind: {kind}); a reviewer must confirm the typed extraction (identifier, number, species, cohort) is read correctly",
    }


def render_markdown(queue: list[dict[str, Any]]) -> str:
    lines = [
        "# NTS human review queue",
        "",
        f"Status: **{PENDING}**. Nothing below is a human decision. Proposed judgments are automation proposals and are not in any packet.",
        "",
    ]
    for item in queue:
        lines += [
            f"## {item['case_id']} - {item['kind']} - `{item['finding_id']}`",
            "",
            f"- Article: {item['doi']} ({item['pmcid']}); snapshot `{item['snapshot_sha256'][:16]}`",
            f"- Passage: view offsets {item['view_start']}-{item['view_end']}, match **{item['receipt_status']}**, quote sha256 `{item['quote_sha256'][:16]}`",
            "- Quote: "
            + (
                f"> {item['quote']}"
                if item["quote"]
                else "_withheld (rights not established); open the private source copy_"
            ),
            f"- Suggested extraction: `{item['suggested_extraction']}`",
            f"- Proposed judgment: **{item['proposed_judgment']['status']}** ({item['proposed_judgment']['label']})",
            "- Uncertainties:",
            *[f"  - {u}" for u in item["uncertainties"]],
            f"- Review state: **{item['review_state']}**",
            "",
        ]
    return "\n".join(lines) + "\n"
