# Human support assessments v1

Draft SEIP #4 slice, stacked on the answer/evidence graph in PR #27. A qualified
project reviewer can record `supports`, `contradicts`, `insufficient`, `unclear`,
or `not_assessed` for one **existing answer–evidence link** in one prediction
revision. The server stamps actor, time and `seip-support-v1`; a nonempty rationale
is required for a judgment other than `not_assessed`. The assessment is an
explicit human claim. Quote occurrence, answer agreement and model confidence
never create or prefill it.

The API path includes project, article and prediction IDs. The POST body supplies
the graph revision and exact answer/evidence IDs. It rejects a missing graph,
legacy/unlinked graph, absent link, mismatched revision or stale prediction. A
new prediction has new IDs and starts with no assessments; old records remain
readable as historical for authorized users. The GET response includes `current`,
`graph_revision_ref`, `state` and the complete visible assessment history. The
`state` is `not_assessed` when there is no human judgment, even if quotes matched,
or if every explicit record is `not_assessed`. Conflicting reviewers' records
remain separate; this slice does not adjudicate or aggregate them.

Both endpoints enforce project membership and article/search scope. For a blind
search, the prediction and assessments are unavailable until the caller has
submitted their own annotation; then GET returns only that caller's assessments.
POST locks the article row before checking the current prediction so a concurrent
prediction insert cannot silently retarget the judgment on PostgreSQL. The
database checks the exact stored link, revision, project, article, actor and
protocol on insert; updates and deletes are blocked. A populated migration will
not downgrade and discard these records. This intentionally prevents ordinary
deletion of a prediction or user with an assessment. Account anonymization and
retention policy need a later explicit design.

This does not implement a reviewer interface, source-view verification of what
the assessor read, gold adjudication, qualification metrics, or independent
review. A support label should not be used as qualified reference data until
those procedures and the 30-paper pilot are complete.
