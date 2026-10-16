# Human support assessments v1

Draft SEIP #4 slice, stacked on the answer/evidence graph in PR #27. An authorized
project member can record `supports`, `contradicts`, `insufficient`, `unclear`,
or `not_assessed` for one **existing answer–evidence link** in one prediction
revision. The server stamps a durable salted actor pseudonym, time and
`seip-support-v1`; a nonempty rationale
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
This own-annotation gate does not establish the required Stage A lock, reviewer
qualification, or independent review. Those remain protocol controls outside this
slice. After account anonymization/deletion clears `actor_id`, the retained
pseudonymous judgment is no longer returned by this blind GET. A separately
governed phase/export view is needed to retrieve such history for adjudication;
this endpoint must not reveal it to another blinded reviewer.
POST locks the article row before checking the current prediction so a concurrent
prediction insert cannot silently retarget the judgment on PostgreSQL. The
database checks the exact stored link, revision, project, article, actor and
protocol on insert. Judgment fields cannot be updated; only nulling the actor
foreign key for account deletion/anonymization is allowed. The salted pseudonym
remains, following the existing annotation deletion pattern, and no raw user ID
is returned by this API. No API deletes individual judgments. Deleting the owning
prediction or project cascades its assessments under the existing product cleanup
policy. A populated migration will not downgrade and discard the records.

This does not implement a reviewer interface, source-view verification of what
the assessor read, gold adjudication, qualification metrics, or independent
review. A support label should not be used as qualified reference data until
those procedures and the 30-paper pilot are complete.
