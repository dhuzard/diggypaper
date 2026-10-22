"""Per-article extraction: NTS mentions, authorizations and count clusters -> finding proposals.

Everything here is a *proposal* made by deterministic rules. A proposal has a verbatim passage
with a quote-match receipt; it has no reviewer, no support judgment and no approval.
"""

# ruff: noqa: E501  (long diagnostic strings and regexes are kept readable, not wrapped)

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.finding_schema import FindingContent
from app.nts.counts import (
    Cluster,
    CountObservation,
    extract_counts,
    reconcile_total,
    split_sentences,
)
from app.nts.identifiers import extract_authorizations, extract_nts
from app.nts.source import FrozenSource, match_receipt, stable_uuid

_SUBJECT = re.compile(
    r"(?P<subj>[A-Za-z0-9/][\w/ -]{2,40}?) (?:experiments|procedures) (?:were|was)\b"
)
_UNKNOWN = "unknown"
_GENERIC_SUBJECTS = {"all", "the", "these", "those", "both", "some", "most", "other", "further"}


@dataclass
class Proposal:
    finding_id: str
    kind: str  # nts-identifier | national-authorization | animal-counts
    case_id: str
    section_index: int
    window: tuple[int, int]  # inside the section text
    view_window: tuple[int, int]  # inside the composed view
    quote: str
    receipt: dict[str, Any]
    content: dict[str, Any]
    typed: dict[str, Any] = field(default_factory=dict)  # companion-only typed extractions
    observation_ids: list[str] = field(default_factory=list)


def _unique_window(
    frozen: FrozenSource, si: int, text: str, window: tuple[int, int]
) -> tuple[tuple[int, int], dict[str, Any]]:
    """Grow the quote backwards by whole sentences until it matches exactly one source location."""
    sents = split_sentences(text)
    start, end = window
    first = next(i for i, (a, b) in enumerate(sents) if a <= start < b)
    while True:
        quote = text[start:end].strip()
        receipt = match_receipt(frozen, quote)
        if receipt["status"] != "ambiguous" or first == 0:
            return (start, end), receipt
        first -= 1
        start = sents[first][0]


def _sentence_of(text: str, pos: int) -> tuple[int, int]:
    return next((a, b) for a, b in split_sentences(text) if a <= pos < b)


def _subject(sentence: str) -> str:
    m = _SUBJECT.search(sentence)
    subj = m.group("subj").strip() if m else _UNKNOWN
    return _UNKNOWN if subj.lower() in _GENERIC_SUBJECTS else subj


def _content(
    statement: str,
    population: str,
    numeric: list[dict] | None = None,
    origin: str = "source-explicit",
) -> dict[str, Any]:
    return {
        "statement": statement,
        "origin": origin,
        "context": {
            "population": population,
            "exposure": _UNKNOWN,
            "comparator": _UNKNOWN,
            "outcome": _UNKNOWN,
            "time": _UNKNOWN,
            "experimental_unit": _UNKNOWN,
        },
        "numeric_results": numeric or [],
    }


def _make(
    frozen: FrozenSource,
    case_id: str,
    kind: str,
    si: int,
    window: tuple[int, int],
    population: str,
    numeric: list[dict] | None = None,
    typed: dict | None = None,
    worded: str | None = None,
) -> Proposal:
    text = frozen.source["sections"][si]["text"]
    window, receipt = _unique_window(frozen, si, text, window)
    quote = text[window[0] : window[1]].strip()
    off = frozen.section_offset(si)
    if len(quote) > 2000:
        quote = quote[:2000]  # FindingContent.statement bound; receipt below is for the cut text
        receipt = match_receipt(frozen, quote)
        window = (window[0], window[0] + len(quote))
    # v1 states the quote itself; v2 words the claim and keeps the quote as its evidence only.
    if worded is None:
        content = _content(quote, population, numeric)
    else:
        content = _content(worded, population, numeric, origin="ai-paraphrase")
    FindingContent.model_validate(content)  # same validation Finding Curation applies
    key = f"{kind}|{si}|{window[0]}|{window[1]}"
    return Proposal(
        finding_id=str(stable_uuid("finding", case_id, key)),
        kind=kind,
        case_id=case_id,
        section_index=si,
        window=window,
        view_window=(off + window[0], off + window[1]),
        quote=quote,
        receipt=receipt,
        content=content,
        typed=typed or {},
    )


