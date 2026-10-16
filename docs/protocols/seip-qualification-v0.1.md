# SEIP scientific qualification protocol v0.1 — draft for domain review

Issue: [SEIP-6](https://github.com/dhuzard/diggypaper/issues/7). Prepared 2026-10-16. Status: **draft, no dataset selected, no scientific result**. This document fixes candidate procedures before evaluating model output. Two qualified domain reviewers and a separate adjudicator must approve the field codebook, source-access policy and adjudication procedure before reference creation. Changes after the reference set is frozen require a new protocol version with a dated reason.

## Question and units

Can a fixed Diggypaper configuration extract specified animal-research facts from scientific papers while attaching authentic, correctly attributed and scientifically supporting passages? The primary unit for paper-level extraction is an eligible paper × field. Evidence analysis also has quotation and answer–evidence-link units, clustered by paper for uncertainty estimates. A paper can contain multiple species, strains, sex groups, cohorts or interventions; the reference may therefore be a set of scoped observations, not a single inferred article-wide value.

The pilot tests engineering and scientific feasibility, not prevalence or population-wide model accuracy. No claim of scientific validation follows from one successful source match or from the existence of a gold record.

## Sample and freeze

Select 30 legally accessible preclinical animal papers by a written, reproducible search/selection rule: 6 development cases and 24 held-out cases. Target eight held-out papers each where the effective available source is JATS, extracted PDF or abstract only. Within these strata include known challenges of absent reporting, negation, contradictory passages, multiple arms and repeated quotations; record how each challenge was identified without reading model outputs. The purposive design does not support a population-representative accuracy claim. A paper is eligible when bibliographic identity and permitted input-text access can be documented. Duplicate versions of the same study must not cross development and held-out sets; record DOI/PMID/PMCID aliases and check manually when identifiers are absent.

Freeze a manifest before evaluation with paper ID, aliases, source URL/type, retrieval date, source and exact effective-input SHA-256, source/license availability, stratum, selection reason, eligibility, development/held-out assignment, seed and protocol version. Preserve excluded candidates with reasons. The six development papers may refine instructions or scoring code, but cannot contribute to held-out estimates or serve as hidden few-shot examples in the 24-paper set. Freeze model identifier, available revision, prompt/template and output schema versions, software commit, decoding settings and input-section policy before held-out calls. Archive an invocation manifest including every attempted paper, invalid output and error; do not rerun selected failures without recording both attempts and a predeclared choice rule.

## Field codebook to approve

For each field, annotate `reported`, `not_reported`, `not_accessible`, `not_applicable` or `unclear` first. `Not_reported` means the accessible, selected source was searched under the protocol; `not_accessible` means the needed full-text portion was unavailable. Neither is a negative scientific finding. For a reported value, record the exact passage, source version, section/location, the observed value and its scope (species/cohort/arm/time point), plus reviewer notes on conflicting context.

| Field | Value and scope rule | Cases requiring explicit review |
|---|---|---|
| Species | Taxon as written, with a separate normalized label only when unambiguous; multiple species remain separate observations. | Generic “animals” without species, background mentions, cell lines versus live animals. |
| Strain | Strain/stock/genotype as reported, attached to the relevant species/cohort. Preserve the raw string. | Vendor alone, transgenic line versus background strain, strain named only in citations. |
| Animal sex | Male, female, both, mixed without allocation, or unknown as explicitly supported; attach cohort/arm. | “Sex-matched,” exclusions, sex reported for only one arm, human participant sex. |
| Sample size | Integer or reported range with unit: individual animal, litter, cage, experiment or group; attach arm, time point and denominator context. | `n` for sections/samples versus animals, attrition, reused animals, pooled litters, multiple experiments. Do not sum groups unless source explicitly supports a unique total. |
| Housing | Reported cage/grouping, light schedule, temperature, enrichment or other predefined conditions as separate keyed observations; retain units. | Manufacturer recommendations rather than actual conditions, post-surgical housing versus baseline, no explicit condition. |
| Experimental intervention | Intervention/control substance or procedure, dose if stated, route and timing when available; attach arm/species. | Prior literature or protocol citation versus study procedure, intended versus performed treatment. |
| Methodological information | Predefined binary/unknown checklist: random allocation, blinding of outcome assessment, and inclusion/exclusion criteria; retain exact operational wording. | “Random” in unrelated context, blinding not specifying who/what, planned protocol versus executed study. |

Before annotation, domain reviewers sign off on any permitted synonyms, which text sections count as searched, how multi-arm sets are compared, and whether a field requires exact spelling or scoped semantic equivalence. Do not change these rules after viewing held-out outputs.

## Independent reference procedure

Two qualified reviewers independently annotate each held-out paper without seeing model output or each other's labels. The UI blind flag may help, but the protocol also records what was actually visible, when, and any prior familiarity. Each reviewer records field status/value, passage and context, source/version, support judgment, confidence/uncertainty note, identity or durable pseudonym, timestamp, procedure version and relevant conflicts of interest. If no passage supports a reported value, mark the value unresolved rather than inventing a quotation.

Compare initial judgments only after both are locked. A third qualified adjudicator who did not initially label the case resolves each disagreement and records the original pair, chosen outcome, reason, source passage and timestamp. Preserve all three judgments. Unclear/unresolved cases remain visible; exclude them from an eligible correctness denominator only under the predeclared missing-state rule and show their count. Independent adjudication is a procedure, not proof that a study's finding is true. Agent-generated annotations cannot serve as independent reference data.

For evidence links, reviewers see a specific answer revision, candidate passage and surrounding text. They assign `supports`, `contradicts`, `insufficient` or `unclear`; use `not_assessed` before review. Check negation, population/species/cohort, study design, intervention timing, unit of analysis, limitations and conflict with other passages. A quotation's verbatim match and document attribution are recorded separately from this support decision. A correct answer may have an unsupported quotation, and a real quotation may contradict an answer.

## Frozen scoring definitions

Publish numerator, denominator and raw counts for every metric; show `NA` for zero denominators. Report overall and by source stratum, with small-stratum limitations. Keep engineering adversarial fixtures outside empirical paper estimates.

| Metric | Numerator / denominator | Required companion count |
|---|---|---|
| Eligible field coverage | Paper-fields with a usable model answer / all adjudicated eligible paper-fields. | Missing call, invalid JSON/schema, missing field and reference `unclear` separately. |
| Categorical correctness | Correct scoped categorical paper-field observations / eligible adjudicated paper-field observations. | Correct among answered fields as secondary; multi-valued sets scored by preapproved matching rule. |
| Binary/checklist precision, recall, F1 | TP/(TP+FP), TP/(TP+FN), harmonic mean for each predeclared item/class. | TP, FP, FN, TN; missing eligible positive answers contribute FN. Avoid undefined F1 as perfect. |
| Numeric sample-size correctness | Exactly correct value and unit/scope / eligible numeric observations. | Value-only and unit/scope errors separately; no post hoc tolerance. |
| Verbatim quote rate | Exact occurrences in frozen target-article input / all emitted nonempty quotations. | Normalized-only, absent, malformed, empty and source-unavailable separately; repeated occurrences are existence matches but ambiguous locations. |
| Verified attribution | Correct article, document version and location / all proposed evidence records. | Unknown/unavailable and ambiguous locations; adjudicable-only accuracy as secondary. |
| Answer-link correctness | Correct scoped answer–passage links / all proposed links. | Eligible answers with zero links and answers with at least one correct link. |
| False verification | System-verified records independently found absent/wrongly attributed / all system-verified records. | False acceptances / known-negative challenge records separately; authentication and attribution errors split. |
| Unverifiable quote rate | Missing, ambiguous or unavailable-source quotations / all emitted quotations. | Each reason, with normalized-only shown separately. |
| Scientific support error | Contradictory or insufficient links / all independently adjudicated links. | Supports, contradictory, insufficient, unclear and unassessed raw counts; coverage among all proposed links. |
| Negation/conflict sensitivity | Correctly flagged negation/conflict links / all independently marked eligible challenge links. | Missed cases and their contexts. |

Review agreement is reported before adjudication as raw agreement and, where class support permits, Cohen's kappa; the final adjudicated set is used for model comparison. Agreement is not a substitute for source truth. Existing `metrics.score` intersects predictions and gold, so this pilot scorer must start from the frozen attempted-paper manifest and count absent outputs explicitly. Existing text token overlap cannot stand in for semantic correctness of strain, housing or intervention.

Use 95% Wilson intervals for a binary paper-level outcome. Use a fixed-seed paper-cluster bootstrap with 2,000 resamples for rates/F1 based on multiple fields or links per paper; resample papers, carrying all their observations together. Report interval method, seed, raw denominators and undefined replicates. Small strata and purposive selection limit interpretation. A zero observed error count does not establish zero risk.

## Reproducibility and stop gates

Store the manifest, protocol version, signed reviewer codebook, locked pre-adjudication judgments, adjudication trail, exact source/input hashes, model/configuration, prompts, outputs including failures, scoring code commit and machine-readable results. Export enough evidence for an independent reader to reproduce match/attribution counts; identify unavailable licensed raw documents without fabricating a copy. Publish only materials permitted by source license and reviewer privacy policy.

Engineering gate: every asserted exact verified span replays from retained input; known-negative fixtures never verify; old source versions are not silently rebound; offline export validates; attempted-paper denominators retain failures. Scientific gate: independent references are frozen before held-out results, and qualified humans review interpretations. If reviewers, source rights or the source/evidence implementation are unavailable, publish software checks and mark empirical qualification pending. After 24 held-out papers, report observed failure modes and a human-approved bounded continue/stop decision; do not invent an empirical threshold after observing results or claim a large benchmark.
