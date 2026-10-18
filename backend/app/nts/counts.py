"""Deterministic extraction of animal-count observations from article text.

Rules read only numbers that are written in the passage. A figure computed from written
numbers (groups x animals per group) is flagged ``derived`` with its formula, and never
presented as reported. Species, strain, sex and cohort come from the same passage or section;
what the passage does not say stays ``unknown``. Nothing here asserts that two counts refer to
the same animals: overlap is only flagged.
"""

# ruff: noqa: E501  (long diagnostic strings and regexes are kept readable, not wrapped)

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.nts.source import stable_uuid

COUNT_CATEGORIES = (
    "NTS_planned_or_authorized",
    "RAR_actual_used",
    "article_reported",
    "breeding",
    "control",
    "experimental",
    "unknown",
)
NUMBER_WORDS = {
    w: i
    for i, w in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"), 0
    )
}
_NUM = r"(?:\d+|" + "|".join(NUMBER_WORDS) + r")"
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z‘“(½])|\n+")
_SPECIES = re.compile(r"\b(rats?|mice|mouse)\b", re.IGNORECASE)
_STRAIN = re.compile(
    r"\b(Wistar|Sprague[- ]Dawley|C57BL/6[JN]?|db/db|OF1|CD1|BALB/c)\b", re.IGNORECASE
)

_TOTAL = re.compile(r"\bA total of (?P<n>\d+)\b(?P<tail>[^.]{0,60})")
_IN_SPECIES = re.compile(r"\bin (?P<n>\d+) (?P<sp>rats?|mice|mouse)\b", re.IGNORECASE)
_PAIR = re.compile(
    rf"(?P<m>{_NUM}) (?P<add>additional )?males? and (?P<f>{_NUM}) (?:additional )?females?"
    r"(?: per (?P<per>[a-z]+))?",
    re.IGNORECASE,
)
_PAIR_FM = re.compile(
    rf"\((?P<f>{_NUM}) females? \+ (?P<m>{_NUM}) males?(?: per (?P<per>[a-z]+))?(?:;\s*total\s*n?\s*=\s*(?P<tot>\d+))?",
    re.IGNORECASE,
)
_GROUPS = re.compile(
    rf"\b(?P<g>{_NUM}) (?:active treatment |separate )?(?:doses|groups)\b", re.IGNORECASE
)
_NAMED_GROUP = re.compile(r"the (?P<g>[\d.]+ mg/kg) group")
_BREEDING = re.compile(r"\bbreeding (?:pairs?|colony|stock)\b", re.IGNORECASE)


