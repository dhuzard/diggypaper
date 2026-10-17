"""Pure, manifest-first SEIP scoring; no source verification or scientific adjudication.

All matches here are exact typed value/unit/scope comparisons under an externally
approved codebook. ``None`` means NA, never zero or perfection. Evidence judgments
are supplied independently; quote existence never implies support. See
``docs/protocols/seip-scoring-contract-v0.1.md`` for the bounded contract and human gates.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from random import Random
from typing import Literal

CONTRACT_VERSION = "seip-scoring/v0.1"
REFERENCE_STATES = {"reported", "not_reported", "not_accessible", "not_applicable", "unclear"}
ELIGIBLE_STATES = {"reported", "not_reported"}
CALL_STATES = {"valid", "missing_call", "invalid_json", "invalid_schema", "error"}
QUOTE_STATES = {
    "exact",
    "normalized_only",
    "absent",
    "malformed",
    "empty",
    "source_unavailable",
    "not_assessed",
}
ATTRIBUTION_STATES = {"correct", "incorrect", "ambiguous", "unavailable", "not_assessed"}
SUPPORT_STATES = {"supports", "contradicts", "insufficient", "unclear", "not_assessed"}


@dataclass(frozen=True)
class FieldSpec:
    key: str
    kind: Literal["categorical", "numeric", "binary"]


@dataclass(frozen=True)
class PaperAttempt:
    """One paper in an archived attempt manifest, including unsuccessful calls.

    Retries must be resolved by a frozen external choice rule; never supply two
    records for a paper. Development/examples remain inspectable but unscored.
    """

    paper_id: str
    stratum: str
    split: Literal["held_out", "development"] = "held_out"
    eligible: bool = True
    example: bool = False


@dataclass(frozen=True)
class Manifest:
    protocol_version: str
    codebook_version: str
    configuration_id: str
    papers: tuple[PaperAttempt, ...]
    fields: tuple[FieldSpec, ...]
    seed: int
    attempt_manifest_id: str
    attempt_manifest_sha256: str
    reference_set_id: str
    bootstrap_resamples: int = 2000
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True)
class Observation:
    value: str | int | bool
    scope: tuple[tuple[str, str], ...] = ()
    unit: str | None = None

    def key(self) -> tuple:
        # bool and int compare equal in Python: their types must be part of the key.
        return type(self.value).__name__, self.value, self.unit, tuple(sorted(self.scope))


@dataclass(frozen=True)
class ReferenceField:
    status: str
    observations: tuple[Observation, ...] = ()


@dataclass(frozen=True)
class PredictedField:
    status: str
    observations: tuple[Observation, ...] = ()
    answer_revision: str | None = None


@dataclass(frozen=True)
class PaperOutput:
    state: str = "valid"
    fields: Mapping[str, PredictedField] = field(default_factory=dict)


@dataclass(frozen=True)
class QuoteJudgment:
    """One proposed evidence record, with independently supplied outcomes.

    ``emitted_nonempty`` defines the verbatim denominator, including malformed
    nonempty emissions. Empty records still count in attribution. A stated
    existence claim needs its own independent test, not the producer's verdict.
    """

    evidence_id: str
    existence: str = "not_assessed"
    attribution: str = "not_assessed"
    emitted_nonempty: bool = True
    claimed_present: Literal["exact", "normalized"] | None = None
    claim_passes: bool | None = None
    claimed_unique: bool = False
    unique_correct: bool | None = None
    known_negative_challenge: bool = False


@dataclass(frozen=True)
class LinkJudgment:
    """One proposed scoped answer-passage link, including invalid proposals.

    IDs retain identity only; correctness/support are supplied adjudications,
    not inferred by this scorer. Links may reference absent answers/evidence:
    retain those failed proposals with correctness='incorrect'.
    """

    link_id: str
    field_key: str
    evidence_id: str
    correctness: Literal["correct", "incorrect", "not_assessed"] = "not_assessed"
    support: str = "not_assessed"
    negation_conflict_challenge: bool = False
    challenge_eligible: bool = False
    presented_supporting: bool = False
    answer_revision: str | None = None
    answer_scope: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EvidenceJudgments:
    quotes: tuple[QuoteJudgment, ...] = ()
    links: tuple[LinkJudgment, ...] = ()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_field(spec: FieldSpec, record: ReferenceField | PredictedField) -> None:
    allowed = REFERENCE_STATES | ({"invalid"} if isinstance(record, PredictedField) else set())
    _require(record.status in allowed, f"Unknown status for {spec.key}")
    if isinstance(record, PredictedField) and record.status != "invalid":
        _require(
            isinstance(record.answer_revision, str) and bool(record.answer_revision),
            "Usable answers require an explicit revision identity",
        )
    _require(
        record.status == "reported" or not record.observations,
        "Only reported fields may contain observations",
    )
    if isinstance(record, ReferenceField) and record.status == "reported":
        _require(bool(record.observations), "Reported reference needs an observation")
    scopes = []
    for observation in record.observations:
        _require(isinstance(observation, Observation), "Expected Observation")
        scope = tuple(sorted(observation.scope))
        _require(
            all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in scope),
            "Scope must have nonempty string keys and values",
        )
        _require(len({k for k, _ in scope}) == len(scope), "Duplicate scope dimension")
        scopes.append(scope)
        if spec.kind == "binary":
            _require(
                type(observation.value) is bool and observation.unit is None,
                "Binary observations need bool values without units",
            )
        elif spec.kind == "categorical":
            _require(
                type(observation.value) is str
                and bool(observation.value)
                and observation.unit is None,
                "Categorical observations need nonempty strings",
            )
        else:
            _require(
                type(observation.value) in {int, str}
                and observation.value != ""
                and isinstance(observation.unit, str)
                and bool(observation.unit),
                "Numeric observations need an exact integer/range and explicit unit",
            )
    if spec.kind == "binary":
        _require(len(scopes) == len(set(scopes)), "Binary observations need unique scopes")


def _validate(manifest, references, outputs, evidence):
    _require(manifest.contract_version == CONTRACT_VERSION, "Unsupported scoring contract")
    _require(
        all(
            (
                manifest.protocol_version,
                manifest.codebook_version,
                manifest.configuration_id,
                manifest.attempt_manifest_id,
                manifest.reference_set_id,
            )
        ),
        "Protocol, codebook, configuration, attempt and reference identities are required",
    )
    _require(type(manifest.seed) is int, "Seed must be an integer")
    _require(
        isinstance(manifest.attempt_manifest_sha256, str)
        and len(manifest.attempt_manifest_sha256) == 64
        and all(c in "0123456789abcdef" for c in manifest.attempt_manifest_sha256),
        "Attempt manifest requires a lowercase SHA-256 digest",
    )
    _require(
        type(manifest.bootstrap_resamples) is int and manifest.bootstrap_resamples > 0,
        "Bootstrap resamples must be positive",
    )
    paper_ids = [p.paper_id for p in manifest.papers]
    _require(all(paper_ids) and len(set(paper_ids)) == len(paper_ids), "Duplicate/empty paper ID")
    specs = {f.key: f for f in manifest.fields}
    _require(len(specs) == len(manifest.fields) and all(specs), "Duplicate/empty field key")
    _require(
        all(f.kind in {"binary", "categorical", "numeric"} for f in specs.values()),
        "Unknown field kind",
    )
    for paper in manifest.papers:
        _require(
            paper.split in {"held_out", "development"} and bool(paper.stratum),
            "Invalid paper split/stratum",
        )
        _require(
            type(paper.eligible) is bool and type(paper.example) is bool,
            "Paper eligibility/example flags must be boolean",
        )
    for records in (references, outputs, evidence):
        _require(not (set(records) - set(paper_ids)), "Paper outside attempted manifest")
    for fields in references.values():
        _require(not (set(fields) - set(specs)), "Unknown reference field")
        for key, reference in fields.items():
            _require(isinstance(reference, ReferenceField), "Expected ReferenceField")
            _validate_field(specs[key], reference)
    for output in outputs.values():
        _require(output.state in CALL_STATES, "Unknown call state")
        _require(output.state == "valid" or not output.fields, "Failed calls cannot contain fields")
        _require(not (set(output.fields) - set(specs)), "Unknown output field")
        for key, prediction in output.fields.items():
            _require(isinstance(prediction, PredictedField), "Expected PredictedField")
            _validate_field(specs[key], prediction)
    for paper_id, judgments in evidence.items():
        quote_ids = [q.evidence_id for q in judgments.quotes]
        link_ids = [link.link_id for link in judgments.links]
        _require(
            all(quote_ids) and len(quote_ids) == len(set(quote_ids)), "Duplicate/empty evidence ID"
        )
        _require(all(link_ids) and len(link_ids) == len(set(link_ids)), "Duplicate/empty link ID")
        for quote in judgments.quotes:
            _require(
                type(quote.emitted_nonempty) is bool
                and type(quote.claimed_unique) is bool
                and type(quote.known_negative_challenge) is bool,
                "Quote flags must be boolean",
            )
            _require(
                type(quote.claim_passes) in {bool, type(None)}
                and type(quote.unique_correct) in {bool, type(None)},
                "Independent checks must be boolean or unassessed",
            )
            _require(quote.existence in QUOTE_STATES, "Unknown quote state")
            _require(quote.attribution in ATTRIBUTION_STATES, "Unknown attribution state")
            _require(
                quote.emitted_nonempty == (quote.existence != "empty"),
                "Only empty quotations may have emitted_nonempty=False",
            )
            _require(
                quote.claimed_present in {None, "exact", "normalized"}, "Unknown present claim"
            )
            _require(
                quote.claimed_present is not None or quote.claim_passes is None,
                "Claim test requires a claim",
            )
            _require(
                quote.claimed_unique or quote.unique_correct is None,
                "Unique test requires a unique claim",
            )
            if quote.claim_passes is not None:
                _require(
                    quote.existence != "source_unavailable",
                    "Unavailable sources cannot have an assessed existence claim check",
                )
                expected = {
                    "exact": True,
                    "normalized_only": quote.claimed_present == "normalized",
                    "absent": False,
                    "malformed": False,
                    "empty": False,
                }.get(quote.existence)
                _require(
                    expected is None or quote.claim_passes is expected,
                    "Independent existence and claim check judgments contradict",
                )
            _require(
                quote.unique_correct is not True
                or quote.attribution not in {"ambiguous", "incorrect", "unavailable"},
                "Independent attribution and unique claim check judgments contradict",
            )
        for link in judgments.links:
            _require(
                type(link.negation_conflict_challenge) is bool
                and type(link.challenge_eligible) is bool
                and type(link.presented_supporting) is bool,
                "Link flags must be boolean",
            )
            _require(link.field_key in specs, "Unknown link field")
            _require(
                link.correctness in {"correct", "incorrect", "not_assessed"},
                "Unknown link correctness",
            )
            _require(link.support in SUPPORT_STATES, "Unknown support judgment")
            _require(
                isinstance(link.answer_revision, str) and bool(link.answer_revision),
                "Links require an explicit answer revision",
            )
            _require(
                all(
                    isinstance(k, str) and k and isinstance(v, str) and v
                    for k, v in link.answer_scope
                ),
                "Invalid link answer scope",
            )
            _require(
                len({k for k, _ in link.answer_scope}) == len(link.answer_scope),
                "Duplicate link scope dimension",
            )
            _require(
                not link.challenge_eligible or link.negation_conflict_challenge,
                "Challenge eligibility requires a challenge",
            )
            if link.correctness == "correct":
                _require(
                    link.evidence_id in quote_ids,
                    "Correct links require a supplied evidence record",
                )
                output = outputs.get(paper_id)
                answer = output.fields.get(link.field_key) if output else None
                _require(
                    output is not None
                    and output.state == "valid"
                    and answer is not None
                    and answer.status != "invalid",
                    "Correct links require a usable answer",
                )
                _require(
                    link.answer_revision == answer.answer_revision
                    and tuple(sorted(link.answer_scope)) in _answer_scopes(answer),
                    "Correct links require the current scoped answer revision",
                )


def _answer_scopes(answer):
    return {tuple(sorted(o.scope)) for o in answer.observations} or {()}


def _binary(counts: Counter, reference: tuple, predicted: tuple) -> None:
    gold = {tuple(sorted(o.scope)): o.value for o in reference}
    proposals = {tuple(sorted(o.scope)): o.value for o in predicted}
    for scope, truth in gold.items():
        if scope not in proposals:
            counts["binary_missing_observations"] += 1
            counts[f"binary_{'positive' if truth else 'negative'}_fn"] += 1
            continue
        answer = proposals[scope]
        for label, target in (("positive", True), ("negative", False)):
            outcome = (
                ("tp" if answer == target else "fn")
                if truth == target
                else ("fp" if answer == target else "tn")
            )
            counts[f"binary_{label}_{outcome}"] += 1
    for scope in proposals.keys() - gold.keys():
        counts["binary_extra_observations"] += 1
        counts[f"binary_{'positive' if proposals[scope] else 'negative'}_fp"] += 1


def _score_field(spec, reference, prediction, call_state):
    counts = Counter({"paper_fields": 1})
    if reference is None:
        counts["missing_reference"] = 1
        counts["unscored_proposals"] = len(prediction.observations) if prediction else 0
        return counts
    counts[f"reference_{reference.status}"] += 1
    if reference.status not in ELIGIBLE_STATES:
        counts["excluded_reference"] += 1
        counts["unscored_proposals"] = len(prediction.observations) if prediction else 0
        return counts
    counts["eligible_fields"] = 1
    missing = (
        call_state
        if call_state != "valid"
        else (
            "missing_field"
            if prediction is None
            else "invalid_field"
            if prediction.status == "invalid"
            else None
        )
    )
    predicted_status = prediction.status if missing is None else "missing"
    counts[f"reporting_{reference.status}__{predicted_status}"] += 1
    if missing:
        counts[f"eligible_{missing}"] += 1
        counts["missing_output_fields"] += 1
    else:
        counts["covered_fields"] += 1
        counts["correct_reporting_status"] += predicted_status == reference.status
    proposed = prediction.observations if missing is None else ()
    gold = reference.observations
    ref_keys, pred_keys = Counter(o.key() for o in gold), Counter(o.key() for o in proposed)
    matches = sum((ref_keys & pred_keys).values())
    prefix = spec.kind
    counts[f"{prefix}_tp"] += matches
    counts[f"{prefix}_fp"] += len(proposed) - matches
    counts[f"{prefix}_fn"] += len(gold) - matches
    if spec.kind == "binary":
        _binary(counts, gold, proposed)
    if spec.kind == "numeric":
        unmatched = pred_keys - ref_keys
        for observation in proposed:
            if unmatched[observation.key()] <= 0:
                continue
            unmatched[observation.key()] -= 1
            counts["numeric_unit_or_scope_errors"] += any(
                type(o.value) is type(observation.value)
                and o.value == observation.value
                and (o.unit, sorted(o.scope)) != (observation.unit, sorted(observation.scope))
                for o in gold
            )
            counts["numeric_value_errors"] += any(
                (o.unit, sorted(o.scope)) == (observation.unit, sorted(observation.scope))
                and (type(o.value), o.value) != (type(observation.value), observation.value)
                for o in gold
            )
    return counts


def _score_evidence(judgments, covered_fields):
    counts = Counter()
    counts["eligible_answers"] = len(covered_fields)
    if judgments is None:
        counts["eligible_answers_evidence_unavailable"] = len(covered_fields)
        return counts
    for quote in judgments.quotes:
        counts["proposed_evidence"] += 1
        counts["emitted_nonempty_quotes"] += quote.emitted_nonempty
        counts[f"quote_{quote.existence}"] += 1
        counts[f"attribution_{quote.attribution}"] += 1
        counts["unverifiable_quotes"] += (
            quote.existence in {"absent", "empty", "malformed", "source_unavailable"}
            or quote.attribution == "ambiguous"
        )
        if quote.claimed_present:
            prefix = f"present_claim_{quote.claimed_present}"
            counts[prefix] += 1
            counts[f"{prefix}_false"] += quote.claim_passes is False
            counts[f"{prefix}_unassessed"] += quote.claim_passes is None
        if quote.claimed_unique:
            # Independent ambiguity can refute the producer's uniqueness assertion.
            counts["unique_claims"] += 1
            counts["unique_claim_false"] += quote.unique_correct is False
            counts["unique_claim_unassessed"] += quote.unique_correct is None
        elif quote.attribution == "ambiguous":
            counts["ambiguous_existence_without_unique_claim"] += 1
        if quote.known_negative_challenge:
            counts["known_negative_quotes"] += 1
            counts["known_negative_false_acceptances"] += bool(quote.claimed_present)
    linked, correctly_linked = set(), set()
    for link in judgments.links:
        counts["proposed_links"] += 1
        counts[f"link_{link.correctness}"] += 1
        counts[f"support_{link.support}"] += 1
        identity = (link.field_key, link.answer_revision, tuple(sorted(link.answer_scope)))
        linked.add(identity)
        if link.correctness == "correct":
            correctly_linked.add(identity)
        adjudicated = link.support != "not_assessed"
        counts["support_adjudicated"] += adjudicated
        counts["support_determinate"] += link.support in {"supports", "contradicts", "insufficient"}
        if link.negation_conflict_challenge:
            counts["challenge_links"] += 1
            counts["challenge_eligible"] += link.challenge_eligible
            counts["challenge_adjudicated"] += adjudicated and link.challenge_eligible
            counts["challenge_unassessed"] += not adjudicated
            counts["challenge_errors"] += (
                adjudicated
                and link.challenge_eligible
                and link.presented_supporting
                and link.support in {"contradicts", "insufficient"}
            )
    counts["eligible_answers_zero_links"] = len(covered_fields - linked)
    counts["eligible_answers_correct_link"] = len(covered_fields & correctly_linked)
    return counts


def _rate(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _metrics(counts):
    c = counts
    rates = {
        "eligible_field_coverage": _rate(c["covered_fields"], c["eligible_fields"]),
        "reporting_status_correctness": _rate(c["correct_reporting_status"], c["eligible_fields"]),
        "verbatim_quote_rate": _rate(c["quote_exact"], c["emitted_nonempty_quotes"]),
        "verified_attribution": _rate(c["attribution_correct"], c["proposed_evidence"]),
        "answer_link_correctness": _rate(c["link_correct"], c["proposed_links"]),
        "answer_correct_link_coverage": _rate(
            c["eligible_answers_correct_link"], c["eligible_answers"]
        ),
        "scientific_support_error": _rate(
            c["support_contradicts"] + c["support_insufficient"], c["support_adjudicated"]
        ),
        "support_assessment_coverage": _rate(c["support_adjudicated"], c["proposed_links"]),
        "support_determinate_coverage": _rate(c["support_determinate"], c["proposed_links"]),
        "scientific_support_error_determinate_only": _rate(
            c["support_contradicts"] + c["support_insufficient"], c["support_determinate"]
        ),
        "negation_conflict_challenge_error": _rate(
            c["challenge_errors"], c["challenge_adjudicated"]
        ),
        "false_unique_attribution": _rate(
            c["unique_claim_false"], c["unique_claims"] - c["unique_claim_unassessed"]
        ),
        "false_unique_attribution_all_labelled_lower_bound": _rate(
            c["unique_claim_false"], c["unique_claims"]
        ),
        "unique_claim_assessment_coverage": _rate(
            c["unique_claims"] - c["unique_claim_unassessed"], c["unique_claims"]
        ),
        "known_negative_false_acceptance": _rate(
            c["known_negative_false_acceptances"], c["known_negative_quotes"]
        ),
        "unverifiable_quote_rate": _rate(c["unverifiable_quotes"], c["proposed_evidence"]),
    }
    for mode in ("exact", "normalized"):
        rates[f"false_existence_verification_{mode}"] = _rate(
            c[f"present_claim_{mode}_false"],
            c[f"present_claim_{mode}"] - c[f"present_claim_{mode}_unassessed"],
        )
        rates[f"false_existence_verification_{mode}_all_labelled_lower_bound"] = _rate(
            c[f"present_claim_{mode}_false"], c[f"present_claim_{mode}"]
        )
        rates[f"present_claim_{mode}_assessment_coverage"] = _rate(
            c[f"present_claim_{mode}"] - c[f"present_claim_{mode}_unassessed"],
            c[f"present_claim_{mode}"],
        )
    for kind in ("categorical", "numeric", "binary", "binary_positive", "binary_negative"):
        tp, fp, fn = (c[f"{kind}_{outcome}"] for outcome in ("tp", "fp", "fn"))
        rates[f"{kind}_precision"] = _rate(tp, tp + fp)
        rates[f"{kind}_recall"] = _rate(tp, tp + fn)
        rates[f"{kind}_f1"] = _rate(2 * tp, 2 * tp + fp + fn)
    return rates


def _aggregate(papers, field_keys):
    counts = Counter()
    fields = {key: Counter() for key in field_keys}
    for paper in papers:
        counts.update(paper["counts"])
        for key in field_keys:
            fields[key].update(paper["fields"][key])
    # Explicit zeros make confusion counts inspectable even for empty/single-class sets.
    for counter in (counts, *fields.values()):
        for prefix, states in (
            ("quote", QUOTE_STATES),
            ("attribution", ATTRIBUTION_STATES),
            ("support", SUPPORT_STATES),
            ("reference", REFERENCE_STATES),
        ):
            for state in states:
                counter.setdefault(f"{prefix}_{state}", 0)
        for kind in ("categorical", "numeric", "binary", "binary_positive", "binary_negative"):
            for outcome in ("tp", "fp", "fn", "tn"):
                counter.setdefault(f"{kind}_{outcome}", 0)
        for reason in (
            "missing_call",
            "invalid_json",
            "invalid_schema",
            "error",
            "missing_field",
            "invalid_field",
        ):
            counter.setdefault(f"eligible_{reason}", 0)
    return {
        "counts": dict(sorted(counts.items())),
        "metrics": _metrics(counts),
        "by_field": {
            key: {"counts": dict(sorted(c.items())), "metrics": _metrics(c)}
            for key, c in fields.items()
        },
    }


def _quantile(values, fraction):
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _bootstrap(papers, manifest, field_key=None):
    """Percentile intervals, sampling whole papers, including zero-output/link clusters."""
    rng = Random(manifest.seed)
    samples = {name: [] for name in _metrics(Counter())}
    undefined = Counter()
    for _ in range(manifest.bootstrap_resamples):
        counts = Counter()
        for _ in papers:
            paper = papers[rng.randrange(len(papers))]
            counts.update(paper["counts"] if field_key is None else paper["fields"][field_key])
        for name, metric in _metrics(counts).items():
            if metric["value"] is None:
                undefined[name] += 1
            else:
                samples[name].append(metric["value"])
    result = {
        "method": "paper-cluster percentile bootstrap; linear quantiles",
        "confidence": 0.95,
        "seed": manifest.seed,
        "resamples": manifest.bootstrap_resamples,
        "paper_ids": [paper["paper_id"] for paper in papers],
        "metrics": {
            name: {
                "lower": _quantile(sorted(values), 0.025) if values else None,
                "upper": _quantile(sorted(values), 0.975) if values else None,
                "defined_replicates": len(values),
                "undefined_replicates": undefined[name],
            }
            for name, values in samples.items()
        },
    }
    if field_key is None:
        result["by_field"] = {
            spec.key: _bootstrap(papers, manifest, spec.key) for spec in manifest.fields
        }
    return result


def score_pilot(
    manifest: Manifest,
    references: Mapping[str, Mapping[str, ReferenceField]],
    outputs: Mapping[str, PaperOutput],
    evidence: Mapping[str, EvidenceJudgments] | None = None,
) -> dict:
    """Score attempted eligible held-out papers without modifying caller data.

    Omitted outputs are missing calls, omitted references are unqualified gold,
    and omitted evidence judgments mean no proposed records were supplied (not
    that the model certainly produced none). No empirical qualification is made.
    """
    evidence = evidence or {}
    _validate(manifest, references, outputs, evidence)
    selected = sorted(
        (p for p in manifest.papers if p.eligible and p.split == "held_out" and not p.example),
        key=lambda p: p.paper_id,
    )
    papers = []
    for paper in selected:
        output = outputs.get(paper.paper_id, PaperOutput("missing_call"))
        fields = {}
        covered = set()
        for spec in manifest.fields:
            fields[spec.key] = _score_field(
                spec,
                references.get(paper.paper_id, {}).get(spec.key),
                output.fields.get(spec.key),
                output.state,
            )
            if fields[spec.key]["covered_fields"]:
                answer = output.fields[spec.key]
                covered.update(
                    (spec.key, answer.answer_revision, scope) for scope in _answer_scopes(answer)
                )
        counts = Counter(
            {
                "attempted_papers": 1,
                f"call_{output.state}": 1,
                "papers_without_reference": int(paper.paper_id not in references),
                "papers_without_evidence_judgments": int(paper.paper_id not in evidence),
            }
        )
        for field_counts in fields.values():
            counts.update(field_counts)
        counts.update(_score_evidence(evidence.get(paper.paper_id), covered))
        papers.append(
            {
                "paper_id": paper.paper_id,
                "stratum": paper.stratum,
                "counts": counts,
                "fields": fields,
            }
        )
    keys = [spec.key for spec in manifest.fields]
    result = {
        "contract_version": CONTRACT_VERSION,
        "protocol_version": manifest.protocol_version,
        "codebook_version": manifest.codebook_version,
        "configuration_id": manifest.configuration_id,
        "attempt_manifest_id": manifest.attempt_manifest_id,
        "attempt_manifest_sha256": manifest.attempt_manifest_sha256,
        "reference_set_id": manifest.reference_set_id,
        "qualification": "not_assessed_by_scorer",
        "selection": {
            "manifest_papers": len(manifest.papers),
            "scored_papers": len(selected),
            "excluded_development": sum(p.split == "development" for p in manifest.papers),
            "excluded_examples": sum(p.example for p in manifest.papers),
            "excluded_ineligible": sum(not p.eligible for p in manifest.papers),
        },
        "overall": _aggregate(papers, keys),
        "by_stratum": {},
        "papers": {},
        "uncertainty": _bootstrap(papers, manifest),
    }
    for stratum in sorted({p.stratum for p in selected}):
        subset = [p for p in papers if p["stratum"] == stratum]
        result["by_stratum"][stratum] = _aggregate(subset, keys)
        result["by_stratum"][stratum]["uncertainty"] = _bootstrap(subset, manifest)
    for paper in papers:
        result["papers"][paper["paper_id"]] = _aggregate([paper], keys)
    return result
