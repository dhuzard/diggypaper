"""Synthetic engineering arithmetic only: no paper data or independent human gold."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from random import Random

import pytest

from app.seip_scoring import (
    CONTRACT_VERSION,
    EvidenceJudgments,
    FieldSpec,
    LinkJudgment,
    Manifest,
    Observation,
    PaperAttempt,
    PaperOutput,
    PredictedField,
    QuoteJudgment,
    ReferenceField,
    score_pilot,
)

DEFAULT_FIELDS = (FieldSpec("species", "categorical"),)


def manifest(ids, fields=DEFAULT_FIELDS, resamples=40):
    return Manifest(
        "synthetic-protocol",
        "synthetic-codebook",
        "synthetic-model",
        tuple(PaperAttempt(i, "JATS") for i in ids),
        fields,
        17,
        "synthetic-attempts",
        sha256(b"synthetic frozen manifest fixture").hexdigest(),
        "synthetic-references",
        resamples,
    )


def ref(value, scope=(), unit=None):
    return ReferenceField("reported", (Observation(value, scope, unit),))


def pred(value, scope=(), unit=None):
    return answer("reported", (Observation(value, scope, unit),))


def answer(status, observations=(), revision="synthetic-answer"):
    return PredictedField(status, observations, revision)


def link(*args, **kwargs):
    return LinkJudgment(*args, answer_revision="synthetic-answer", **kwargs)


def metric(result, name):
    return result["overall"]["metrics"][name]


def test_attempt_manifest_retains_missing_invalid_calls_and_fields_and_excludes_examples():
    m = manifest(["ok", "missing", "json", "schema", "error", "field", "invalid", "dev", "ex"])
    m = replace(
        m,
        papers=(
            *m.papers[:7],
            replace(m.papers[7], split="development"),
            replace(m.papers[8], example=True),
        ),
    )
    references = {
        i: {"species": ReferenceField("not_reported")} for i in (p.paper_id for p in m.papers)
    }
    outputs = {
        "ok": PaperOutput(fields={"species": answer("not_reported")}),
        "json": PaperOutput("invalid_json"),
        "schema": PaperOutput("invalid_schema"),
        "error": PaperOutput("error"),
        "field": PaperOutput(),
        "invalid": PaperOutput(fields={"species": answer("invalid")}),
        "dev": PaperOutput(fields={"species": answer("not_reported")}),
        "ex": PaperOutput(fields={"species": answer("not_reported")}),
    }
    result = score_pilot(m, references, outputs)
    counts = result["overall"]["counts"]
    assert result["selection"]["scored_papers"] == counts["attempted_papers"] == 7
    assert metric(result, "eligible_field_coverage") == {
        "numerator": 1,
        "denominator": 7,
        "value": 1 / 7,
    }
    assert metric(result, "reporting_status_correctness")["value"] == 1 / 7
    assert counts["reporting_not_reported__missing"] == 6
    assert counts["missing_output_fields"] == 6
    for reason in (
        "missing_call",
        "invalid_json",
        "invalid_schema",
        "error",
        "missing_field",
        "invalid_field",
    ):
        assert counts[f"eligible_{reason}"] == 1
    assert "dev" not in result["uncertainty"]["paper_ids"]
    assert "ex" not in result["papers"]


def test_one_to_one_scope_matching_extra_duplicates_and_not_reported_proposals():
    a, b = (("arm", "A"),), (("arm", "B"),)
    m = manifest(["multi", "absent"])
    references = {
        "multi": {
            "species": ReferenceField("reported", (Observation("mouse", a), Observation("rat", b)))
        },
        "absent": {"species": ReferenceField("not_reported")},
    }
    outputs = {
        "multi": PaperOutput(
            fields={
                "species": answer(
                    "reported",
                    (
                        Observation("mouse", a),
                        Observation("mouse", a),
                        Observation("rat", a),
                    ),
                )
            }
        ),
        "absent": PaperOutput(fields={"species": pred("invented")}),
    }
    result = score_pilot(m, references, outputs)
    counts = result["overall"]["counts"]
    assert (counts["categorical_tp"], counts["categorical_fp"], counts["categorical_fn"]) == (
        1,
        3,
        1,
    )
    assert metric(result, "categorical_precision")["value"] == 1 / 4
    assert metric(result, "categorical_recall")["value"] == 1 / 2
    assert metric(result, "categorical_f1")["value"] == 1 / 3


def test_absent_gold_and_excluded_reference_states_are_visible_without_invented_negatives():
    m = manifest(["no_gold", "no_field", "access", "na", "unclear"])
    references = {
        "no_field": {},
        "access": {"species": ReferenceField("not_accessible")},
        "na": {"species": ReferenceField("not_applicable")},
        "unclear": {"species": ReferenceField("unclear")},
    }
    outputs = {p.paper_id: PaperOutput(fields={"species": pred("mouse")}) for p in m.papers}
    result = score_pilot(m, references, outputs)
    counts = result["overall"]["counts"]
    assert counts["papers_without_reference"] == 1
    assert counts["missing_reference"] == 2
    assert counts["excluded_reference"] == 3
    assert counts["unscored_proposals"] == 5
    assert metric(result, "reporting_status_correctness")["value"] is None
    assert metric(result, "categorical_precision")["value"] is None
    assert len(result["uncertainty"]["paper_ids"]) == 5


def test_binary_class_imbalance_missing_negatives_never_become_true_negatives():
    m = manifest([str(i) for i in range(10)], (FieldSpec("randomized", "binary"),))
    references = {str(i): {"randomized": ref(i == 0)} for i in range(10)}
    outputs = {str(i): PaperOutput(fields={"randomized": pred(False)}) for i in range(9)}
    result = score_pilot(m, references, outputs)
    counts = result["overall"]["by_field"]["randomized"]["counts"]
    assert tuple(counts[f"binary_positive_{o}"] for o in ("tp", "fp", "fn", "tn")) == (0, 0, 1, 8)
    assert tuple(counts[f"binary_negative_{o}"] for o in ("tp", "fp", "fn", "tn")) == (8, 1, 1, 0)
    assert counts["binary_missing_observations"] == 1
    assert metric(result, "binary_positive_precision")["value"] is None
    assert metric(result, "binary_positive_recall")["value"] == 0
    assert metric(result, "binary_positive_f1")["value"] == 0
    assert metric(result, "binary_negative_f1")["value"] == pytest.approx(8 / 9)
    assert metric(result, "reporting_status_correctness")["value"] == 0.9


def test_numeric_exact_units_scopes_and_type_aware_matching():
    m = manifest(["n"], (FieldSpec("sample_size", "numeric"),))
    a = (("arm", "A"),)
    references = {"n": {"sample_size": ref(10, a, "animal")}}
    outputs = {
        "n": PaperOutput(
            fields={
                "sample_size": answer(
                    "reported",
                    (
                        Observation(10, a, "cage"),
                        Observation(11, a, "animal"),
                        Observation("10", a, "animal"),
                    ),
                )
            }
        )
    }
    result = score_pilot(m, references, outputs)
    counts = result["overall"]["counts"]
    assert tuple(counts[f"numeric_{o}"] for o in ("tp", "fp", "fn")) == (0, 3, 1)
    assert counts["numeric_unit_or_scope_errors"] == 1
    assert counts["numeric_value_errors"] == 2
    with pytest.raises(ValueError, match="integer/range"):
        score_pilot(
            m, references, {"n": PaperOutput(fields={"sample_size": pred(True, a, "animal")})}
        )


def test_evidence_authenticity_attribution_linkage_and_support_are_independent():
    m = manifest(["q", "no_output", "zero_links"])
    references = {p.paper_id: {"species": ref("mouse")} for p in m.papers}
    outputs = {i: PaperOutput(fields={"species": pred("mouse")}) for i in ("q", "zero_links")}
    judgments = EvidenceJudgments(
        quotes=(
            QuoteJudgment("real", "exact", "correct", claimed_present="exact", claim_passes=True),
            QuoteJudgment(
                "negated", "exact", "ambiguous", claimed_unique=True, unique_correct=False
            ),
            QuoteJudgment(
                "invented",
                "absent",
                "incorrect",
                claimed_present="exact",
                claim_passes=False,
                known_negative_challenge=True,
            ),
            QuoteJudgment(
                "normalized",
                "normalized_only",
                "correct",
                claimed_present="normalized",
                claim_passes=True,
            ),
            QuoteJudgment("missing_source", "source_unavailable", "unavailable"),
            QuoteJudgment("empty", "empty", "not_assessed", emitted_nonempty=False),
        ),
        links=(
            link("l1", "species", "real", "correct", "insufficient"),
            link("l2", "species", "negated", "incorrect", "contradicts", True, True, True),
            link("l3", "species", "normalized", "correct", "supports"),
            link("l4", "species", "invented", "incorrect"),
            link("l5", "species", "missing_source", support="unclear"),
        ),
    )
    result = score_pilot(
        m, references, outputs, {"q": judgments, "zero_links": EvidenceJudgments()}
    )
    assert metric(result, "verbatim_quote_rate")["value"] == 2 / 5
    assert metric(result, "verified_attribution")["value"] == 2 / 6
    assert metric(result, "answer_link_correctness")["value"] == 2 / 5
    assert metric(result, "scientific_support_error")["value"] == 2 / 4
    assert metric(result, "scientific_support_error_determinate_only")["value"] == 2 / 3
    assert metric(result, "support_assessment_coverage")["value"] == 4 / 5
    assert metric(result, "false_existence_verification_exact")["value"] == 1 / 2
    assert metric(result, "false_existence_verification_normalized")["value"] == 0
    assert metric(result, "known_negative_false_acceptance")["value"] == 1
    assert metric(result, "negation_conflict_challenge_error")["value"] == 1
    assert metric(result, "false_unique_attribution")["value"] == 1
    assert metric(result, "unverifiable_quote_rate")["value"] == 4 / 6
    counts = result["overall"]["counts"]
    assert counts["eligible_answers_zero_links"] == 1
    assert counts["eligible_answers_correct_link"] == 1
    assert counts["support_unclear"] == counts["support_not_assessed"] == 1
    assert counts["unique_claims"] == counts["unique_claim_false"] == 1


def test_unverifiable_quote_reasons_do_not_double_count_and_missing_evidence_is_unknown():
    m = manifest(["p", "unknown"])
    references = {p.paper_id: {"species": ref("mouse")} for p in m.papers}
    outputs = {p.paper_id: PaperOutput(fields={"species": pred("mouse")}) for p in m.papers}
    result = score_pilot(
        m,
        references,
        outputs,
        {
            "p": EvidenceJudgments((QuoteJudgment("q", "absent", "ambiguous"),)),
        },
    )
    assert metric(result, "unverifiable_quote_rate")["value"] == 1
    counts = result["overall"]["counts"]
    assert counts["eligible_answers_zero_links"] == 1
    assert counts["eligible_answers_evidence_unavailable"] == 1


def test_unassessed_claims_cannot_dilute_error_rates_or_become_successful_checks():
    m = manifest(["x"])
    judgments = EvidenceJudgments(
        quotes=(
            QuoteJudgment(
                "false",
                "absent",
                "incorrect",
                claimed_present="exact",
                claim_passes=False,
                claimed_unique=True,
                unique_correct=False,
            ),
            QuoteJudgment("unknown", claimed_present="exact", claimed_unique=True),
            QuoteJudgment("normalized_unknown", claimed_present="normalized"),
        )
    )
    result = score_pilot(m, {}, {}, {"x": judgments})
    assert metric(result, "false_existence_verification_exact")["value"] == 1
    assert (
        metric(result, "false_existence_verification_exact_all_labelled_lower_bound")["value"]
        == 0.5
    )
    assert metric(result, "present_claim_exact_assessment_coverage")["value"] == 0.5
    assert metric(result, "false_unique_attribution")["value"] == 1
    assert metric(result, "false_unique_attribution_all_labelled_lower_bound")["value"] == 0.5
    assert metric(result, "unique_claim_assessment_coverage")["value"] == 0.5
    assert metric(result, "false_existence_verification_normalized")["value"] is None
    assert metric(result, "present_claim_normalized_assessment_coverage")["value"] == 0


def test_independent_ambiguity_refutes_claimed_uniqueness_without_excluding_its_denominator():
    m = manifest(["x"])
    judgments = EvidenceJudgments(
        quotes=(
            QuoteJudgment(
                "false_unique", "exact", "ambiguous", claimed_unique=True, unique_correct=False
            ),
            QuoteJudgment("existence_only", "exact", "ambiguous"),
        )
    )
    result = score_pilot(m, {}, {}, {"x": judgments})
    assert metric(result, "false_unique_attribution") == {
        "numerator": 1,
        "denominator": 1,
        "value": 1,
    }
    assert metric(result, "false_unique_attribution_all_labelled_lower_bound")["value"] == 1
    assert metric(result, "unique_claim_assessment_coverage")["value"] == 1
    counts = result["overall"]["counts"]
    assert counts["attribution_ambiguous"] == 2
    assert counts["ambiguous_existence_without_unique_claim"] == 1


@pytest.mark.parametrize(
    "existence, claim, passes",
    [
        ("absent", "exact", True),
        ("absent", "normalized", True),
        ("normalized_only", "exact", True),
        ("normalized_only", "normalized", False),
        ("exact", "exact", False),
        ("exact", "normalized", False),
        ("malformed", "exact", True),
        ("empty", "normalized", True),
        ("source_unavailable", "exact", True),
        ("source_unavailable", "normalized", False),
    ],
)
def test_contradictory_independent_existence_and_claim_checks_fail_closed(existence, claim, passes):
    q = QuoteJudgment(
        "q",
        existence,
        emitted_nonempty=existence != "empty",
        claimed_present=claim,
        claim_passes=passes,
    )
    with pytest.raises(ValueError, match="Independent existence|Unavailable sources"):
        score_pilot(manifest(["x"]), {}, {}, {"x": EvidenceJudgments(quotes=(q,))})


def test_producer_claim_disagreement_with_consistent_independent_judgments_is_retained():
    judgments = EvidenceJudgments(
        quotes=(
            QuoteJudgment("absent", "absent", claimed_present="exact", claim_passes=False),
            QuoteJudgment(
                "normalized", "normalized_only", claimed_present="exact", claim_passes=False
            ),
            QuoteJudgment(
                "normalized_pass",
                "normalized_only",
                claimed_present="normalized",
                claim_passes=True,
            ),
        )
    )
    result = score_pilot(manifest(["x"]), {}, {}, {"x": judgments})
    assert metric(result, "false_existence_verification_exact") == {
        "numerator": 2,
        "denominator": 2,
        "value": 1,
    }
    assert metric(result, "false_existence_verification_normalized")["value"] == 0
    assert metric(result, "present_claim_exact_assessment_coverage")["value"] == 1


def test_independently_ambiguous_attribution_cannot_have_a_successful_unique_check():
    q = QuoteJudgment("q", "exact", "ambiguous", claimed_unique=True, unique_correct=True)
    with pytest.raises(ValueError, match="Independent attribution"):
        score_pilot(manifest(["x"]), {}, {}, {"x": EvidenceJudgments(quotes=(q,))})


def test_stale_and_wrong_scope_links_never_credit_the_current_scoped_answer():
    a, b, wrong = (("arm", "A"),), (("arm", "B"),), (("arm", "wrong"),)
    m = manifest(["x"])
    observations = (Observation("mouse", a), Observation("rat", b))
    references = {"x": {"species": ReferenceField("reported", observations)}}
    outputs = {"x": PaperOutput(fields={"species": answer("reported", observations, "current")})}
    judgments = EvidenceJudgments(
        quotes=(QuoteJudgment("q", "exact", "correct"),),
        links=(
            LinkJudgment("old", "species", "q", "incorrect", answer_revision="old", answer_scope=b),
            LinkJudgment("a", "species", "q", "correct", answer_revision="current", answer_scope=a),
            LinkJudgment(
                "wrong", "species", "q", "incorrect", answer_revision="current", answer_scope=wrong
            ),
        ),
    )
    result = score_pilot(m, references, outputs, {"x": judgments})
    counts = result["overall"]["counts"]
    assert counts["eligible_answers"] == 2
    assert counts["eligible_answers_zero_links"] == counts["eligible_answers_correct_link"] == 1
    assert metric(result, "answer_link_correctness")["value"] == 1 / 3
    assert result["attempt_manifest_sha256"] == m.attempt_manifest_sha256
    for index in (0, 2):
        bad = replace(judgments, links=(replace(judgments.links[index], correctness="correct"),))
        with pytest.raises(ValueError, match="current scoped answer revision"):
            score_pilot(m, references, outputs, {"x": bad})
    with pytest.raises(ValueError, match="revision identity"):
        score_pilot(
            m, references, {"x": PaperOutput(fields={"species": PredictedField("reported")})}
        )


def test_bootstrap_resamples_every_paper_carries_failures_and_reports_undefined_replicates():
    m = manifest(["a", "b", "c"], resamples=100)
    references = {p.paper_id: {"species": ref("mouse")} for p in m.papers}
    outputs = {"a": PaperOutput(fields={"species": pred("mouse")})}
    evidence = {"a": EvidenceJudgments((QuoteJudgment("only", "exact", "correct"),))}
    before = deepcopy((m, references, outputs, evidence))
    result = score_pilot(m, references, outputs, evidence)
    assert (m, references, outputs, evidence) == before
    assert score_pilot(m, references, outputs, evidence) == result
    assert metric(result, "eligible_field_coverage")["value"] == 1 / 3
    # Draw every cluster even after an absent 'a'; short-circuiting would change the RNG stream.
    rng = Random(m.seed)
    draws = [[rng.randrange(3) for _ in range(3)] for _ in range(m.bootstrap_resamples)]
    expected_undefined = sum(0 not in draw for draw in draws)
    interval = result["uncertainty"]["metrics"]["verbatim_quote_rate"]
    assert interval["undefined_replicates"] == expected_undefined > 0
    assert interval["defined_replicates"] + interval["undefined_replicates"] == 100
    assert interval["lower"] == interval["upper"] == 1
    assert result["uncertainty"]["metrics"]["eligible_field_coverage"]["lower"] == 0
    assert result["uncertainty"]["paper_ids"] == ["a", "b", "c"]
    assert (
        score_pilot(replace(m, seed=3), references, outputs, evidence)["uncertainty"]
        != result["uncertainty"]
    )


def test_zero_denominators_empty_manifest_and_unassessed_only_support_are_na():
    result = score_pilot(manifest([]), {}, {})
    assert result["contract_version"] == CONTRACT_VERSION
    assert result["qualification"] == "not_assessed_by_scorer"
    assert all(m["value"] is None for m in result["overall"]["metrics"].values())
    assert all(
        i["undefined_replicates"] == 40 and i["lower"] is None and i["upper"] is None
        for i in result["uncertainty"]["metrics"].values()
    )
    m = manifest(["x"])
    result = score_pilot(
        m, {}, {}, {"x": EvidenceJudgments(links=(link("l", "species", "missing"),))}
    )
    assert metric(result, "scientific_support_error")["value"] is None
    assert metric(result, "support_assessment_coverage")["value"] == 0


def test_source_strata_and_order_independence():
    m = manifest(["z", "a"])
    m = replace(
        m, papers=(replace(m.papers[0], stratum="PDF"), replace(m.papers[1], stratum="abstract"))
    )
    references = {"z": {"species": ref("mouse")}, "a": {"species": ref("rat")}}
    outputs = {"z": PaperOutput(fields={"species": pred("mouse")})}
    result = score_pilot(m, references, outputs)
    assert result["by_stratum"]["PDF"]["metrics"]["eligible_field_coverage"]["value"] == 1
    assert result["by_stratum"]["abstract"]["metrics"]["eligible_field_coverage"]["value"] == 0
    assert score_pilot(replace(m, papers=tuple(reversed(m.papers))), references, outputs) == result


@pytest.mark.parametrize(
    "change, message",
    [
        ({"contract_version": "future"}, "Unsupported"),
        ({"protocol_version": ""}, "identities"),
        ({"papers": (PaperAttempt("x", "JATS"), PaperAttempt("x", "JATS"))}, "Duplicate"),
        ({"bootstrap_resamples": 0}, "positive"),
        ({"attempt_manifest_sha256": "missing"}, "SHA-256"),
    ],
)
def test_invalid_scoring_contracts_fail_closed(change, message):
    with pytest.raises(ValueError, match=message):
        score_pilot(replace(manifest(["x"]), **change), {}, {})


def test_unknown_papers_fields_and_failed_call_payloads_fail_closed():
    m = manifest(["x"])
    with pytest.raises(ValueError, match="outside attempted"):
        score_pilot(m, {}, {"outside": PaperOutput()})
    with pytest.raises(ValueError, match="Unknown reference field"):
        score_pilot(m, {"x": {"typo": ref("mouse")}}, {})
    with pytest.raises(ValueError, match="Failed calls"):
        score_pilot(m, {}, {"x": PaperOutput("invalid_json", {"species": pred("mouse")})})
