"""Persist each actual non-generative call against a frozen article view."""

from __future__ import annotations

import base64
import hashlib
import uuid

from app.db.models import Invocation
from app.inference import InferenceError
from app.provenance import (
    ExecutionDetails,
    InputSpan,
    ModelIdentity,
    PromptIdentity,
    Reconciliation,
    ResponseCapture,
    complete_invocation,
    digest,
    invocation_record,
    prepare_invocation,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class PersistedScorerTrace:
    def __init__(
        self,
        session,
        *,
        snapshot,
        view_id,
        job,
        prompt,
        model,
        field_schema,
        group_id,
        stage,
        sequence=0,
    ):
        self.session = session
        self.snapshot = snapshot
        self.view = next(v for v in snapshot.views if v.id == view_id)
        self.job, self.prompt, self.model = job, prompt, model
        self.field_schema, self.group_id, self.stage = field_schema, group_id, stage
        self.sequence = sequence
        self.row: Invocation | None = None
        self.selected_row: Invocation | None = None
        self.rows: dict[uuid.UUID, Invocation] = {}

    def _spans(self, start, state):
        if self.view.text[start : start + len(state)] != state:
            raise ValueError("scorer state differs from frozen article view")
        end = start + len(state)
        spans = []
        for origin in self.view.origins:
            lo, hi = max(start, origin.start), min(end, origin.end)
            if lo >= hi:
                continue
            target = origin.origin == "component"
            refs = (
                dict(
                    view_id=self.view.id,
                    view_start=lo,
                    view_end=hi,
                    component_path=origin.component_path,
                    source_start=origin.source_start + lo - origin.start,
                    source_end=origin.source_start + hi - origin.start,
                )
                if target
                else {}
            )
            spans.append(
                InputSpan(
                    payload_path=("state",),
                    start=lo - start,
                    end=hi - start,
                    origin="target" if target else "synthetic",
                    **refs,
                )
            )
        return tuple(spans)

    async def _prepare(self, request, *, kind, execution, state_start=0, model_identifier=None):
        if self.row is not None and self.row.outcome == "outcome_unknown":
            raise ValueError("previous scorer call has unknown outcome")
        spans = self._spans(state_start, request["state"]) if kind.startswith("systemone_") else ()
        if kind.startswith("systemone_"):
            for name, question in request["questions"].items():
                value = question["instructions"]
                if value:
                    spans += (
                        InputSpan(
                            payload_path=("questions", name, "instructions"),
                            start=0,
                            end=len(value),
                            origin="template",
                        ),
                    )
                for key, label in question.get("criteria", {}).items():
                    if label:
                        spans += (
                            InputSpan(
                                payload_path=("questions", name, "criteria", key),
                                start=0,
                                end=len(label),
                                origin="template",
                            ),
                        )
        identity = model_identifier or self.model.name
        envelope = prepare_invocation(
            self.snapshot,
            group_id=self.group_id,
            search_id=self.job.search_id,
            inference_job_id=self.job.id,
            stage=self.stage,
            call_kind=kind,
            sequence=self.sequence,
            attempt=1,
            # Job configuration only; scorer wire inputs are in request/input_spans.
            prompt=PromptIdentity(
                template_id=self.prompt.id,
                version=self.prompt.version_number,
                system_template=self.prompt.system_prompt,
                user_template=self.prompt.user_prompt_schema,
                field_schema=self.field_schema,
                field_schema_sha256=digest(self.field_schema),
            ),
            model=ModelIdentity(backend=self.model.backend_type.value, identifier=identity),
            request=request,
            source_view_ids=(self.view.id,),
            input_spans=spans,
            execution=execution,
            quotation_producer="not_produced",
        )
        row = invocation_record(envelope)
        self.session.add(row)
        await self.session.commit()
        self.row = row
        self.selected_row = row
        self.rows[row.id] = row
        self.sequence += 1
        complete_invocation(row, "outcome_unknown")
        await self.session.commit()

    async def before_dispatch(
        self, body, *, kind, window_index, window_count, state_start, confidence_call_id
    ):
        await self._prepare(
            body,
            kind=kind,
            state_start=state_start,
            model_identifier=body.get("model") or "systemone-server-default-unknown",
            execution=ExecutionDetails(
                window_index=window_index,
                window_count=window_count,
                confidence_call_id=confidence_call_id,
                tie_policy="first_maximum",
            ),
        )

    async def on_http_response(self, raw, status_code):
        row = self.row
        if row is None or row.outcome != "outcome_unknown":
            raise ValueError("SystemOne response lacks an unknown call")
        if status_code >= 400:
            outcome = "failed"
            response = ResponseCapture(
                origin="driver_parsed",
                raw_availability="unavailable",
                parsed={"http_status": status_code},
                reason="provider error body excluded",
            )
        elif len(raw) > MAX_RESPONSE_BYTES:
            outcome = "failed"
            response = ResponseCapture(
                origin="driver_parsed",
                raw_availability="unavailable",
                parsed={
                    "http_status": status_code,
                    "raw_size": len(raw),
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                },
                reason="response exceeded retention limit",
            )
        else:
            outcome = "succeeded"
            response = ResponseCapture(
                origin="driver_raw",
                raw_availability="retained",
                raw_base64=base64.b64encode(raw).decode(),
                raw_sha256=hashlib.sha256(raw).hexdigest(),
            )
        complete_invocation(
            row,
            outcome,
            response=response,
            reconciliation=Reconciliation(
                actor_ref=f"worker:inference:{self.job.id}", reason="actual SystemOne HTTP response"
            ),
        )
        await self.session.commit()
        if status_code < 400 and len(raw) > MAX_RESPONSE_BYTES:
            raise InferenceError("response_too_large_for_provenance")

    async def before_run(self, feeds, *, identifier):
        request = {
            "feeds": {
                name: {
                    "shape": list(value.shape),
                    "dtype": str(value.dtype),
                    "values": value.reshape(-1).tolist(),
                }
                for name, value in feeds.items()
            },
            "output_names": None,
        }
        # Tokenized feeds are exact; the model/tokenizer artifact revision remains unknown.
        await self._prepare(
            request,
            kind="onnx",
            model_identifier=self.model.name,
            execution=ExecutionDetails(max_tokens=512, truncation="unknown"),
        )

    async def on_run_result(self, score):
        complete_invocation(
            self.row,
            "succeeded",
            response=ResponseCapture(
                origin="driver_parsed",
                raw_availability="not_captured",
                parsed={"confidence_score": score},
            ),
            reconciliation=Reconciliation(
                actor_ref=f"worker:inference:{self.job.id}", reason="actual ONNX session output"
            ),
        )
        await self.session.commit()

    async def on_run_failure(self):
        complete_invocation(
            self.row,
            "failed",
            response=ResponseCapture(
                origin="unavailable",
                raw_availability="unavailable",
                reason="ONNX session raised without output",
            ),
            reconciliation=Reconciliation(
                actor_ref=f"worker:inference:{self.job.id}", reason="ONNX execution failed"
            ),
        )
        await self.session.commit()

    @property
    def producing_invocation(self):
        row = self.selected_row
        return row if row is not None and row.outcome == "succeeded" else None

    def select_producing(self, call_id):
        row = self.rows.get(call_id)
        if row is None or row.outcome != "succeeded":
            raise ValueError("selected scorer call did not succeed in this group")
        self.selected_row = row
