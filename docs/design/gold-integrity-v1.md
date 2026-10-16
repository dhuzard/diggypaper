# Gold integrity revisions v1

Draft SEIP #4 slice stacked on PR #32. A gold article retains its current
`ground_truth_json` for existing benchmark consumers. Every new manual or
review-derived gold entry also gets a frozen revision. Refresh appends another
revision; it does not rewrite earlier answers, evidence quotes, source/review
references, actor pseudonyms, or explicitly selected support-assessment IDs.
The first refresh of a pre-existing gold row captures its then-current values as
`legacy_unqualified` before adding the new revision. Earlier changes to that
legacy row cannot be reconstructed. Deleting the owning gold row, article, or
project cascades the revisions under the existing cleanup lifecycle; individual
revision updates are blocked in PostgreSQL and SQLite.

Each revision freezes its answer field keys and records a digest over those
fields, along with a
separate digest over its evidence quote list. Review-derived revisions retain
the annotation ID, displayed source snapshot/view IDs and view SHA-256 when
the annotation has a bound retained view, plus its reviewer pseudonym, time,
status, and blind flag. Manual revisions have no review/source reference.
Support-assessment IDs are attached only when supplied explicitly and validated
against the same project, article, and search; blind reviewers may attach only
their own assessments. No answer, quote match, or support assessment is inferred
to be a qualified or independently adjudicated gold verdict. All new revisions
are `unqualified`; untouched legacy rows and their first captured revision are
`legacy_unqualified`.

The gold list returns frozen revision history and separate `source`, `evidence`,
and `answer` staleness values: `changed`, `unchanged`, or `unknown`. The existing
`stale` boolean is true when any known dimension changed. Answer and evidence
compare frozen digests with the latest review in the same search. Source
compares frozen displayed-view SHA-256 values only when both reviews carry one;
otherwise it is `unknown`. Manual entries, untouched legacy rows, and missing
comparators also report `unknown`. Repeated capture of identical displayed text
does not make the source stale solely because a snapshot ID changed. PR #26's
review-source binding is required before ordinary API-created reviews can
reliably produce source staleness; this slice never invents a displayed view.

During blind review, a caller sees only gold rows currently sourced from their
own annotation and only their own sourced revisions. The latest comparator is
also their own review. Gold creation from reviews draws only from that caller's
annotations. Manual gold has no review source and stays hidden in blind lists.
This endpoint does not implement a governed phase unlock or adjudicator export;
reviewer qualification and independent adjudication remain protocol work.
