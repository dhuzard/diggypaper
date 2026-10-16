# SEIP portable replay manifest v1

This is a bounded, unsigned offline consistency format for retained SEIP evidence. The [JSON Schema](schemas/seip-portable-replay-v1.schema.json) describes its wire shape, and the [example](examples/seip-portable-replay-v1.json) contains one frozen source, a succeeded invocation, and a replayable quotation check. The schema is only a shape check; the Python validator performs semantic replay.

From the `backend` directory, run:

```shell
python -m app.portable_replay ../docs/examples/seip-portable-replay-v1.json
```

The command exits nonzero on invalid input. It needs neither a database nor a network connection. The entire manifest is limited to 128 MiB; each embedded canonical source or invocation is limited to the existing 64 MiB storage bound. Entry counts are bounded at 10,000 sources, 20,000 invocations, and 20,000 results.

Each `canonical_base64` value contains the exact `seip-json-v1` UTF-8 bytes of a source or invocation envelope. Its adjacent `sha256` is over those bytes. The validator checks version, canonical encoding, envelope hashes, source and invocation scope, request hashes, input origin slices, response capture and its hash, and peer references. A result with `capture_state: captured` must point to a succeeded invocation. For a chat call with `quote_state: recorded`, `quotation_field` is the captured presence and value of `evidence_quotes` from the producer's parsed decision. The validator runs the frozen quote checker against the invocation's retained target input spans and compares the full occurrence record. A scorer declares `quote_state: not_produced`; it cannot assert occurrences.

`legacy_unversioned` and `source_unavailable` are explicit states with reasons and no fabricated frozen references. A captured chat result may declare quote checks `unavailable` with a reason. These states do not pass as replayed evidence. The `response_capture` envelope may itself explicitly record unavailable raw bytes under the existing provenance contract.

Passing validation means the supplied artifacts are **internally consistent**. This unsigned manifest cannot prove who created the bytes, that the producer truly made the represented call, that a quotation supports an answer, or that an adjudicated decision is correct. A downstream export must preserve those distinctions and establish its own trusted distribution or signature if authenticity is required. This foundation does not integrate with RO-Crate export yet.
