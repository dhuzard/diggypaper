# SEIP source and invocation contract v0.1

Design input for [issue #2](https://github.com/dhuzard/diggypaper/issues/2), 2026-10-16. Status: **reviewed proposal, not implemented**. This freezes the scientific invariants and fixture expectations before the additive migration. Physical column names, indexes and request trace adapters may be refined in the implementation PR if these invariants and old-client behavior remain testable.

## Storage boundary

Use two additive entities: immutable `source_snapshots` and per-actual-call `invocations`. Add nullable source/invocation references to predictions and benchmark predictions, and nullable source/view binding to annotations. Keep answer/evidence graphs and assessment revisions in versioned JSONB for the first bounded implementation. A dedicated evidence table is not warranted until a concrete query or integrity constraint requires it. Existing article, search, prompt, model, job and review IDs remain references rather than duplicated relational hierarchies.

Historical rows created before capture have `legacy_unversioned` provenance. Their current article text cannot be asserted to be the text used earlier. A snapshot first captured during rollout has its real capture timestamp; it is not a retrospective version. Do not copy current text into old predictions as if it were their inference input.

## `SourceSnapshotV1`

Required: schema version, UUID, project/article IDs, capture timestamp, immutable DOI/PMID/PMCID aliases, source type/public URL/attribution-check state, extraction method/version/software build or explicit unknown, raw artifact SHA-256/availability/media type, and a snapshot digest. Retain exact title, abstract, legacy methods text and ordered section dictionaries as separate source components. `methods_text` may differ from the methods section; reconstructing it from sections can change a legacy `{methods_text}` prompt.

Retain the exact UTF-8 text (or an immutable, resolvable blob reference) for every target-article view actually supplied or displayed, its SHA-256, selection/fallback/composition version and origin-span map. At minimum the design must represent section-composed text, title-plus-abstract scorer text and legacy methods text. A displayed review view also needs its exact text/hash and selection. Source components, section titles and synthetic headings should be distinguishable in origin spans. Two identical strings with different article/source attribution remain distinct snapshot identities; do not use digest alone as version ID. Offset convention: zero-based, half-open Unicode code points; hashes: SHA-256 over exact UTF-8 text without implicit normalization. Define a separate, versioned canonical JSON encoding for snapshot/request digests.

Raw-file state is explicit: `retained`, `digest_only`, `not_captured` or `unavailable`. The mutable current `ArticleFile` row is not a historical raw artifact. A retained extracted-text view is required for any claim of replayable quote matching, even when the original licensed PDF cannot be exported.

## `InvocationV1`

Required: schema version, UUID, invocation group and optional parent, project/article/search/source IDs, related inference job or benchmark run and variant, stage (`prefilter` or `main`), call kind (`chat`, `systemone_relevance`, `systemone_detail`, `systemone_combined`, `onnx`), call sequence/attempt, start/end timestamps, and outcome (`prepared`, `succeeded`, `failed`, `outcome_unknown`). Snapshot prompt/template version and rendered system/user messages, field-definition/schema identity, model backend/identifier and known revision or explicit unknown, software build and safe request parameters. Credential-bearing URLs, API keys, authentication headers, decrypted secrets and arbitrary settings dumps must never enter the record.

Store the exact canonical request payload sent to the driver boundary and its digest. This represents the application payload, not necessarily the transport's byte-for-byte HTTP serialization. Record target-article spans and example/template spans separately, including payload path/message index, input offsets, snapshot view/component offsets and origin. Distinct source-location counts use the underlying component and its offsets; repeated insertions or two views of one component retain their separate input/view occurrences without inventing additional document locations. Build mappings during rendering; never infer source origin by substring search of the final prompt. The existing sequential `fill_prompt` substitutions can expand a literal `{methods_text}` inside inserted article text, so repeated/missing placeholders and this literal case require explicit fixtures before changing rendering semantics.

Write an immutable prepared request before dispatch. Completion may transition its outcome and response capture once; a crash after dispatch remains `outcome_unknown` until reconciled. Retried calls receive new invocation IDs and attempt numbers. Capture available raw response before parsing; when the existing driver exposes parsed JSON only, mark raw response unavailable rather than presenting a JSON reconstruction as raw model output. A prediction references its final producing call and the associated group; a prefilter rejection has no invented main call.

SystemOne creates a combined relevance-and-detail request for a single window, or multiple relevance-window requests plus a possible winning-window detail request. Retain every actual body, effective stripped/truncated window, questions, `max_len`, winning-window/tie policy and response. In a multiwindow result, confidence and detail answers may come from different calls; the group must retain both producing-call relationships. ONNX capture records feeds actually passed to `session.run` (names, shapes, dtypes, values), tokenizer/model artifact identity and truncation. The current ONNX path uses title plus abstract even in a main-stage path. Scorer-only outputs have producer state `not_produced` for quotations, not a failed-verification state.

## Capture and review binding

Obtain a coherent current article/source revision in a short transaction. All source writers, including PDF/JATS replacement, import and metadata refresh, must participate in the same coherence rule. Refresh already loaded ORM values explicitly; a job-wide cached `Article` object cannot be treated as the latest source. Hold no database lock over external retrieval or inference. Render and dispatch exclusively from the frozen copy, then bind the returned prediction to those same IDs without rereading mutable article text.

Article GET returns an exact retained review view and a server-issued token binding project, article, search, snapshot/view ID and text digest. Annotation POST validates the token and scope. If the article changes between GET and POST, accept an authorized retained old view with separate `current_source_drift`, or reject clearly if that view is unavailable; never silently bind the review to new text. A client omitting a source token creates an explicit `legacy_unversioned` review. A malformed, forged or cross-project token fails without disclosing another project's data. The token records what the client acknowledged, not proof that a person read the text. Blind searches must keep model output and other reviewers hidden until the existing authorization rule permits access.

Both standard and custom-field annotation parsing must retain valid reserved evidence/binding metadata rather than dropping it. Exclude reserved metadata from `_review_status` answer-agreement comparison. A changed answer gets a new revision and cannot inherit an old support assessment. An old client cannot erase a newer structured evidence revision through a silent missing-key update; define downgrade and conflict behavior explicitly before writing the API adapter.

## Review fixtures and blockers

| Fixture | Required observation |
|---|---|
| PDF→JATS with no methods; failed retrieval; protected upload | Current source/PDF/methods remain coherent; failure rolls source adoption back. A metadata-only refresh is recorded as such. |
| Source replacement during a call | Prediction binds one old or new retained source, never mixed text and file generations. |
| Already loaded ORM article | Capture refreshes to a coherent database revision rather than trusting an old identity-map object. |
| Abstract fallback; `{methods_text}` only; repeated/missing placeholders; literal placeholder inside article | Exact effective request and target-origin spans reproduce the actual model input. |
| Example-only quotation | Cannot verify as target-article evidence, even if it appears in the system prompt. |
| Prefilter rejection/pass; SystemOne multiwindow/tie; ONNX truncation | Stored invocation count and payload/feed hashes match mocked actual calls; no phantom calls. |
| Source, prompt or field change during a call; crash/resume | Result retains frozen definitions and distinct attempt IDs; unknown outcomes are explicit. |
| GET source A → replace with B → POST A; invalid/cross-scope token | Review binds A with drift or fails explicitly; never binds B as seen. |
| Old standard/custom clients; user deletion; blind search | Historical structured evidence is retained, actor identity follows privacy policy, and hidden material stays hidden. |
| Populated PostgreSQL upgrade | Nullable legacy refs, scoped integrity, rollback strategy and retained-reference constraints demonstrated. |

Block a PR for invented historical bindings, missing actual calls/windows, example leakage into target evidence, source substitution at review, credential or blind-data leakage, unavailable text described as replayable, legacy-client evidence loss, or quotation matching displayed as scientific support. Required checks include focused API/worker tests, real PostgreSQL Alembic upgrade from populated data, backend lint/full suite and frontend checks when the review-view contract changes.