def _numeric_for(cluster: Cluster) -> list[dict[str, Any]]:
    """Exact numbers of a cluster as contract numeric results (distinct names, explicit n)."""
    out, seen = [], set()
    for o in cluster.observations:
        if o.number is None:
            continue
        label = o.cohort_label.split(" ")[0]
        name = f"{o.rule_id}:{o.sex}:{o.count_scope}:{label}".replace(" ", "_")
        base, k = name, 1
        while name in seen:
            k += 1
            name = f"{base}#{k}"
        seen.add(name)
        definition = {"article-total": "article-level", "per-group": "group"}.get(
            o.count_scope, "unknown"
        )
        out.append(
            {
                "name": name[:200],
                "value": float(o.number),
                "unit": "animals",
                "n": {"value": int(o.number), "definition": definition},
            }
        )
    return out


Statements = Literal["verbatim", "short"]

_AUTH_LABELS = {
    "apafis": "French APAFIS authorization",
    "uk-ppl": "UK Home Office project licence",
    "es-regional": "Spanish regional authorization",
    "ro-project-authorization": "Romanian project authorization",
    "ro-ethics-approval": "Romanian ethics approval",
    "ro-facility-authorization": "Romanian facility authorization",
}
_SCOPE_WORDS = {
    "article-total": "in total",
    "cohort-total": "in one cohort",
    "per-group": "per group",
    "additional-to-group": "in addition to the groups",
}
_MAX_COUNT_ITEMS = 8


def _for_population(population: str) -> str:
    return "" if population == _UNKNOWN else f", for {population}"


def _nts_statement(mentions: list, population: str) -> str:
    """One sentence naming each cited identifier as normalized, with the form the article used."""
    parts = []
    for m in mentions:
        part = m.normalized
        if m.version_cited:
            part += f" version {m.version_cited}"
        if m.date_cited:
            part += f" ({m.date_cited})"
        if m.verbatim.strip() != m.normalized:
            part += f', written "{m.verbatim.strip()}"'
        if part not in parts:
            parts.append(part)
    noun = "summary" if len(parts) == 1 else "summaries"
    return f"The article cites the ALURES non-technical {noun} {'; '.join(parts)}{_for_population(population)}."


def _authorization_statement(auths: list, population: str) -> str:
    parts = []
    for a in auths:
        part = f"{_AUTH_LABELS.get(a.kind, a.kind)} {a.value}"
        if part not in parts:
            parts.append(part)
    noun = "authorization" if len(parts) == 1 else "authorizations"
    return (
        f"The article cites the national {noun} {'; '.join(parts)}{_for_population(population)} "
        "(not an NTS identifier)."
    )


def _count_statement(cluster: Cluster, population: str) -> str:
    """The exact numbers of a cluster with their scope; repeated sentences are listed once."""
    items: list[str] = []
    for o in cluster.observations:
        if o.number is None:
            continue
        sex = "" if o.sex in ("unknown", "") else f" {o.sex.replace('+', ' and ')}"
        item = f"{o.number}{sex} {_SCOPE_WORDS.get(o.count_scope, o.count_scope)}"
        if o.derived:
            item += " (derived)"
        if item not in items:
            items.append(item)
    if not items:
        return f"Animal numbers are mentioned without an exact value{_for_population(population)}."
    shown = "; ".join(items[:_MAX_COUNT_ITEMS])
    if len(items) > _MAX_COUNT_ITEMS:
        shown += f"; and {len(items) - _MAX_COUNT_ITEMS} more (see the typed values)"
    who = "" if population == _UNKNOWN else f" ({population})"
    return f"Animal numbers reported{who}: {shown}."


