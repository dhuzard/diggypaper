# SEIP scorer call capture

Normal inference now freezes the article once before the scorer cascade. Each
SystemOne HTTP request is persisted from the actual driver body before dispatch,
including its bounded text window, question set, window index and response.
Window text is mapped to exact offsets in the retained article view. For a
multi-window call, the first maximum wins. If details are asked, their call
points to the relevance call that supplied the confidence score. Otherwise the
winning relevance call is the prediction's producer.

ONNX capture occurs after tokenization and input-name filtering, immediately
before `session.run`. It retains the named tensors and their values, shapes,
and types. The local output is recorded as a parsed confidence score; raw
session outputs are not retained. Artifact revisions and tokenizer digest are
unknown in this slice. A frozen input view is retained, but token-level
attribution to source characters is not asserted.

Scorers have `quotation_producer=not_produced`, empty generated quotations and
no quote occurrence checks. These calls cannot establish textual support for
an answer. A normal scorer result without a succeeded captured call fails the
job instead of writing an unbound prediction. Transport loss retains an
`outcome_unknown` call; provider errors retain their status, not error bodies.
The benchmark scorer path remains legacy and is outside this slice.
