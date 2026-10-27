# SEIP scorer call capture

> **Retired on 2026-10-27.** Upstream removed the decision models (Laya, Kev, ONNX, SystemOne)
> and the fork followed: `app/provenance_scorer.py` and the scorer branches of the inference and
> benchmark tasks are gone. The trace format below stays readable by `app.provenance` and the
> portable replay validator so historical `systemone_*` / `onnx` invocations remain verifiable;
> no new scorer call is captured. Kept as the record of what those rows mean.

Normal inference freezes the article once before the scorer cascade; benchmarks
freeze it once per article and reuse that snapshot across models and variants. Each
SystemOne HTTP request is persisted from the actual driver body before dispatch,
including its bounded text window, question set, window index and response.
Window text is mapped to exact offsets in the retained article view. For a
multi-window call, the first maximum wins. If details are asked, their call
points to the relevance call that supplied the confidence score. Otherwise the
winning relevance call is the prediction's producer.
If no SystemOne checkpoint is specified, the request omits `model` and the
invocation identifies the served model as `systemone-server-default-unknown`.
The registry name is a local alias, not evidence of the server's model identity.

ONNX capture occurs after tokenization and input-name filtering, immediately
before `session.run`. It retains the named tensors and their values, shapes,
and types. The local output is recorded as a parsed confidence score; raw
session outputs are not retained. Artifact revisions and tokenizer digest are
unknown in this slice. A frozen input view is retained, but token-level
attribution to source characters is not asserted.

The scorer invocation's `PromptIdentity` carries the job's configured prompt
template for context. SystemOne and ONNX do not receive those system/user
templates. The retained `request` and its input spans describe the actual
model input; `PromptIdentity` must not be presented as a sent prompt.

Scorers have `quotation_producer=not_produced`, empty generated quotations and
no quote occurrence checks. These calls cannot establish textual support for
an answer. Any scorer result without a succeeded captured call fails the
job or benchmark instead of writing an unbound prediction. Transport loss retains an
`outcome_unknown` call; provider errors retain their status, not error bodies.
Benchmark scorer invocations carry the exact run, search and resolved variant identity,
with no inference-job ID. Their configured template identity and field schema are
frozen as context; actual SystemOne questions remain in the captured request.
SystemOne reads the selected composed view; ONNX reads title/abstract only. Each
benchmark prediction references the selected producing invocation (the winning
window or detail call), its group and frozen source. Existing historical rows
remain unversioned; no invocation or source history is backfilled.
