# Bounded evidence companion for RO-Crate

This slice adds `evidence/portable-replay.json` and
`evidence/prediction-history.json` to the existing RO-Crate ZIP. The portable
manifest uses the SEIP replay contract. The history links each prediction to
its answer/evidence graph and any retained human support assessments. Multiple
runs for the same article remain separate. A receiver can run
`python -m app.evidence_crate path/to/archive.zip` with the backend package on
the Python path to check canonical source and invocation hashes, input origins,
quotation occurrences, result scopes, graph links, and support-assessment refs
without a database or network connection.

Only frozen title/abstract captures are distributed when the captured request
has no prompt examples, peer invocation, retained raw artifact, full-text
section, or obvious contact/credential text. Other captured runs keep an
explicit `source_unavailable` result; runs without frozen refs remain
`legacy_unversioned`. The companion never reads a mutable current article to
reconstruct older input. The provider response is deliberately withheld, so
quotation emission remains `unverified` even when source occurrence replays.
Assessment rationale is withheld; the exported state and pseudonymous actor
reference do not imply independent qualification or adjudication. Blind-search
archives contain no prediction evidence companion records.

The archive is unsigned. Its checks detect internal damage or inconsistency,
not the authenticity of the original source, the truth of an answer, or
scientific support for a claim. Existing RO-Crate article files and tables keep
their established access rules and licensing notices; this new companion does
not add a PDF or full text. A later governed export can cover licensed source
text, complete multi-call invocations, responses, reviewer rationale, and
qualified gold lineage after their distribution policies are defined.