def propose(
    frozen: FrozenSource, case_id: str, statements: Statements = "verbatim"
) -> dict[str, Any]:
    """All proposals, count observations and extraction diagnostics for one frozen article.

    ``statements="verbatim"`` (extractor v1) states each finding as its quoted passage;
    ``"short"`` (v2) words the claim in one machine sentence (origin ``ai-paraphrase``) and keeps
    the same passage as evidence. Windows, quotes, receipts and typed values are identical.
    """
    short = statements == "short"
    sections = frozen.source["sections"]
    article_ref = str(frozen.snapshot.article_id)
    proposals: list[Proposal] = []
    nts_found, auth_found = [], []

    for si, section in enumerate(sections):
        text = section["text"]
        by_sentence: dict[tuple[int, int], dict[str, list]] = {}
        for m in extract_nts(text):
            by_sentence.setdefault(_sentence_of(text, m.start), {"nts": [], "auth": []})[
                "nts"
            ].append(m)
            nts_found.append({"section_index": si, "mention": m})
        for a in extract_authorizations(text):
            by_sentence.setdefault(_sentence_of(text, a.start), {"nts": [], "auth": []})[
                "auth"
            ].append(a)
            auth_found.append({"section_index": si, "mention": a})
        for (a, b), hit in sorted(by_sentence.items()):
            sentence = text[a:b]
            population = _subject(sentence)
            if hit["nts"]:
                p = _make(
                    frozen,
                    case_id,
                    "nts-identifier",
                    si,
                    (a, b),
                    population,
                    typed={
                        "nts_mentions": [m.__dict__ for m in hit["nts"]],
                        "same_passage_authorizations": [x.__dict__ for x in hit["auth"]],
                    },
                    worded=_nts_statement(hit["nts"], population) if short else None,
                )
                proposals.append(p)
            elif hit["auth"]:
                p = _make(
                    frozen,
                    case_id,
                    "national-authorization",
                    si,
                    (a, b),
                    population,
                    typed={
                        "authorizations": [x.__dict__ for x in hit["auth"]],
                        "note": "national/institutional numbers; not NTS identifiers and not equal to one",
                    },
                    worded=_authorization_statement(hit["auth"], population) if short else None,
                )
                proposals.append(p)
            if hit["nts"] and hit["auth"]:
                proposals.append(
                    _make(
                        frozen,
                        case_id,
                        "national-authorization",
                        si,
                        (a, b),
                        population,
                        typed={
                            "authorizations": [x.__dict__ for x in hit["auth"]],
                            "note": "same sentence as an NTS identifier; kept as a separate finding because the two identifier systems are not interchangeable",
                        },
                        worded=_authorization_statement(hit["auth"], population) if short else None,
                    )
                )

    clusters = extract_counts(article_ref, sections)
    observations: list[CountObservation] = []
    for cl in clusters:
        first = cl.observations[0]
        pop = (
            _UNKNOWN
            if first.species == _UNKNOWN
            else f"{first.species}" + ("" if first.strain == _UNKNOWN else f" ({first.strain})")
        )
        p = _make(
            frozen,
            case_id,
            "animal-counts",
            cl.section_index,
            cl.window,
            pop,
            numeric=_numeric_for(cl),
            typed={"cohort": cl.section_title},
            worded=_count_statement(cl, pop) if short else None,
        )
        p.observation_ids = [o.observation_id for o in cl.observations]
        proposals.append(p)
        for o in cl.observations:
            o.window = p.window  # the quoted (possibly widened) window
            observations.append(o)

    # idempotent: identical (kind, section, window) collapses to one proposal
    unique: dict[str, Proposal] = {}
    for p in proposals:
        key = f"{p.kind}|{p.section_index}|{p.window}|{sorted(a['value'] for a in p.typed.get('authorizations', []))}"
        unique.setdefault(key, p)
    return {
        "proposals": list(unique.values()),
        "nts_mentions": nts_found,
        "authorizations": auth_found,
        "observations": observations,
        "reconciliation": reconcile_total(clusters),
        "missing_counts": not observations,
    }
