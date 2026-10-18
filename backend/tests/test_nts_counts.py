"""Animal-count observation rules: exact reading, derived flags, overlap, missing N."""

# ruff: noqa: E501  (long synthetic sentences)

from app.nts.counts import COUNT_CATEGORIES, extract_counts, reconcile_total


def _clusters(*sections):
    return extract_counts("art", [{"title": t, "kind": "methods", "text": x} for t, x in sections])


def _flat(clusters):
    return [o for c in clusters for o in c.observations]


def test_categories_include_all_required_values():
    assert set(COUNT_CATEGORIES) == {
        "NTS_planned_or_authorized",
        "RAR_actual_used",
        "article_reported",
        "breeding",
        "control",
        "experimental",
        "unknown",
    }


def test_total_of_n_is_article_reported_and_not_derived():
    (o,) = _flat(_clusters(("Animals", "A total of 146 rats were used.")))
    assert (o.count_category, o.number, o.derived, o.species) == (
        "article_reported",
        146,
        False,
        "rat",
    )


def test_missing_noun_is_flagged_not_guessed():
    (o,) = _flat(
        _clusters(("Animals", "A total of 146—Wistar breed, males and females, were used."))
    )
    assert o.unit_counted.startswith("unknown")
    assert any("noun is absent" in n for n in o.notes)


def test_per_group_pair_and_derived_total():
    obs = _flat(
        _clusters(
            (
                "Dose",
                "Doses (a, b, c) were given to five doses of rats (2 males and 2 females per dose).",
            )
        )
    )
    per = [o for o in obs if o.count_scope == "per-group"]
    assert {o.sex: o.number for o in per} == {"male": 2, "female": 2}
    (tot,) = [o for o in obs if o.rule_id == "derived-cohort-total"]
    assert tot.number == 20 and tot.derived and "5 groups x (2 males + 2 females)" in tot.derivation


def test_controls_are_a_separate_category():
    text = "Six active treatment groups, each consisting of 5 males and 5 females. The negative control group and the positive control group each consisted of 7 males and 7 females."
    obs = _flat(_clusters(("Main", text)))
    cats = {(o.count_category, o.number) for o in obs if o.count_scope == "per-group"}
    assert ("experimental", 5) in cats and ("control", 7) in cats


def test_additional_animals_are_not_a_second_cohort():
    text = "Rats got three doses (2 males and 2 females per dose). The 0.104 mg/kg group was expanded with 6 additional males and 6 females."
    obs = _flat(_clusters(("FT", text)))
    extra = [o for o in obs if o.count_scope == "additional-to-group"]
    assert {o.number for o in extra} == {6}
    assert all(any("one cohort, not two" in p for p in o.possible_overlap) for o in extra)


def test_main_study_dose_selection_flags_possible_overlap():
    text = "Rats received three doses (2 males and 2 females per dose). The 0.104 mg/kg group (dose selected for the main study) was expanded with 6 additional males and 6 females."
    obs = _flat(_clusters(("FT", text)))
    assert any("not stated" in p for o in obs for p in o.possible_overlap)


def test_missing_n_yields_no_observation_and_no_invented_total():
    clusters = _clusters(("Animals", "Mice were housed under standard conditions."))
    assert clusters == []
    assert reconcile_total(clusters)["status"] == "no-article-total"


def test_breeding_category():
    obs = _flat(_clusters(("Colony", "We kept 12 mice as breeding pairs.")))
    assert [o.count_category for o in obs] == ["breeding"] and obs[0].number == 12


def test_reconcile_never_attributes_the_difference():
    clusters = _clusters(
        ("Animals", "A total of 30 rats were used."),
        ("Dose", "Rats got two groups (5 males and 5 females)."),
    )
    r = reconcile_total(clusters)
    assert (
        r["article_reported_total"] == 30
        and r["unattributed_animals"] == 10
        and r["sum_of_stated_cohorts"] == 20
    )
    assert "not attributed" in r["caveat"]


def test_inconsistent_total_n_is_called_out():
    obs = _flat(
        _clusters(("Res", "Values are combined (two females + two males per group; total n = 5)."))
    )
    assert any("INCONSISTENT" in n for o in obs for n in o.notes)
