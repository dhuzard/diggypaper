"""SEIP v1 storage envelopes; no inference or review capture is wired here.

``seip-json-v1`` is UTF-8 JSON, sorted string keys, no ASCII escaping, separators
``,``/``:``, finite JSON numbers only, and no Unicode normalization. Text hashes
use the exact UTF-8 bytes. Request hashes describe application payloads, not HTTP
serialization. Database ownership cascades deliberately bound retention.

Constructors recompute canonical envelope digests. The database enforces scope,
immutable rows/links, lifecycle, raw-byte bounds and (on PostgreSQL) raw digests;
it does not independently reproduce canonical JSON hashes on direct SQL INSERT.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, model_validator

MAX_RAW_BYTES = 25 * 1024 * 1024
ENCODING = "seip-json-v1"
Hash = str
Path = tuple[str | StrictInt, ...]


def canonical_json(value: Any) -> bytes:
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("canonical JSON requires finite JSON values and string keys")

    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash(value: str | None) -> None:
    if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA-256")


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps require an explicit timezone")


def _safe_url(value: str | None) -> None:
    if value is None:
        return
    parsed = urlsplit(value)
    # Preserve approved public query parameters verbatim; reject unknown/signed
    # URLs. Never strip a token and claim the modified URL was the source.
    # This is a storage guard, not proof that arbitrary URLs contain no secrets.
    # Runtime adapters must construct known public permalinks from trusted IDs.
    patterns = {
        "id": r"[0-9]{1,20}",
        "article": r"(?:PMC)?[0-9]{1,20}",
        "article_id": r"[0-9]{1,20}",
        "articleId": r"[0-9]{1,20}",
        "pmid": r"[0-9]{1,20}",
        "pmcid": r"PMC[0-9]{1,20}",
        "page": r"[0-9]{1,6}",
        "doi": r"10\.[0-9]{4,9}/[A-Za-z0-9._;()/:+-]{1,160}",
        "format": r"(?:pdf|xml|html|txt)",
        "download": r"(?:0|1|true|false|yes|no)",
        "pdf": r"(?:0|1|true|false|yes|no)",
        "filename": r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.(?:pdf|xml|html|txt)",
        "file": r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.(?:pdf|xml|html|txt)",
    }
    suspicious = re.compile(r"bearer|secret|password|credential|api[_-]?key|signature|token", re.I)
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or len(value) > 2048
        or suspicious.search(parsed.path + "?" + parsed.query)
        or any(
            key not in patterns or not re.fullmatch(patterns[key], val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        )
        or parsed.fragment
    ):
        raise ValueError("a credential-free public source permalink is required")


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Section(Frozen):
    # Preserve exact extractor section dictionaries, including future JSON metadata.
    model_config = ConfigDict(extra="allow", frozen=True)
    __pydantic_extra__: dict[str, JsonValue]
    title: str
    kind: str
    text: str


class Components(Frozen):
    title: str | None
    abstract: str | None
    methods_text: str | None
    sections: tuple[Section, ...]


def _component(components: Components, path: Path) -> str:
    canonical = path in (("title",), ("abstract",), ("methods_text",)) or (
        len(path) == 3
        and path[0] == "sections"
        and type(path[1]) is int
        and path[1] >= 0
        and path[2] in ("title", "text")
    )
    if not canonical:
        raise ValueError("component path must use a canonical component identity")
    value = components.model_dump(mode="json")
    try:
        for key in path:
            value = value[key]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("component path does not resolve") from exc
    if not path or not isinstance(value, str):
        raise ValueError("component path must resolve to retained text")
    return value


class OriginSpan(Frozen):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=0)
    origin: Literal["component", "synthetic"]
    component_path: Path | None = None
    source_start: StrictInt | None = Field(default=None, ge=0)
    source_end: StrictInt | None = Field(default=None, ge=0)


class TextView(Frozen):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    kind: Literal["composed", "title_abstract", "legacy_methods", "review"]
    availability: Literal["retained", "not_captured", "unavailable"]
    text: str | None
    sha256: Hash | None
    selection: tuple[str, ...]
    fallback: str | None
    composition_version: str
    origins: tuple[OriginSpan, ...] = ()

    @model_validator(mode="after")
    def validate_text(self):
        _hash(self.sha256)
        if self.availability == "retained":
            if self.text is None or self.sha256 != text_digest(self.text):
                raise ValueError("retained view requires exact text and matching digest")
            cursor = 0
            for span in self.origins:
                if span.start != cursor or span.end <= span.start or span.end > len(self.text):
                    raise ValueError("origins must partition view text in code point offsets")
                cursor = span.end
            if cursor != len(self.text):
                raise ValueError("view text has unmapped origins")
        elif self.text is not None or self.sha256 is not None or self.origins:
            raise ValueError("unretained views cannot claim retained text or origins")
        return self


class RawArtifact(Frozen):
    availability: Literal["retained", "digest_only", "not_captured", "unavailable"]
    sha256: Hash | None = None
    media_type: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_artifact(self):
        _hash(self.sha256)
        if self.availability in ("retained", "digest_only") and not (
            self.sha256 and self.media_type
        ):
            raise ValueError("retained/digest-only artifacts require digest and media type")
        if self.availability == "not_captured" and self.sha256 is not None:
            raise ValueError("not-captured artifact cannot claim a digest")
        if self.availability == "unavailable" and not self.reason:
            raise ValueError("unavailable artifact requires a reason")
        return self


class SourceDescription(Frozen):
    type: str
    public_url: str | None = None
    url_state: Literal["retained", "not_captured", "unavailable"] = "not_captured"
    url_unavailable_reason: str | None = None
    attribution_check: Literal[
        "not_checked", "heuristic_match", "mismatch_forced", "unverifiable"
    ] = "not_checked"
    extraction_method: str = "unknown"
    extraction_version: str = "unknown"
    software_build: str = "unknown"

    @model_validator(mode="after")
    def validate_url(self):
        _safe_url(self.public_url)
        if self.url_state == "retained" and self.public_url is None:
            raise ValueError("retained URL requires a public source location")
        if self.url_state != "retained" and self.public_url is not None:
            raise ValueError("unavailable/uncaptured URL cannot claim a source location")
        if self.url_state == "unavailable" and not self.url_unavailable_reason:
            raise ValueError("unavailable URL requires an explicit reason")
        return self


class SourceSnapshotV1(Frozen):
    schema_version: Literal[1] = 1
    canonical_encoding: Literal["seip-json-v1"] = ENCODING
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    project_id: uuid.UUID
    article_id: uuid.UUID
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    source: SourceDescription
    components: Components
    raw_artifact: RawArtifact
    views: tuple[TextView, ...]

    @model_validator(mode="after")
    def validate_views(self):
        _aware(self.captured_at)
        if len({view.id for view in self.views}) != len(self.views):
            raise ValueError("duplicate view ID")
        for view in self.views:
            for span in view.origins:
                if span.origin == "synthetic":
                    if any(
                        v is not None
                        for v in (span.component_path, span.source_start, span.source_end)
                    ):
                        raise ValueError("synthetic origins cannot assert component attribution")
                    continue
                if (
                    span.component_path is None
                    or span.source_start is None
                    or span.source_end is None
                ):
                    raise ValueError("component origin requires path and offsets")
                source = _component(self.components, span.component_path)
                if not 0 <= span.source_start < span.source_end <= len(source):
                    raise ValueError("component offsets outside retained component")
                if source[span.source_start : span.source_end] != view.text[span.start : span.end]:
                    raise ValueError("origin slice does not reproduce view text")
        return self

    @property
    def sha256(self) -> str:
        return digest(self.model_dump(mode="json"))


class InputSpan(Frozen):
    payload_path: Path
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=0)
    origin: Literal["target", "template", "example", "synthetic"]
    view_id: uuid.UUID | None = None
    view_start: StrictInt | None = Field(default=None, ge=0)
    view_end: StrictInt | None = Field(default=None, ge=0)
    component_path: Path | None = None
    source_start: StrictInt | None = Field(default=None, ge=0)
    source_end: StrictInt | None = Field(default=None, ge=0)
    example_ref: str | None = None


class PromptIdentity(Frozen):
    template_id: uuid.UUID | None = None
    version: str | None = None
    system_template: str | None = None
    user_template: str | None = None
    field_schema: JsonValue
    field_schema_sha256: str

    @model_validator(mode="after")
    def validate_schema(self):
        if digest(self.field_schema) != self.field_schema_sha256:
            raise ValueError("field schema digest mismatch")
        return self


class ModelIdentity(Frozen):
    backend: str
    identifier: str
    revision: str | None = None  # NULL means explicitly unknown, never inferred.
    model_artifact_sha256: str | None = None
    tokenizer_artifact_sha256: str | None = None

    @model_validator(mode="after")
    def validate_identity(self):
        if "://" in self.identifier:
            raise ValueError("model identity must exclude service URLs and credentials")
        _hash(self.model_artifact_sha256)
        _hash(self.tokenizer_artifact_sha256)
        return self


def _keys(value, allowed, required):
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        raise ValueError("request contains unsupported or missing keys")


def _validate_request(kind: str, request: dict) -> None:
    canonical_json(request)
    if kind == "chat":
        _keys(
            request, {"model", "messages", "temperature", "response_format"}, {"model", "messages"}
        )
        if not isinstance(request["model"], str) or "://" in request["model"]:
            raise ValueError("invalid chat model identity")
        if not isinstance(request["messages"], list):
            raise ValueError("messages must be a list")
        for message in request["messages"]:
            _keys(message, {"role", "content"}, {"role", "content"})
            if message["role"] not in ("system", "user", "assistant") or not isinstance(
                message["content"], str
            ):
                raise ValueError("unsupported message shape")
        if "response_format" in request and request["response_format"] != {"type": "json_object"}:
            raise ValueError("unsupported response format")
        if "temperature" in request and type(request["temperature"]) not in (int, float):
            raise ValueError("invalid temperature")
    elif kind.startswith("systemone_"):
        _keys(request, {"state", "questions", "model", "max_len"}, {"state", "questions"})
        if not isinstance(request["state"], str) or not isinstance(request["questions"], dict):
            raise ValueError("invalid SystemOne input")
        if "model" in request and (
            not isinstance(request["model"], str) or "://" in request["model"]
        ):
            raise ValueError("invalid SystemOne checkpoint")
        if "max_len" in request and (type(request["max_len"]) is not int or request["max_len"] < 1):
            raise ValueError("invalid SystemOne max_len")
        for question in request["questions"].values():
            _keys(question, {"type", "instructions", "criteria"}, {"type", "instructions"})
            if question["type"] not in ("noul", "choice") or not isinstance(
                question["instructions"], str
            ):
                raise ValueError("unsupported SystemOne question")
            if "criteria" in question and not (
                isinstance(question["criteria"], dict)
                and all(isinstance(v, str) for v in question["criteria"].values())
            ):
                raise ValueError("invalid SystemOne criteria")
    else:
        _keys(request, {"feeds", "output_names"}, {"feeds", "output_names"})
        if not isinstance(request["feeds"], dict):
            raise ValueError("ONNX feeds must be named")
        if request["output_names"] is not None and not (
            isinstance(request["output_names"], list)
            and all(isinstance(v, str) for v in request["output_names"])
        ):
            raise ValueError("invalid ONNX output names")
        for tensor in request["feeds"].values():
            _keys(tensor, {"shape", "dtype", "values"}, {"shape", "dtype", "values"})
            if tensor["dtype"] not in ("int64", "int32", "float32", "float64", "bool"):
                raise ValueError("unsupported ONNX dtype")
            if not isinstance(tensor["shape"], list) or any(
                type(n) is not int or n < 0 for n in tensor["shape"]
            ):
                raise ValueError("invalid ONNX tensor shape")
            if not isinstance(tensor["values"], list) or (
                len(tensor["values"]) != math.prod(tensor["shape"])
            ):
                raise ValueError("ONNX tensor shape/value mismatch")
            expected = bool if tensor["dtype"] == "bool" else int
            for value in tensor["values"]:
                if tensor["dtype"].startswith("float"):
                    valid = type(value) in (int, float)
                else:
                    valid = type(value) is expected
                if not valid:
                    raise ValueError("invalid ONNX tensor value")


class ExecutionDetails(Frozen):
    """Peer-call IDs are typed representations, not resolved group assertions.

    Runtime capture must validate every peer reference against the completed
    same-scope group before binding a prediction. Insert-time existence is not
    required here because an actual trace may contain forward references.
    """

    window_index: StrictInt | None = Field(default=None, ge=0)
    window_count: StrictInt | None = Field(default=None, ge=1)
    winning_call_id: uuid.UUID | None = None
    tie_policy: str | None = None
    max_tokens: StrictInt | None = Field(default=None, ge=1)
    truncation: Literal["none", "applied", "unknown"] = "unknown"
    confidence_call_id: uuid.UUID | None = None
    answers_call_id: uuid.UUID | None = None


class InvocationV1(Frozen):
    schema_version: Literal[1] = 1
    canonical_encoding: Literal["seip-json-v1"] = ENCODING
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    group_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    project_id: uuid.UUID
    article_id: uuid.UUID
    source_snapshot_id: uuid.UUID
    search_id: uuid.UUID | None = None
    inference_job_id: uuid.UUID | None = None
    benchmark_run_id: uuid.UUID | None = None
    variant: str | None = None
    stage: Literal["prefilter", "main"]
    call_kind: Literal[
        "chat", "systemone_relevance", "systemone_detail", "systemone_combined", "onnx"
    ]
    sequence: StrictInt = Field(ge=0)
    attempt: StrictInt = Field(ge=1)
    prepared_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    prompt: PromptIdentity
    model: ModelIdentity
    software_build: str = "unknown"
    request: dict[str, JsonValue]
    request_sha256: Hash
    source_view_ids: tuple[uuid.UUID, ...] = ()
    input_spans: tuple[InputSpan, ...] = ()
    execution: ExecutionDetails = Field(default_factory=ExecutionDetails)
    quotation_producer: Literal["may_produce", "not_produced"]

    @model_validator(mode="after")
    def validate_request(self):
        _aware(self.prepared_at)
        if self.inference_job_id is not None and self.benchmark_run_id is not None:
            raise ValueError("an invocation cannot belong to both job types")
        _validate_request(self.call_kind, self.request)
        if digest(self.request) != self.request_sha256:
            raise ValueError("request digest mismatch")
        if self.call_kind != "chat" and self.quotation_producer != "not_produced":
            raise ValueError("scorers do not produce quotations")
        return self


def prepare_invocation(snapshot: SourceSnapshotV1, **values) -> InvocationV1:
    """Validate an explicit driver payload and rendering-time maps; never infer origins."""
    request = json.loads(canonical_json(values.pop("request")))
    invocation = InvocationV1(
        project_id=snapshot.project_id,
        article_id=snapshot.article_id,
        source_snapshot_id=snapshot.id,
        request=request,
        request_sha256=digest(request),
        **values,
    )
    views = {view.id: view for view in snapshot.views}
    textual: dict[Path, str] = {}
    if invocation.call_kind == "chat":
        textual = {
            ("messages", i, "content"): message["content"]
            for i, message in enumerate(invocation.request["messages"])
        }
    elif invocation.call_kind.startswith("systemone_"):
        textual[("state",)] = invocation.request["state"]
        for name, question in invocation.request["questions"].items():
            textual[("questions", name, "instructions")] = question["instructions"]
            for key, label in question.get("criteria", {}).items():
                textual[("questions", name, "criteria", key)] = label
    for view_id in invocation.source_view_ids:
        if view_id not in views or views[view_id].availability != "retained":
            raise ValueError("invocation view must resolve to retained source text")
    ranges: dict[Path, list[tuple[int, int]]] = {}
    for span in invocation.input_spans:
        if span.payload_path not in textual:
            raise ValueError("origin path is not a textual model-input field")
        value: Any = invocation.request
        try:
            for key in span.payload_path:
                value = value[key]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("input path does not resolve") from exc
        if not isinstance(value, str) or not 0 <= span.start < span.end <= len(value):
            raise ValueError("input offsets outside request text")
        previous = ranges.setdefault(span.payload_path, [])
        if any(span.start < end and start < span.end for start, end in previous):
            raise ValueError("overlapping input origins")
        previous.append((span.start, span.end))
        if span.origin != "target":
            if any(
                v is not None
                for v in (
                    span.view_id,
                    span.view_start,
                    span.view_end,
                    span.component_path,
                    span.source_start,
                    span.source_end,
                )
            ):
                raise ValueError("non-target input cannot assert target attribution")
            continue
        if span.view_id is None or span.component_path is None:
            raise ValueError("target input requires both view and canonical component references")
        if span.view_id not in invocation.source_view_ids:
            raise ValueError("target input view must be declared among source views")
        if (
            any(
                v is None
                for v in (span.source_start, span.source_end, span.view_start, span.view_end)
            )
            or span.example_ref is not None
        ):
            raise ValueError("target input requires offsets and cannot be an example")
        view = views.get(span.view_id)
        if view is None or view.availability != "retained":
            raise ValueError("target input view is unavailable")
        source = _component(snapshot.components, span.component_path)
        if not 0 <= span.source_start < span.source_end <= len(source):
            raise ValueError("target offsets outside retained text")
        if not 0 <= span.view_start < span.view_end <= len(view.text):
            raise ValueError("target view offsets outside retained text")
        mapped = any(
            origin.origin == "component"
            and origin.component_path == span.component_path
            and origin.start <= span.view_start < span.view_end <= origin.end
            and origin.source_start + span.view_start - origin.start == span.source_start
            and origin.source_start + span.view_end - origin.start == span.source_end
            for origin in view.origins
        )
        if not mapped:
            raise ValueError(
                "target input must resolve through a component origin, not synthetic text"
            )
        if (
            view.text[span.view_start : span.view_end]
            != source[span.source_start : span.source_end]
        ):
            raise ValueError("target view and component slices disagree")
        if value[span.start : span.end] != source[span.source_start : span.source_end]:
            raise ValueError("target origin does not reproduce request slice")
    for path, value in textual.items():
        cursor = 0
        for start, end in sorted(ranges.get(path, ())):
            if start != cursor:
                raise ValueError("textual request origins have gaps")
            cursor = end
        if cursor != len(value):
            raise ValueError("every textual request field requires complete origin coverage")
    return invocation


class ResponseCapture(Frozen):
    origin: Literal["driver_raw", "driver_parsed", "unavailable"]
    raw_availability: Literal["retained", "not_captured", "unavailable"]
    raw_base64: str | None = None
    raw_sha256: str | None = None
    parsed: JsonValue = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_capture(self):
        if self.raw_availability == "retained":
            if self.raw_base64 is None or self.origin != "driver_raw":
                raise ValueError("retained response requires actual driver bytes")
            data = base64.b64decode(self.raw_base64, validate=True)
            if hashlib.sha256(data).hexdigest() != self.raw_sha256:
                raise ValueError("raw response digest mismatch")
        elif self.raw_base64 is not None or self.raw_sha256 is not None:
            raise ValueError("uncaptured raw response cannot be reconstructed from parsed JSON")
        if self.origin == "driver_raw" and self.raw_availability != "retained":
            raise ValueError("raw-driver capture requires retained actual bytes")
        if self.raw_availability == "unavailable" and not self.reason:
            raise ValueError("unavailable raw response requires a reason")
        if self.origin == "driver_parsed" and self.parsed is None:
            raise ValueError("parsed response requires actual driver output")
        if self.origin == "unavailable" and (self.parsed is not None or not self.reason):
            raise ValueError("unavailable response requires a reason and no fabricated output")
        return self


class Reconciliation(Frozen):
    actor_ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_time(self):
        _aware(self.recorded_at)
        return self


def source_record(snapshot: SourceSnapshotV1, *, raw_data: bytes | None = None):
    from app.db.models import SourceSnapshot

    raw = snapshot.raw_artifact
    if raw.availability == "retained":
        if raw_data is None or len(raw_data) > MAX_RAW_BYTES:
            raise ValueError("retained raw artifact requires permitted bounded bytes")
        if hashlib.sha256(raw_data).hexdigest() != raw.sha256:
            raise ValueError("raw artifact digest mismatch")
    elif raw_data is not None:
        raise ValueError("raw bytes require explicit retained availability")
    return SourceSnapshot(
        id=snapshot.id,
        project_id=snapshot.project_id,
        article_id=snapshot.article_id,
        captured_at=snapshot.captured_at,
        schema_version=1,
        snapshot_sha256=snapshot.sha256,
        payload=snapshot.model_dump(mode="json"),
        raw_availability=raw.availability,
        raw_sha256=raw.sha256,
        raw_media_type=raw.media_type,
        raw_data=raw_data,
    )


def invocation_record(invocation: InvocationV1):
    from app.db.models import Invocation

    payload = invocation.model_dump(mode="json")
    columns = (
        "id",
        "project_id",
        "article_id",
        "source_snapshot_id",
        "group_id",
        "parent_id",
        "search_id",
        "inference_job_id",
        "benchmark_run_id",
        "variant",
        "stage",
        "call_kind",
        "sequence",
        "attempt",
        "prepared_at",
        "request_sha256",
    )
    return Invocation(
        **{name: getattr(invocation, name) for name in columns},
        schema_version=1,
        request_envelope=payload,
        envelope_sha256=digest(payload),
        outcome="prepared",
    )


def complete_invocation(
    row,
    outcome: Literal["succeeded", "failed", "outcome_unknown"],
    *,
    response: ResponseCapture | None = None,
    reconciliation: Reconciliation | None = None,
    completed_at: datetime | None = None,
) -> None:
    """Set one outcome; callers flush/commit. DB guards also enforce transitions."""
    if row.outcome not in ("prepared", "outcome_unknown") or outcome not in (
        "succeeded",
        "failed",
        "outcome_unknown",
    ):
        raise ValueError("invocation outcome is already terminal")
    if row.outcome == "outcome_unknown":
        if (
            outcome == "outcome_unknown"
            or reconciliation is None
            or response is None
            or (response.origin == "unavailable")
        ):
            raise ValueError("unknown outcome requires audited actual-response reconciliation")
    elif reconciliation is not None:
        raise ValueError("reconciliation requires a previous unknown outcome")
    if outcome == "outcome_unknown":
        if response is not None or completed_at is not None:
            raise ValueError("unknown outcome cannot claim a completed response")
        ended = None
    else:
        if response is None:
            raise ValueError("terminal outcome requires explicit response availability")
        ended = completed_at or datetime.now(UTC)
        _aware(ended)
        prepared_at = InvocationV1.model_validate(row.request_envelope).prepared_at
        if ended < prepared_at:
            raise ValueError("completion precedes preparation")
    row.outcome, row.completed_at = outcome, ended
    row.response_capture = response.model_dump(mode="json") if response is not None else None
    row.reconciliation = reconciliation.model_dump(mode="json") if reconciliation else None


def review_binding(snapshot: SourceSnapshotV1, view_id: uuid.UUID) -> dict:
    view = next((view for view in snapshot.views if view.id == view_id), None)
    if view is None or view.availability != "retained":
        raise ValueError("review binding requires a retained exact display view")
    return {
        "provenance_project_id": snapshot.project_id,
        "source_snapshot_id": snapshot.id,
        "source_view_id": view.id,
        "source_view_sha256": view.sha256,
    }
