# finding-credit-companion/0.1.0

Status: **draft** contract for [issue #55](https://github.com/dhuzard/diggypaper/issues/55), coordinated with [ORAtlas #207](https://github.com/dhuzard/oratlas/issues/207). Docs, a JSON Schema, synthetic fixtures, an offline validator and a pure producer-side builder (`backend/app/finding_credit.py`). No database migration, API route, UI or LLM call is added. Wiring the builder into any export route, and every disclosure path, waits on the ORAtlas privacy/authorization contract [#202](https://github.com/dhuzard/oratlas/issues/202).

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Scope

A **finding credit companion** is an optional, separately versioned JSON file that states who contributed what to the source-paper findings of exactly one [`source-evidence-packet/0.1.0`](../../source-evidence-packet/v0.1.0/README.md) (SEP). It exists so that a consumer such as ORAtlas can carry Diggypaper source-finding credit and exact provenance without receiving private project members, anonymous reviewers or copyrighted text.

- The SEP is **not changed**. `source-evidence-packet/0.1.0` stays frozen, its validator and the ComputationalReviewTemplate importer are unchanged, and nothing in this contract is read by them.
- The companion is **omittable**. A packet is complete and valid without one. A consumer that does not implement this contract ignores the companion and loses only credit, never evidence.
- The companion is a **sibling file**, never a packet member: the SEP rejects any undeclared file in the packet directory (`unexpected-file`). Deliver it next to the packet, for example `packet/` plus `packet.credit.json`.
- Non-goals: TRUST or quality scores; cross-project derivation graphs (ORAtlas #207); permission-aware projection and the shared credit contract (ORAtlas #205/#207); access grants and connector authorization (Diggypaper #53, ORAtlas #202); notifying credited people.

## 2. Five kinds of credit, never inferred from one another

| Kind | Where it lives | Notes |
|---|---|---|
| (a) **Cited paper authors** | SEP `papers.jsonl` `authors` | Bibliographic credit for the published article. Never repeated as a contribution here; never evidence that an author proposed, curated or reviewed a finding. |
| (b) **Finding proposer / curator** | `contributions[]` with `capacity` `finding-proposer` or `finding-curator` | The Diggypaper source-finding contribution. A proposer comes from the finding record; a curator only from an explicit declaration. |
| (c) **Source-support assessor** | `contributions[]` with `capacity` `support-assessor` | A project-scoped `seip-support-v1` judgment on named assessments of one finding revision. It is not a validation of the paper. |
| (d) **Software / model run** | `production[]` | Non-human production provenance (`software` or `ml-model`). It has no ORCID, no CRediT role and no consent, and is never a contributor. |
| (e) **Project owner / team** | `contributions[]` with `capacity` `project-team` | Only from an explicit declaration. Project membership alone creates no contribution and no credit. |

A consumer MUST NOT merge kinds, for example treat a cited author with the same name as the curator, upgrade an assessor to an author, or credit a model as a person. The validator warns (`cited-author-name-match`) when a disclosed name equals a cited author of the bound paper: a coincidence is possible, an inference is not allowed.

## 3. File format

One JSON object in a file (any name, `.json`), UTF-8, pretty-printed with indent 2, sorted keys, LF line ends, a final newline, no BOM (`non-canonical-encoding`). The JSON Schema is `schemas/finding-credit-companion.schema.json` (2020-12, closed objects). The **companion digest** is `sha256:` plus the hex SHA-256 of the exact file bytes, by the same rule as the SEP packet digest (SEP section 2.1).

Explicit unknowns follow SEP section 3.1: `"not-available"` instead of `null`, empty strings or missing keys. Timestamps are RFC 3339 UTC with a `Z` suffix.

### 3.1 Top level

| Field | Type / constraint |
|---|---|
| `schema_version` | const `"finding-credit-companion/0.1.0"` |
| `companion_id` | `credit:` id |
| `created_at` | timestamp |
| `creation_method` | `producer-export`, `synthetic-fixture`, `manual` |
| `producer` | `{name, commit}`; `commit` is 40 hex or `"synthetic-fixture"` |
| `packet` | `{schema_version: "source-evidence-packet/0.1.0", packet_id, packet_digest}`. `packet_digest` MUST equal the SEP packet digest of the bound packet. |
| `audience` | `{level, recipient_ref, purpose, expires_at}` (section 6) |
| `release` | `{state: controller-approved \| not-released, approval_ref, approved_at}` (section 6) |
| `credit_semantics` | `{role_vocabulary: "CRediT ANSI/NISO Z39.104-2022", implies_authorship: false}`. A contribution, a CRediT role or a capacity never implies scholarly authorship. |
| `entries` | array of entries (3.2) |
| `export_status` | `{state: complete \| partial \| withheld, withheld_counts: {entries, contributions}, reasons}`; same completeness rules as SEP section 5.7 |

There is no project id, user id, email, login, rationale, salted global digest or any other cross-project correlator anywhere; the schema rejects such fields, and the validator rejects email-like strings and bare 64-hex digests in any string value (`private-identifier-leak`).

### 3.2 Entry

One entry per finding revision. `binding` ties it to the exact packet content:

| `binding` field | Rule |
|---|---|
| `packet_digest` | the packet digest, repeated per entry so an entry carried alone stays bound |
| `finding_id`, `finding_revision` | an existing revision, and the **latest** revision of that finding in the packet |
| `finding_origin` | equals the SEP `finding.origin` (`source-explicit`, `ai-paraphrase`, `reviewer-interpretation`), copied, never re-derived |
| `paper_id` | the finding's single paper. One finding has one paper; an entry cannot bind several. |
| `source_snapshot_ref`, `source_snapshot_sha256` | equal the paper's `source_snapshot` |
| `passage_ids` | exactly the finding's `passage_ids` |
| `assessment_protocol` | const `seip-support-v1` |
| `assessment_ids` | exactly the packet assessments of this finding revision |

Findings are never merged by DOI or by text. Two findings of one article (same DOI, different results) are two entries with their own proposers, passages and assessments.

`production[]` items are `{agent_type: software | ml-model, name, version, activity: extraction | paraphrase | quote-matching | proposal, run_ref}`.

### 3.3 Contribution

| Field | Rule |
|---|---|
| `contribution_id` | `contribution:` id, unique in the companion |
| `capacity` | `finding-proposer`, `finding-curator`, `support-assessor`, `project-team` |
| `status` | `source-declared` (recorded by the source system or its controller), `self-declared` (stated by the person), `confirmed` (stated and confirmed by both), `withheld` (exists and is not disclosed), `disputed`, `withdrawn` |
| `contributor` | one of `{kind: pseudonym, ref: rv-<hex>}` (project-scoped pseudonym, `app.pseudonym.reviewer_reference`, same form as the SEP `reviewer_ref`), `{kind: withheld, ref: "attribution-withheld"}`, or `{kind: disclosed, display_name, orcid}` |
| `credit_roles` | `[{role, degree: lead \| equal \| supporting \| not-available, declaration_ref}]`. `role` is one of the 14 CRediT roles, spelled as in Z39.104-2022 (`Writing – original draft` with an en dash). Each role points at the explicit assignment or acceptance that created it. |
| `assessment_ids` | the assessor's own assessments; empty for every other capacity |
| `review_blinding` | `blinded`, `unblinded`, `unknown` for an assessor; `not-applicable` otherwise |
| `disclosure` | `{person_consent: granted \| not-granted \| revoked \| not-applicable, controller_authorization: granted \| not-granted \| revoked, membership: active \| revoked \| unknown, consent_ref}` |

`orcid` is `"not-available"` or `{id, verification, verified_at, verification_ref}`:

- `oauth-verified`: the account linkage was verified by ORCID OAuth; `verified_at` and `verification_ref` are real values.
- `claimed-unverified`: a user-supplied string; `verified_at` and `verification_ref` are `"not-available"`. A consumer MUST NOT display it as verified, and MUST NOT merge it with a verified linkage of the same iD.
- The iD check digit (ISO 7064 MOD 11-2) must be correct. An ORCID never grants access and never confirms a CRediT role.

## 4. Credit rules

1. CRediT roles appear only on `source-declared`, `self-declared` or `confirmed` contributions, and only from an explicit declaration (`declaration_ref`). Nothing is derived from project role, quote-match state, review decisions or authorship. Roles on a `withheld`, `disputed` or `withdrawn` contribution are `credit-role-not-asserted`; a role repeated in one contribution is `duplicate-credit-role`.
2. `status: withheld` requires a withheld contributor (`withheld-status-inconsistent`). A withheld contributor MAY keep a status such as `confirmed`: the contribution is acknowledged, the person is not named.
3. A `disclosed` contributor requires `person_consent: granted`, `controller_authorization: granted`, `membership: active` and a `consent_ref` (`consent-missing`); any of them `revoked` is `consent-revoked`. A `disputed` or `withdrawn` contribution is never disclosed (`inactive-assertion-disclosed`).
4. A disclosed contributor has no pseudonym, so it cannot be linked to other pseudonymous records. A support assessor MAY be disclosed only when `review_blinding` is `unblinded` (`blind-reviewer-disclosed`); disclosing one also reveals which packet assessments, and so which SEP `reviewer_ref`, are theirs.
5. A support assessor's pseudonym MUST equal the `reviewer_ref` of each of its packet assessments (`assessor-pseudonym-mismatch`), and those assessments MUST be bound by the entry (`assessor-binding-mismatch`).
6. A production agent's name used as a disclosed person's name is `software-as-person`. The schema gives production agents no ORCID, roles or consent.

## 5. Fail-closed binding

The validator checks the packet first with the frozen SEP validator (allow-restricted mode). If the packet does not validate, the companion is refused (`packet-invalid`). Otherwise:

| Condition | Code |
|---|---|
| `packet.packet_digest`, `packet.packet_id` or an entry's `packet_digest` differs from the bound packet | `packet-digest-mismatch` |
| Finding or revision absent | `dangling-finding-ref` |
| Revision is not the latest in the packet | `stale-finding-revision` |
| `paper_id` or `finding_origin` differs | `finding-binding-mismatch` |
| Snapshot ref or digest differs from the paper, or an assessment was made on another snapshot | `evidence-digest-changed` |
| `passage_ids` or `assessment_ids` differ from the packet | `evidence-binding-mismatch` |

A changed revision, snapshot or assessment set therefore never inherits credit: the producer issues a new companion for the new packet.

## 6. Audience, release and expiry

Release of evidence and credit is a controller decision, separate from project membership and from quotation rights. Citing a published article is bibliographic credit; revealing that a private project used it can still disclose project information.

| `audience.level` | Packet `access_scope.level` allowed | Release | `expires_at` | Other |
|---|---|---|---|---|
| `public` | `public` | `controller-approved` | MAY be `not-available` | approved findings only; no paper with redistribution `restricted` or `unknown` |
| `named-recipient` | `public`, `restricted` | `controller-approved` | required | `recipient_ref` required; same finding and rights limits as public |
| `project-members` | any | MAY be `not-released` | required | the private trail: may include proposed findings and any paper |

Codes: `audience-exceeds-packet-scope`, `release-not-approved`, `release-inconsistent` (approval data missing, present while not released, or approved after `created_at`), `audience-inconsistent` (`recipient_ref` present iff `named-recipient`), `handoff-expiry-missing`, `handoff-expired` (`expires_at` not after `created_at`, or not after the consumer's `--as-of`), `unapproved-finding-shared`, `restricted-redistribution`.

`project-members` means members of the originating project resolving pseudonyms **inside Diggypaper**; the file itself never contains their identities. A `project-members` companion handed to anyone else is a disclosure error by the holder. Who counts as a member, and any grant beyond that, is ORAtlas #202 / Diggypaper #53, not this contract.

Consumers MUST honor revocation: a later companion that withholds a contributor supersedes earlier ones for the same packet, and a consumer MUST drop the earlier disclosure from views, caches and exports. The validator performs no clock reads; a consumer passes its own time as `--as-of`.

## 7. What a public consumer may show

From a valid `public` companion and its packet: the bibliographic citation of the paper (from the packet); disclosed contributors with their status and CRediT roles, with an ORCID marked verified only when `oauth-verified`; pseudonyms as opaque labels; everything else as `attribution-withheld`; production agents as software or model provenance, never as people. Withheld entry counts MUST be shown as omissions, not hidden.

## 8. Validator error codes

`invalid-json`, `non-canonical-encoding`, `schema-invalid`, `unsupported-schema-version`, `artifact-missing`, `private-identifier-leak`, `packet-invalid`, `packet-digest-mismatch`, `dangling-finding-ref`, `stale-finding-revision`, `finding-binding-mismatch`, `evidence-digest-changed`, `evidence-binding-mismatch`, `duplicate-id`, `duplicate-entry`, `assessor-binding-mismatch`, `assessor-pseudonym-mismatch`, `consent-missing`, `consent-revoked`, `blind-reviewer-disclosed`, `inactive-assertion-disclosed`, `withheld-status-inconsistent`, `credit-role-not-asserted`, `duplicate-credit-role`, `orcid-verification-inconsistent`, `orcid-checksum-invalid`, `software-as-person`, `audience-inconsistent`, `audience-exceeds-packet-scope`, `release-not-approved`, `release-inconsistent`, `handoff-expiry-missing`, `handoff-expired`, `unapproved-finding-shared`, `restricted-redistribution`, `completeness-fabricated`, `silent-omission`. Warning: `cited-author-name-match`.

Every code except `artifact-missing` and `packet-invalid` has a negative fixture in `fixtures/invalid/<case>/` (`expected.json` names the packet to bind and the codes that must fire); those two are covered by tests.

## 9. Fixtures and validation

```
python docs/contracts/finding-credit-companion/v0.1.0/validator/fcc_validate.py \
    docs/contracts/finding-credit-companion/v0.1.0/fixtures/valid/public/credit.json \
    --packet docs/contracts/finding-credit-companion/v0.1.0/fixtures/packets/public [--as-of 2027-01-01T00:00:00Z]
```

`--packet` is required: a companion is never validated on its own. Offline, deterministic. Prints JSON `{valid, errors, warnings, counts}` sorted by `(code, file, id)`, plus `companion_digest` and `packet_digest` when valid. Exit 0 valid, 1 invalid, 2 usage. Regenerate fixtures with `python docs/contracts/finding-credit-companion/v0.1.0/fixtures/build_fixtures.py` (idempotent). Tests: `backend/tests/test_finding_credit_companion.py` (contract) and `backend/tests/test_finding_credit.py` (builder).

Fixtures are synthetic: `10.5555/` example DOIs, "Synthetic" names, and only ORCID's fictitious test identity `0000-0002-1825-0097`, used once as `oauth-verified` and once as `claimed-unverified` to prove the two are kept apart. `packets/public` and `packets/private` hold the same records at different access levels. `valid/public` shows the agreed public statements; `valid/project-members` is the private trail, including a proposed finding on a restricted paper.

## 10. Producer builder

`app.finding_credit.build_credit_companion(packet_files, data)` is pure (no database, clock, environment or randomness) and takes the bytes of a SEP packet, for example `app.finding_packet.build_packet(...).files`. It:

- binds every latest finding revision of the packet, and omits (counted, with reasons) findings that a non-member audience may not receive;
- credits the proposer from the finding record and assessors from the packet's own assessments, as pseudonyms, with blinding taken from the review records (otherwise `unknown`);
- adds curators, team members and CRediT roles only from explicit declarations, and drops roles of non-active declarations;
- **never discloses**: every contributor is a pseudonym or `attribution-withheld`, with consent and authorization `not-granted`;
- fails closed (`ValueError`) on a missing controller release for a non-member audience, a missing purpose or expiry, an audience wider than the packet, a non-CRediT role or a production agent that is not software or a model.

It is not exposed through any route. Route integration and disclosure wait on ORAtlas #202.

## 11. Version and evolution policy

Same rules as SEP section 6: additive changes only within `0.1.x`; anything breaking is a new directory `finding-credit-companion/v0.2.0/`; consumers reject versions they do not implement. A companion for a future packet version is a new companion version.

## 12. Open items

1. Align the contribution statuses, capacities and the public projection with the shared ORAtlas credit contract (#207) once it lands; this draft uses the statuses named there plus `withheld`.
2. Consent, controller-authorization and revocation records are referenced (`consent_ref`, `approval_ref`) but not defined here; their format and authority follow ORAtlas #202.
3. Signing, if consumers need authenticity of a companion rather than integrity (same as SEP open item 4).
4. A companion built from a real Finding Curation (#46) export has not been validated end to end. Every companion exercised so far is synthetic or built from synthetic inputs.
