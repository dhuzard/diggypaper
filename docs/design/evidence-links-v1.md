# Evidence and answer links v1: pure contract

Draft implementation slice for [SEIP issue #4](https://github.com/dhuzard/diggypaper/issues/4),
based on `origin/main` at `9e5859c1d00921fb19f5c61146deeb39977b2df9`.
The new `app.evidence_links` module has no database, API, task or frontend wiring.
It does not complete issue #4 or establish scientific support.

## JSON contract

[`evidence-envelope-v1.schema.json`](../schemas/evidence-envelope-v1.schema.json)
describes structural JSON validation. Authoritative reference, scope, frozen-value
and immutable-revision checks require `validate_envelope` and a trusted context;
JSON Schema alone cannot perform them. All models forbid extra properties and
freeze nested records/collections. Version is explicit and required on envelopes.

```json
{
  "schema_version": "evidence-envelope-v1",
  "state": "structured",
  "scope": {
    "project_ref": "project-A",
    "article_ref": "article-A",
    "record_kind": "prediction",
    "record_ref": "prediction-A",
    "revision_ref": "revision-1"
  },
  "field_definition_ref": "sha256:<frozen-definition-digest>",
  "answers": [{"answer_ref": "<server-UUID>", "field_key": "design", "value": "RCT"}],
  "evidence": [{
    "evidence_ref": "<server-UUID>",
    "local_ref": "passage-1",
    "quote": "Mice were randomized.",
    "origin": "captured_input_context",
    "source_binding_ref": "binding-A",
    "quote_match_ref": null
  }],
  "links": [{"answer_ref": "<server-UUID>", "evidence_ref": "<server-UUID>"}]
}
```

Placeholders above explain the shape; the complete valid fixture with real UUIDs
and digest is [`evidence_links_v1.json`](../../backend/tests/fixtures/evidence_links_v1.json).
It includes a many-to-many graph, trusted context, producer-local drafts and a
legacy conversion. One answer references two evidence records; one evidence
record links to two answers without duplicated provenance.

Scope includes project, article, record kind (`prediction`,
`benchmark_prediction` or `annotation`), record reference and revision reference.
References are opaque ASCII tokens, not authorization credentials. A server
adapter must resolve authorized records and allocate a stable revision reference
before constructing the context. The validator compares the entire submitted
scope to that context.

The record revision conservatively covers the whole envelope: answers, evidence,
links and field identity. Updating any of them requires a fresh revision reference
when a prior version exists. Resuming/replaying an unchanged version is idempotent.
Changing an answer and later changing it back still uses separate revision IDs.
This first slice does not implement independently revisioned individual fields.

## Stable identities and frozen fields

`FrozenFieldDefinition` retains the exact effective question and ordered field
definitions. Supported fields follow the existing backend's boolean/choice/text
schema, with labels, options, primary and metric metadata. Unknown properties,
duplicate keys, reserved metadata keys and invalid definitions fail validation.
Callers supply the effective definitions; the module does not recover old field
meaning from a mutable current project.

The field identity is SHA-256 of the `field-definition-v1` model's JSON data,
including its schema version and explicit defaults. `canonical_json` uses sorted
object keys, compact separators, exact UTF-8, no ASCII escaping, no implicit Unicode
normalization and no NaN. Arrays preserve order. This is the envelope contract's
encoding, not an assertion of equivalence to snapshot/request digest encodings.
Changing a key, label, option, question or ordering changes the identity. An old
envelope cannot silently retarget evidence after a field rename.

Authoritative answer and evidence IDs are deterministic UUID5 values under the
fixed namespace in the module. Their name encoding is the versioned canonical
JSON array `["evidence-id-v1", scope, kind, field_definition_ref_or_null, local]`.
Answer `local` is the frozen field key; evidence `local` is a producer-local
passage token. Evidence identity uses a null field-definition reference because
passages can link to multiple fields. Scope binds all IDs to their owning
record/revision. UUIDs are recomputed on validation; they are not access tokens.

`build_envelope` accepts local passage tokens and field-key links, then assigns
the authoritative UUIDs. Drafts cannot assert server IDs. Local tokens have no
authority outside their envelope. Reusing a token twice, including with different
quotes, fails; it is never resolved by taking the first or last occurrence.
Reusing a revision for changed contents fails against an authoritative `previous`
envelope. Caller-supplied `previous` is optional only for initial creation or
read validation; a persistence adapter must load it for updates and enforce
revision uniqueness/concurrency transactionally. Pure code cannot establish global
history or prevent a caller from omitting that database check.

Answers must match every frozen field and its trusted expected value exactly.
Boolean values are not coerced from strings or numbers. Links reject unknown
fields, dangling IDs, duplicate edges, and cross-record/revision IDs. Invalid raw
model structures are rejected without mutation, trimming, repaired links or
guessed assignments. Their raw output must be retained by the caller.

## Source and match authority

The trusted `ValidationContext` contains a fail-closed `allowed_input_refs`
allowlist, source-binding receipts and optional quote-match receipts. It must
come from authorized retained server records, never from the submitted graph.
Broad receipt registries do not authorize other calls: a referenced input must
also occur in the allowlist. Its default is empty, so captured evidence fails
without an explicitly authorized input. Legacy unlinked evidence needs no input.

A source-binding receipt resolves an opaque binding reference to project,
article, snapshot, input and input kind. Prediction and benchmark-prediction
records require `invocation` inputs; annotations require `review_view` inputs.
These adapters can map to #2's physical snapshot/invocation IDs or retained review
view/token bindings without inventing a historical capture. The receipts must
be immutable: an existing binding reference cannot be reassigned to another input
or snapshot. This module does not authenticate receipts, load source text, or
validate review tokens itself.

An optional quote-match reference resolves to a trusted #3 result receipt. Its
project/article, snapshot/input and exact original quote must agree with the
selected source binding and evidence record. Duplicate receipt IDs fail rather
than selecting one. Full match results, candidate offsets and resource-limit
metadata remain in their separately referenced record. They are not reconstructed
or duplicated here. Component-based locations remain the matcher/source contract's
responsibility; view offsets never become a new document-location identity here.

`captured_input_context` means only that a quotation claim is bound to retained
input context. It does not mean the quote occurred there. A match reference can be absent or point
to `not_checked`, `not_found`, `incomplete`, or any other matcher status without
turning the link into a support assessment. Source matching and answer linkage
are separate observations. Support/adjudication fields are forbidden in this
envelope; a future assessment contract must attach to the exact revisions.

## Legacy projection and update conflicts

`legacy_envelope` creates `legacy_unlinked` records with no field-definition
identity, answers, source/match binding or links. Original answer JSON remains
outside this quote-only conversion and must be preserved by its storage adapter.
Quote order, duplicates and empty strings remain unchanged; distinct occurrences
have distinct local/authoritative IDs. Invalid non-string or oversized lists fail
without truncation. A failed conversion does not authorize discarding original
legacy data or claiming that its current article was the historical input.

`legacy_quote_projection` returns one string per evidence record in declared
order. It includes unlinked records and preserves duplicate strings, even when
different records happen to have identical wording. One evidence record linked
to several answers appears once. It performs no authenticity filtering.

`preserve_legacy_round_trip` returns the existing validated envelope unchanged
when the old global quote list is omitted/null or exactly echoes its projection.
An edited, reordered or cleared list returns
`legacy_update_conflict_requires_explicit_revision`. A structured-to-legacy
downgrade fails even with a new revision reference. These are pure conflict
primitives, not already deployed endpoint behavior.

The future API must detect quote-only submissions, preserve structured metadata
on unchanged echoes, and report an explicit conflict (for example HTTP 409) for
changed global quotes rather than overwriting the graph. It must also distinguish
answer edits: this quote-only helper cannot authorize carrying old links or
assessments onto changed answers. Such edits require a new revision and an
explicit policy for new unlinked evidence while preserving the prior revision.
No rule here infers which field an old global quotation was intended to support.

## Bounds, checks and remaining integration

Version 1 supports at most 128 fields/answers, 512 evidence records, 4,096 links,
128 allowed inputs, 512 source receipts and 512 match receipts. Quotes and text
answers are at most 16,384 code points. The effective question is at most 20,000,
labels 200, and choice options 1,000 code points, with at most 256 options per
field. Unsupported existing data must be handled explicitly by integration;
these checks never silently trim it. Graph validation makes no latency/RSS claim.

Focused tests use only the backend's existing Pydantic dependency and pytest,
without application/database fixtures:

```powershell
python -m pytest tests/unit/test_evidence_links.py --confcutdir=tests/unit -q
ruff check app/evidence_links.py tests/unit/test_evidence_links.py
ruff format --check app/evidence_links.py tests/unit/test_evidence_links.py
```

The 69 focused cases passed with Python 3.12.6, Pydantic 2.14.1 (the repository
constraint), pytest 9.1.1 and Ruff 0.17.1. The JSON Schema is checked against
`EvidenceEnvelopeV1.model_json_schema()` so structural changes require fixture
review. The full backend suite has not run for this isolated slice.

Still required before issue #4 can be complete: authoritative revision storage
and concurrency; #2 input/review-token adapters and source capture; #3 match-result
persistence and receipt resolution; raw invalid model-output retention; generated
prompt/schema instructions; inference/benchmark resume wiring; standard/custom
annotation parsing; legacy-client API conflict handling; reserved-metadata review
status comparison; blind/project authorization; export and frontend round trips;
and independent review plus integration checks. No migration, API, existing
schema.py behavior, worker, export, frontend or assessment implementation changes
are included in this branch.
