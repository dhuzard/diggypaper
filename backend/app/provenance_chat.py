"""Persist actual OpenAI-compatible chat calls without inventing model output."""

from __future__ import annotations

import base64
import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InferenceJob, Invocation, ModelRegistry, PromptTemplate
from app.inference import ChatCallTrace, InferenceError
from app.provenance import (
    ModelIdentity,
    PromptIdentity,
    Reconciliation,
    ResponseCapture,
    SourceSnapshotV1,
    complete_invocation,
    digest,
    invocation_record,
    prepare_invocation,
)
from app.provenance_render import RenderedChat

MAX_CHAT_RESPONSE_BYTES = 8 * 1024 * 1024


class PersistedChatTrace(ChatCallTrace):
    """One real call: prepared commit, unknown-before-dispatch, actual response.

    A transport loss leaves ``outcome_unknown``. Marking unknown before posting
    also conservatively covers a crash between that commit and actual dispatch.
    Successful HTTP delivery means the call succeeded even if downstream parsing
    rejects the model's content; in that case no prediction is bound to it.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        snapshot: SourceSnapshotV1,
        rendered: RenderedChat,
        job: InferenceJob,
        prompt: PromptTemplate,
        model: ModelRegistry,
        field_schema: list[dict],
        group_id: uuid.UUID,
        stage: str,
        sequence: int,
    ) -> None:
        self.session = session
        self.snapshot = snapshot
        self.rendered = rendered
        self.job = job
        self.prompt = prompt
        self.model = model
        self.field_schema = field_schema
        self.group_id = group_id
        self.stage = stage
        self.sequence = sequence
        self.row: Invocation | None = None

    async def before_dispatch(self, body: dict) -> None:
        if self.row is not None:
            raise ValueError("chat trace cannot dispatch twice")
        if body.get("messages") != self.rendered.messages:
            raise ValueError("actual driver messages differ from frozen origin map")
        ident = body.get("model")
        if not isinstance(ident, str) or not ident:
            raise ValueError("actual model identity is unavailable")
        envelope = prepare_invocation(
            self.snapshot,
            group_id=self.group_id,
            search_id=self.job.search_id,
            inference_job_id=self.job.id,
            stage=self.stage,
            call_kind="chat",
            sequence=self.sequence,
            attempt=1,
            prompt=PromptIdentity(
                template_id=self.prompt.id,
                version=self.prompt.version_number,
                system_template=self.prompt.system_prompt,
                user_template=self.prompt.user_prompt_schema,
                field_schema=self.field_schema,
                field_schema_sha256=digest(self.field_schema),
            ),
            model=ModelIdentity(
                backend=str(
                    self.model.backend_type.value if self.model.backend_type else "unknown"
                ),
                identifier=ident,
                revision=None,
            ),
            request=body,
            source_view_ids=self.rendered.source_view_ids,
            input_spans=self.rendered.input_spans,
            quotation_producer="may_produce",
        )
        row = invocation_record(envelope)
        self.session.add(row)
        await self.session.commit()
        self.row = row
        complete_invocation(row, "outcome_unknown")
        await self.session.commit()

    async def on_http_response(self, raw: bytes, status_code: int) -> None:
        if self.row is None or self.row.outcome != "outcome_unknown":
            raise ValueError("actual response lacks a prepared unknown call")
        if status_code >= 400:
            # Error bodies may echo credentials. Keep the observed status, never
            # an unreviewed provider error body, as parsed driver metadata.
            response = ResponseCapture(
                origin="driver_parsed",
                raw_availability="unavailable",
                parsed={"http_status": status_code},
                reason="provider error body excluded from provenance",
            )
            outcome = "failed"
        else:
            if len(raw) > MAX_CHAT_RESPONSE_BYTES:
                complete_invocation(
                    self.row,
                    "failed",
                    response=ResponseCapture(
                        origin="driver_parsed",
                        raw_availability="unavailable",
                        parsed={
                            "http_status": status_code,
                            "raw_size": len(raw),
                            "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        },
                        reason="actual response exceeded provenance retention limit",
                    ),
                    reconciliation=Reconciliation(
                        actor_ref=f"worker:inference:{self.job.id}",
                        reason="actual oversized HTTP response received",
                    ),
                )
                await self.session.commit()
                raise InferenceError("response_too_large_for_provenance")
            response = ResponseCapture(
                origin="driver_raw",
                raw_availability="retained",
                raw_base64=base64.b64encode(raw).decode("ascii"),
                raw_sha256=hashlib.sha256(raw).hexdigest(),
            )
            outcome = "succeeded"
        complete_invocation(
            self.row,
            outcome,
            response=response,
            reconciliation=Reconciliation(
                actor_ref=f"worker:inference:{self.job.id}",
                reason="actual HTTP response received after dispatch marker",
            ),
        )
        await self.session.commit()

    @property
    def producing_invocation(self) -> Invocation | None:
        if self.row is not None and self.row.outcome == "succeeded":
            return self.row
        return None
