# Persisted quotation checks v1

This draft slice stores occurrence checks on normal chat `article_predictions` in
`quote_checks_json`. It depends on a retained `SourceSnapshotV1` and the final
successful `InvocationV1`. Historical predictions and scorer-only outputs retain
SQL `NULL`, meaning **not checked against captured article input**. Their old
`evidence_quotes` values remain unchanged.

The JSON record has `schema_version: "quote-checks-v1"`, source snapshot and
invocation IDs, `producer: "chat"`, a `state`, `items`, `list_complete` and
`overflow_count`. Each item has the original zero-based quote-list index, a
deterministic match ID scoped to the invocation, and the full
`ArticleQuoteMatcher` outcome: exact or separately labeled normalized matches,
original component/view/input offsets, candidate counts, resource-limit state,
and unknown uniqueness when search stopped early. A `not_found` result is only
recorded after a complete search. The parsed model decision remains in
`decision_json`; actual response bytes belong to the invocation. Checks do not
rewrite quotes or answers.

The state is `missing_quote_field`, `invalid_quote_list`, `empty_quote_list`,
`checked`, or `incomplete` when the list exceeds 100 entries. An oversized list
retains all raw model values in `decision_json` but checks only the first 100;
the remainder is explicitly counted as unassessed. Per-quote `invalid` and
`incomplete` results stay visible inside a `checked` list. In particular, an
empty list from a chat model is different from the scorer's intentional
`not_produced` state, which is not yet captured here.

The matcher receives only validated `origin="target"` spans from the final
producing request. System instructions, few-shot examples, synthetic headings,
and other articles are excluded. Repeated occurrences can prove a quotation
exists without proving a unique passage. A source match never implies the
passage supports an answer, human acceptance or scientific adjudication.

Alembic 0023 leaves all existing rows null. For new checked records, PostgreSQL
and SQLite require the check's input/source IDs to match the prediction's bound
invocation/snapshot and require a completed producing call. They prevent later
mutation of the check and checked model output. They cannot independently
recompute Unicode matching inside SQL; independent replay uses the retained
canonical invocation and algorithm version. Downgrade refuses to discard any
persisted check until an explicit retention decision removes those rows.

This is backend persistence only. Benchmark and human-review matching, the API
payload, UTF-16 display conversion, UI wording and answer-level links are still
separate integration work. Until those are implemented, do not display this as
"verified evidence" or a support decision.