def to_int(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token.lower()]


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Half-open sentence spans (no stripping of internal text)."""
    spans, cursor = [], 0
    for m in _SENT.finditer(text):
        if m.start() > cursor:
            spans.append((cursor, m.start()))
        cursor = m.end()
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return spans


@dataclass
class CountObservation:
    observation_id: str
    article_ref: str
    count_category: str
    source_kind: str  # article | alures | rar
    species: str
    strain: str
    number: int | None
    unit_counted: str
    sex: str
    cohort_label: str
    count_scope: str  # article-total | cohort-total | per-group | additional-to-group
    group_count: int | None
    derived: bool
    derivation: str | None
    method_section: str
    section_index: int
    window: tuple[int, int]
    rule_id: str
    possible_overlap: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["window"] = list(self.window)
        return d


@dataclass
class Cluster:
    """Observations of one section sharing one contiguous sentence window."""

    section_index: int
    section_title: str
    window: tuple[int, int]
    quote: str
    observations: list[CountObservation]


def _species(sentence: str, section: str) -> tuple[str, str]:
    m = _SPECIES.search(sentence) or _SPECIES.search(section)
    word = m.group(1).lower() if m else ""
    species = {"rat": "rat", "rats": "rat", "mice": "mouse", "mouse": "mouse"}.get(word, "unknown")
    s = _STRAIN.search(sentence) or _STRAIN.search(section)
    return species, (s.group(1) if s else "unknown")


def _role(sentence: str, before: str) -> str:
    return "control" if re.search(r"\bcontrols?\b", before, re.IGNORECASE) else "experimental"


def extract_counts(article_ref: str, sections: list[dict[str, Any]]) -> list[Cluster]:
    clusters: list[Cluster] = []
    for si, section in enumerate(sections):
        text, title = section["text"], section["title"]
        sentences = split_sentences(text)
        hits: list[tuple[int, CountObservation]] = []
        seq = 0

        def obs(sidx: int, *, _sents=sentences, _si=si, _title=title, **kw) -> CountObservation:
            nonlocal seq
            seq += 1
            a, b = _sents[sidx]
            return CountObservation(
                observation_id=str(
                    stable_uuid("obs", article_ref, str(_si), str(seq), kw["rule_id"])
                ),
                article_ref=article_ref,
                source_kind="article",
                method_section=_title,
                section_index=_si,
                window=(a, b),
                **kw,
            )

        for sidx, (a, b) in enumerate(sentences):
            sent = text[a:b]
            species, strain = _species(sent, text)
            groups = _GROUPS.search(sent)
            if m := _TOTAL.search(sent):
                sp_noun = _SPECIES.search(m.group("tail"))
                hits.append(
                    (
                        sidx,
                        obs(
                            sidx,
                            count_category="article_reported",
                            species=species,
                            strain=strain,
                            number=int(m.group("n")),
                            unit_counted="animals"
                            if sp_noun
                            else "unknown (counted noun not in passage)",
                            sex="mixed (as stated)"
                            if re.search(r"males and females", sent)
                            else "unknown",
                            cohort_label="article-total",
                            count_scope="article-total",
                            group_count=None,
                            derived=False,
                            derivation=None,
                            rule_id="total-of-N",
                            notes=[]
                            if sp_noun
                            else [
                                "the counted noun is absent from the sentence; species taken from section context"
                            ],
                        ),
                    )
                )
            if m := _IN_SPECIES.search(sent):
                hits.append(
                    (
                        sidx,
                        obs(
                            sidx,
                            count_category=_role(sent, sent[: m.start()]),
                            species=species,
                            strain=strain,
                            number=int(m.group("n")),
                            unit_counted="animals",
                            sex="unknown",
                            cohort_label=title,
                            count_scope="cohort-total",
                            group_count=None,
                            derived=False,
                            derivation=None,
                            rule_id="performed-in-N-species",
                        ),
                    )
                )
            for m in _PAIR.finditer(sent):
                mm, ff = to_int(m.group("m")), to_int(m.group("f"))
                additional = bool(m.group("add")) or bool(
                    re.search(r"additional", sent[max(0, m.start() - 12) : m.start()])
                )
                before = sent[: m.start()]
                role = _role(sent, before)
                if additional:
                    target = None
                    for ng in _NAMED_GROUP.finditer(before):
                        target = ng.group("g")
                    label = f"{title} / {target or 'unnamed group'} (additional animals)"
                    for sex, n in (("male", mm), ("female", ff)):
                        hits.append(
                            (
                                sidx,
                                obs(
                                    sidx,
                                    count_category=role,
                                    species=species,
                                    strain=strain,
                                    number=n,
                                    unit_counted="animals",
                                    sex=sex,
                                    cohort_label=label,
                                    count_scope="additional-to-group",
                                    group_count=1,
                                    derived=False,
                                    derivation=None,
                                    rule_id="additional-males-and-females",
                                ),
                            )
                        )
                    continue
                g_named = (
                    len(re.findall(r"\bgroup\b", before))
                    if re.search(r"\beach\b", sent[m.start() - 30 : m.start()])
                    else 0
                )
                if groups and groups.start() < m.start():
                    g, g_rule = to_int(groups.group("g")), "count-word-before-pair"
                elif g_named >= 2:
                    g, g_rule = g_named, "named-groups-before-each"
                elif g_named == 1:
                    g, g_rule = 1, "named-groups-before-each"
                else:
                    g, g_rule = None, None
                label = title + (" (controls)" if role == "control" else "")
                for sex, n in (("male", mm), ("female", ff)):
                    hits.append(
                        (
                            sidx,
                            obs(
                                sidx,
                                count_category=role,
                                species=species,
                                strain=strain,
                                number=n,
                                unit_counted="animals",
                                sex=sex,
                                cohort_label=label,
                                count_scope="per-group",
                                group_count=g,
                                derived=False,
                                derivation=None,
                                rule_id="males-and-females-per-group",
                                notes=[f"per {m.group('per')}"] if m.group("per") else [],
                            ),
                        )
                    )
                if g is not None:
                    hits.append(
                        (
                            sidx,
                            obs(
                                sidx,
                                count_category=role,
                                species=species,
                                strain=strain,
                                number=g * (mm + ff),
                                unit_counted="animals",
                                sex="male+female",
                                cohort_label=label,
                                count_scope="cohort-total",
                                group_count=g,
                                derived=True,
                                derivation=f"{g} groups x ({mm} males + {ff} females) [{g_rule}]",
                                rule_id="derived-cohort-total",
                            ),
                        )
                    )
            if m := _PAIR_FM.search(sent):
                ff, mm = to_int(m.group("f")), to_int(m.group("m"))
                notes = [f"per {m.group('per')}"] if m.group("per") else []
                if m.group("tot") is not None:
                    tot = int(m.group("tot"))
                    notes.append(
                        f"stated total n={tot}; sex split sums to {ff + mm}: "
                        + ("consistent" if tot == ff + mm else "INCONSISTENT")
                    )
                for sex, n in (("female", ff), ("male", mm)):
                    hits.append(
                        (
                            sidx,
                            obs(
                                sidx,
                                count_category="article_reported",
                                species=species,
                                strain=strain,
                                number=n,
                                unit_counted="animals",
                                sex=sex,
                                cohort_label=title,
                                count_scope="per-group",
                                group_count=None,
                                derived=False,
                                derivation=None,
                                rule_id="females-plus-males-per-group",
                                notes=notes,
                                possible_overlap=[
                                    "results restatement: mapping to methods cohorts not stated"
                                ],
                            ),
                        )
                    )
            if m := _BREEDING.search(sent):
                num = re.search(rf"(?P<n>\d+) [^.]{{0,30}}{re.escape(m.group(0))}", sent)
                if num:
                    hits.append(
                        (
                            sidx,
                            obs(
                                sidx,
                                count_category="breeding",
                                species=species,
                                strain=strain,
                                number=int(num.group("n")),
                                unit_counted="animals",
                                sex="unknown",
                                cohort_label=title,
                                count_scope="cohort-total",
                                group_count=None,
                                derived=False,
                                derivation=None,
                                rule_id="breeding-stock",
                            ),
                        )
                    )
        if not hits:
            continue
        first, last = min(h[0] for h in hits), max(h[0] for h in hits)
        window = (sentences[first][0], sentences[last][1])
        cluster_obs = [h[1] for h in hits]
        for o in cluster_obs:
            o.window = window
        clusters.append(Cluster(si, title, window, text[window[0] : window[1]], cluster_obs))
    _fill_article_context(clusters)
    _flag_overlap(clusters, sections)
    return clusters


def _fill_article_context(clusters: list[Cluster]) -> None:
    """Species/strain missing from a cluster's own section: use the article's only stated one."""
    for field_name in ("species", "strain"):
        known = {getattr(o, field_name) for c in clusters for o in c.observations} - {"unknown"}
        if len(known) != 1:
            continue
        value = next(iter(known))
        for c in clusters:
            for o in c.observations:
                if getattr(o, field_name) == "unknown":
                    setattr(o, field_name, value)
                    o.notes.append(
                        f"{field_name} {value!r} taken from other sections of the same article (not in this section)"
                    )


def _flag_overlap(clusters: list[Cluster], sections: list[dict[str, Any]]) -> None:
    """Phrase-triggered overlap flags. They record doubt; they never merge or subtract counts."""
    for c in clusters:
        text = c.quote
        reported = [o for o in c.observations if o.rule_id == "performed-in-N-species"]
        derived = [o for o in c.observations if o.rule_id == "derived-cohort-total"]
        for r in reported:
            for d in derived:
                same = "equals" if r.number == d.number else "DIFFERS FROM"
                msg = f"stated cohort N={r.number} {same} derived {d.number} ({d.derivation})"
                r.notes.append(msg)
                d.notes.append(msg)
        for o in c.observations:
            if "expanded with" in text and o.count_scope == "additional-to-group":
                o.possible_overlap.append(
                    "extends an earlier group of the same dose; the group's base animals and these additional animals are one cohort, not two"
                )
            if "dose selected for the main study" in text:
                o.possible_overlap.append(
                    "the passage says this dose was selected for the main study; whether these animals are also counted in the main-study groups is not stated"
                )
            if o.count_category == "control" and o.count_scope == "per-group":
                o.notes.append(
                    "control cohort counted separately from treatment groups in the passage"
                )


def reconcile_total(clusters: list[Cluster]) -> dict[str, Any]:
    """Arithmetic comparison of the stated article total with stated methods cohorts."""
    total = next(
        (o for c in clusters for o in c.observations if o.count_scope == "article-total"), None
    )
    cohorts = [
        o
        for c in clusters
        for o in c.observations
        if o.count_scope == "cohort-total"
        and o.rule_id in ("derived-cohort-total", "performed-in-N-species")
        and c.section_title.startswith("4.")
    ]
    additional = [
        o for c in clusters for o in c.observations if o.count_scope == "additional-to-group"
    ]
    # a stated "performed in N" and its own derived total describe one cohort: count it once
    by_label: dict[str, int] = {}
    for o in cohorts:
        by_label.setdefault(o.cohort_label, o.number or 0)
    stated = sum(by_label.values()) + sum(o.number or 0 for o in additional)
    if total is None or total.number is None:
        return {"status": "no-article-total", "sum_of_stated_cohorts": stated}
    diff = total.number - stated
    return {
        "status": "exact"
        if diff == 0
        else "cohort-sum-below-total"
        if diff > 0
        else "cohort-sum-above-total",
        "article_reported_total": total.number,
        "sum_of_stated_cohorts": stated,
        "unattributed_animals": diff,
        "caveat": "Stated cohorts may overlap (see possible_overlap) or omit cohorts the passage does not describe; the difference is not attributed to any group.",
    }
