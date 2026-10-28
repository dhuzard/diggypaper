# Finding curation v1 (MVP slice of #46)

Optional, human-curated layer: one article can hold **many** source-paper findings.
It does not touch the existing `Article × Search × AnswerRecord` model, screening, gold or
RO-Crate behaviour. Export follows the frozen contract
`docs/contracts/source-evidence-packet/v0.1.0/` (#47).

- **Identity and revisions.** `findings` is a stable ID scoped to project and article.
  `finding_revisions` are immutable (`FindingContent`: statement, origin, typed context,
  numeric results with unit and what `n` counts). A change is a new revision; only the latest
  revision can be reviewed or exported, and approvals never carry to a newer one. No
  text-based identity merge. Unknown context is an explicit marker, never null.
- **Evidence links** (`finding_evidence`) are many-to-many onto an *existing* prediction
  answer/evidence link in its structured `evidence_graph_json` (legacy, unlinked or forged
  links are rejected; blind-search predictions are refused). Quote-match status is read from
  the stored quote checks and stays separate from human judgment.
- **Independent review** (`finding_reviews`, append-only): any member except the proposer; one
  review per reviewer per revision; each carries `seip-support-v1` judgments for exactly the
  revision's evidence links, and `approve` needs at least one `supports`. State is derived:
  `superseded | withdrawn | rejected | approved | proposed`. Reviewer references are
  project-scoped pseudonyms.
- **Withdrawal** (`finding_withdrawals`, migration `f0036`, append-only): a LEAD retires the
  latest revision of a finding in favour of another live finding of the **same project and
  article** (`POST …/findings/{id}/revisions/{n}/withdrawal`, reason `superseded-extraction`
  or `duplicate`, a non-blank rationale). Composite foreign keys enforce the scope of the
  finding, its revision and the successor in the database. A withdrawal is not a support
  judgment: existing reviews stay, a withdrawn revision cannot be reviewed, and a later
  revision of the finding is live again (the withdrawal belongs to one revision). Its main use:
  re-running an extractor proposes the same findings on a new source snapshot, and the
  reviewed copies of the older run would otherwise put the paper on two snapshots
  (`paper-snapshot-conflict`). For NTS projects, `POST /projects/{id}/nts/retire-superseded`
  (LEAD) plans, and with `{"apply": true}` records, those withdrawals: an approved finding
  whose latest revision cites only superseded runs is paired with a live finding of the newest
  run with the identical statement; findings without such a twin are listed, never withdrawn.
- **Export** (`GET /projects/{id}/findings/export`, LEAD only): a deterministic zip built by
  the pure `app.finding_packet.build_packet`. A finding is exported as its **complete
  revision chain** (1..n under the stable finding id, never renumbered) when its *latest*
  revision is approved by independent reviewers with an exact/normalized passage, a public
  identifier and a source snapshot. Each historical revision keeps its own content, context,
  numbers, evidence (with producer ids), original reviewer judgments (`assessments.jsonl`,
  bound to that exact revision) and the paper's snapshot digest. A historical revision's
  `export_state`/`review_state`/`verification_level` derive from its **own reviews only**: no
  reviews = `proposed/unreviewed/unverified`; own approval = `approved-for-export`; any other
  reviewed revision (e.g. rejected) = `proposed/human-reviewed`. A later approval never
  propagates backward and support is never synthesized. Representational limit: the frozen
  0.1.0 schema has no approve/reject field, so a rejected historical revision is
  indistinguishable from an unapproved one except through the reviewer judgments and
  `producer_ids.support_assessment_ref` that remain. If the latest revision is rejected or
  unapproved, the whole chain is withheld and counted; a withdrawn latest revision withholds
  it as `withdrawn` and takes no part in the paper's snapshot check. Any chain that cannot be written
  faithfully is withheld whole, never trimmed: revision gap or duplicate (`revision-gap`,
  `duplicate-revision`), missing/unknown snapshot (`no-source-snapshot`), a history spanning
  more than one snapshot (`mixed-snapshot`), several chains of one paper on different snapshots
  (`paper-snapshot-conflict`; the packet has one snapshot per paper and no historical snapshot
  is ever fabricated or old reviews repointed), conflicting shared passages
  (`passage-conflict`), or unrepresentable evidence/judgments/self-reviews
  (`unrepresentable-history`). Only project pseudonyms and native passage-judgment rationales
  are exported (no raw actor ids, no review free-text notes).
  Quotations follow the article's redistribution rights (below); by default every quote is
  withheld (hash kept) and the packet is `restricted`. `GIT_COMMIT` (40 hex) is recorded as
  the producer commit; without it the export is refused (409), because the frozen 0.1.0
  contract has no "unknown" commit and a producer never invents one. `make up` passes
  `git rev-parse HEAD` through `docker-compose.yml`.
- **Redistribution rights** (`article_rights_declarations`, migration `f0030`, append-only):
  `POST /projects/{id}/articles/{article_id}/rights` (LEAD only) records one decision per
  revision (`license_id`, `redistribution` = `allowed | metadata-only | restricted`, a
  non-blank `basis`, an optional https `evidence_url`) under the LEAD's pseudonym;
  `GET` on the same path returns the full history to members. The latest revision applies;
  nothing is edited or deleted, and only account deletion may null the actor FK.
  - `allowed` requires a licence from `app.rights.REDISTRIBUTABLE_LICENSES` (CC0-1.0,
    CC-BY-4.0, CC-BY-SA-4.0, CC-BY-3.0, CC-BY-SA-3.0; no NC/ND) **and** an https URL stating
    it. The API and a database CHECK each enforce both; the packet builder re-checks the
    licence. Extending the list is a code plus migration change, never a project setting.
  - Only an `allowed` article exports its quotations (`quote_state: included`, paper rights
    `allowed` under that licence, a `quotes` licence entry in the manifest). No declaration,
    `metadata-only`, or any other licence keeps quotes withheld and the paper `metadata-only`.
  - `restricted` omits the article's findings (`paper-rights-restricted`), counted in the
    manifest like any other omission.
  - The packet scope is a separate, explicit export choice:
    `?access_level=public&metadata_license=CC0-1.0|CC-BY-4.0`. Default stays `restricted`.
    `public` needs a metadata licence.
  - A Computational Review Template S2 import accepts a finding only from a `public` packet
    whose quotes are included. `tests/test_article_rights_api.py` runs CRT's importer on a
    real export when `CRT_CHECKOUT` points to a CRT checkout.

Status 2026-10-28: backend, API and export implemented (`app/api/v1/findings.py`,
`app/finding_packet.py`, migrations `f0029`–`f0030`, withdrawals `f0036`; withdrawals have
API and frontend client functions but no screen yet); the only review UI is the NTS review
queue (`NtsReviewQueue.tsx`); a general findings UI, the LEAD release step and split/merge
helpers are not built, and the Template importer belongs to CRT.
