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
  `superseded | rejected | approved | proposed`. Reviewer references are project-scoped
  pseudonyms.
- **Export** (`GET /projects/{id}/findings/export`, LEAD only): a deterministic zip built by
  the pure `app.finding_packet.build_packet`. Only latest, approved findings with an
  exact/normalized passage, a public identifier and a source snapshot are exported; everything
  else is omitted and counted by reason (`partial`/`withheld`), never exported as approved.
  Quotations follow the article's redistribution rights (below); by default every quote is
  withheld (hash kept) and the packet is `restricted`. `GIT_COMMIT` (40 hex) is recorded as
  the producer commit, otherwise `unknown`.
- **Redistribution rights** (`article_rights_declarations`, migration 0030, append-only):
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
    `public` needs a metadata licence and a real `GIT_COMMIT` (409 otherwise), because
    consumers pinned to the frozen #47 schema refuse `producer.commit: unknown`.
  - A Computational Review Template S2 import accepts a finding only from a `public` packet
    whose quotes are included. `tests/test_article_rights_api.py` runs CRT's importer on a
    real export when `CRT_CHECKOUT` points to a CRT checkout.

Not in this slice: UI, LEAD release step, split/merge helpers, the Template importer.
