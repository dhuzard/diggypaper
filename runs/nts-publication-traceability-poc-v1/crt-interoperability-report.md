# Interoperability report: Diggypaper packet -> CRT Phase 2 importer

All values below were observed by running the real validators/importer; nothing was patched.

## Field-level crosswalk

| Packet field | CRT Phase 2 | NTS content / outcome |
|---|---|---|
| finding.statement | `claim` | verbatim sentence of the article (origin source-explicit) |
| passage.quote (included, exact, supports) | `claim_source_sentence` | only after a human `supports` assessment; none exists, so never reached |
| paper.identifiers.doi | `doi` | present for all three articles |
| finding.context.population | `study_system` | species/strain or authorization subject; 'unknown' stays 'unknown' |
| numeric_results[].n (definition) | `n` | NTS counts are group / article-level / unknown, never experimental-unit, so no `n` can be filled by design |
| numeric_results, context.* | `evidence` | composed text; counts appear here only after acceptance |
| (none in 0.1.0) | `effect_size` | null; none computed |
| (none in 0.1.0) | `replication_status` | replication_unknown |
| (none in 0.1.0) | `text_access` | unknown |
| NTS identifiers, national authorizations, typed count observations, ALURES links | `(none)` | no packet or Phase 2 field exists; carried only by nts-companion.json |

## Observed

### production packet

- Diggypaper validator valid: **True**; CRT validate-only exit: **0**; CRT import exit: **0**
- Packet digest (Diggypaper): `sha256:9e97100feae5a44fc58ae1923d095a8289b864a6b2a122bb4d98f7851c0021ab`
- Manifest digest (CRT): `sha256:9e97100feae5a44fc58ae1923d095a8289b864a6b2a122bb4d98f7851c0021ab` - agree: **True**
- CRT counts: `{'by_reason': {}, 'consumer_classes': {'incompatible': 0, 'restricted': 0}, 'findings': {'accepted': 0, 'incomplete': 0, 'rejected': 0, 'withheld': 0}, 'omitted_by_producer': {'assessments': 0, 'findings': 13, 'papers': 0, 'passages': 0}, 'producer_export_state': 'withheld'}`

### exploratory packet

- Diggypaper validator valid: **True**; CRT validate-only exit: **0**; CRT import exit: **0**
- Packet digest (Diggypaper): `sha256:c51f114f67c60b28a7c72fd48d0fb34c313c03d57ad9790e00f5d6e4dfd68009`
- Manifest digest (CRT): `sha256:c51f114f67c60b28a7c72fd48d0fb34c313c03d57ad9790e00f5d6e4dfd68009` - agree: **True**
- CRT counts: `{'by_reason': {'rejected:no-supporting-judgment': 13, 'rejected:not-approved-for-export': 13, 'rejected:unreviewed': 13, 'rejected:verification-below-threshold': 13, 'withheld:packet-scope-restricted': 13, 'withheld:paper-rights-not-redistributable': 3}, 'consumer_classes': {'incompatible': 0, 'restricted': 13}, 'findings': {'accepted': 0, 'incomplete': 0, 'rejected': 0, 'withheld': 13}, 'omitted_by_producer': {'assessments': 0, 'findings': 0, 'papers': 0, 'passages': 0}, 'producer_export_state': 'complete'}`

## Verdict

- Schema/validator incompatibility found: **False**
- Digest rule consistent across repositories: **True**
- Acceptance: **BLOCKED_HUMAN_REVIEW_PENDING**

Semantic gaps (design limits, not bugs):

- CRT Phase 2 has no place for NTS identifiers or animal-count observations; the typed data cannot be imported, only referenced from the companion artifact by packet digest.
- CRT only fills `n` from an experimental-unit N; NTS cohort counts are group-level or article-level and are intentionally not relabelled.
- CRT accepts a finding only with a human `supports` assessment on an included exact quote of an `allowed` paper in a non-restricted packet; this study has none of that yet.
