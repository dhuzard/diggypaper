"""Project field template for NTS traceability projects and the decision a deterministic
extraction produces for the platform's prediction pipeline.

The extractor is registered as an inference backend, so its output must look like any other
prediction: one value per project field, ``evidence_quotes`` and field-level ``evidence_links``.
Everything NTS-specific (typed mentions, count observations, receipts) rides along under
``nts_extraction`` and is never a field value.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

NTS_QUESTION = (
    "Which ALURES non-technical summary (NTS) identifiers, national project authorizations and "
    "written animal numbers does this article report?"
)

NTS_FIELDS: list[dict[str, Any]] = [
    {"key": "nts_identifier", "label": "ALURES NTS identifier(s) cited", "type": "text"},
    {
        "key": "national_authorization",
        "label": "National / institutional authorization number(s)",
        "type": "text",
        "primary": False,
    },
    {"key": "animal_counts", "label": "Animal numbers as written", "type": "text"},
]

NONE_REPORTED = "none reported"

# proposal kind -> project field key
KIND_TO_FIELD = {
    "nts-identifier": "nts_identifier",
    "national-authorization": "national_authorization",
    "animal-counts": "animal_counts",
}


def is_nts_project(fields: list[dict[str, Any]]) -> bool:
    keys = {f.get("key") for f in fields}
    return set(KIND_TO_FIELD.values()) <= keys


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def decision_from_extraction(extraction: dict[str, Any]) -> dict[str, Any]:
    """Build the prediction decision from ``extract.propose`` output.

    Quotes are the proposals' quoted windows in proposal order (duplicates collapse to the first
    index); each proposal links its quote to the field of its kind. No confidence is reported:
    a deterministic extraction is never auto-accepted, it waits for human review.
    """
    quotes: list[str] = []
    index_of: dict[str, int] = {}
    links: list[dict[str, Any]] = []
    proposals_out: list[dict[str, Any]] = []
    for p in extraction["proposals"]:
        if p.quote not in index_of:
            index_of[p.quote] = len(quotes)
            quotes.append(p.quote)
        qi = index_of[p.quote]
        link = {"field_key": KIND_TO_FIELD[p.kind], "quote_index": qi}
        if link not in links:
            links.append(link)
        proposals_out.append(
            {
                "finding_id": p.finding_id,
                "kind": p.kind,
                "field_key": KIND_TO_FIELD[p.kind],
                "quote_index": qi,
                "section_index": p.section_index,
                "window": list(p.window),
                "view_window": list(p.view_window),
                "receipt": _plain(p.receipt),
                "content": _plain(p.content),
                "typed": _plain(p.typed),
                "observation_ids": list(p.observation_ids),
            }
        )

    nts_values = []
    for m in extraction["nts_mentions"]:
        mention = m["mention"]
        label = mention.normalized
        if getattr(mention, "version_cited", None):
            label += f" v{mention.version_cited}"
        if label not in nts_values:
            nts_values.append(label)
    auth_values = []
    for a in extraction["authorizations"]:
        mention = a["mention"]
        label = f"{mention.value} ({mention.kind})"
        if label not in auth_values:
            auth_values.append(label)
    count_values = []
    for o in extraction["observations"]:
        if o.number is None:
            continue
        label = f"{o.number} {o.species} ({o.count_scope}{', derived' if o.derived else ''})"
        if label not in count_values:
            count_values.append(label)

    return {
        "nts_identifier": "; ".join(nts_values) or NONE_REPORTED,
        "national_authorization": "; ".join(auth_values) or NONE_REPORTED,
        "animal_counts": "; ".join(count_values) or NONE_REPORTED,
        "evidence_quotes": quotes,
        "evidence_links": links,
        "nts_extraction": {
            "schema": "nts-extraction/1.0",
            "proposals": proposals_out,
            "nts_mentions": [
                {"section_index": m["section_index"], **_plain(m["mention"])}
                for m in extraction["nts_mentions"]
            ],
            "authorizations": [
                {"section_index": a["section_index"], **_plain(a["mention"])}
                for a in extraction["authorizations"]
            ],
            "observations": [_plain(o) for o in extraction["observations"]],
            "reconciliation": _plain(extraction["reconciliation"]),
            "missing_counts": extraction["missing_counts"],
        },
    }
