# Normal prediction evidence graph slice

This draft stacks on the captured chat, quote-check, and pure evidence-link contracts.
New normal chat predictions retain `evidence_graph_json` with an `envelope` and its
exact frozen `field_definition`. The API exposes it as `evidence_graph` only after
the existing prediction visibility and blind-review checks. Historical and scorer
rows stay `null`; no current article text is used to invent their past source.

For a complete, valid set of project/search answers, the envelope stores one
server-assigned answer ID per field and one evidence ID per model-provided global
quote. Quotes bind to the final captured invocation and its snapshot. A quote
check reference is included only when the stored check covers that exact quote
index. `captured_input_context` only identifies the retained search context; it
does not claim an occurrence. A `not_found` check remains bound to this context
with its status in `quote_checks`; an overflow quote has no check reference and is
unassessed. Even a confirmed occurrence does not assert support for an answer.

The generated prompt now requests optional `evidence_links` entries containing a
field key and zero-based index into `evidence_quotes`. The adapter creates edges
only from that explicit valid mapping. Existing responses without it get no
edges. Invalid mappings produce a null graph while retaining the raw decision and
quote checks for review. Outputs without a full valid answer set retain their
quotes as `legacy_unlinked`; invalid or oversized quote lists also yield null.
There is no silent truncation or inferred assignment.

The PostgreSQL migration enforces prediction/project/article scope and makes
stored graphs and their source output immutable. The application validates the
full envelope and authoritative receipts before insert. SQL constraints cannot
recompute the Python canonical IDs, digest, or Unicode occurrence algorithm;
replay verification and independent review remain required. Status 2026-10-27:
implemented for normal chat predictions (`app/evidence_prediction.py`, migration `f0025`)
with support assessments on top (`app/api/v1/support_assessments.py`); benchmark
predictions, human annotations, explicit revision editing and frontend editing are still
not wired, and the qualification pilot has not run.
