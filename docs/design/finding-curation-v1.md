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
  The system holds no redistribution-rights data, so papers are `metadata-only` and every
  quote is withheld (hash kept). `GIT_COMMIT` (40 hex) is recorded as the producer commit,
  otherwise `unknown`.

Not in this slice: UI, LEAD release step, split/merge helpers, per-paper rights metadata,
the Template importer.
