"""Prepared/unknown/actual-response transitions at the chat driver boundary."""

import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Article, InferenceJob, Invocation, ModelRegistry, Project, PromptTemplate
from app.inference import InferenceError
from app.provenance import invocation_from_record
from app.provenance_capture import capture_article_source
from app.provenance_chat import MAX_CHAT_RESPONSE_BYTES, PersistedChatTrace
from app.provenance_render import render_chat


@pytest.fixture
async def traced(session):
    project = Project(title="Trace")
    session.add(project)
    await session.flush()
    article = Article(project_id=project.id, methods_text="Mice randomized.")
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1",
        system_prompt="Read the article",
        user_prompt_schema="{methods_text}",
    )
    model = ModelRegistry(name="local", model_identifier="local:1")
    session.add_all([article, prompt, model])
    await session.flush()
    job = InferenceJob(project_id=project.id, prompt_template_id=prompt.id, model_used=model.name)
    session.add(job)
    await session.commit()
    source, view_id = await capture_article_source(
        session,
        project_id=project.id,
        article_id=article.id,
        text_sections=["methods"],
    )
    await session.commit()
    rendered = render_chat(source, view_id, prompt.system_prompt, prompt.user_prompt_schema)
    trace = PersistedChatTrace(
        session,
        snapshot=source,
        rendered=rendered,
        job=job,
        prompt=prompt,
        model=model,
        field_schema=[{"key": "design", "type": "text"}],
        group_id=uuid.uuid4(),
        stage="main",
        sequence=1,
    )
    body = {
        "model": "local:1",
        "messages": rendered.messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    return trace, body


async def test_actual_raw_response_is_bound_to_prepared_unknown_call(session, traced):
    trace, body = traced
    await trace.before_dispatch(body)
    assert trace.row.outcome == "outcome_unknown"
    assert trace.row.response_capture is None
    with pytest.raises(ValueError, match="twice"):
        await trace.before_dispatch(body)
    raw = b'{"choices":[{"message":{"content":"{}"}}]}'
    await trace.on_http_response(raw, 200)
    assert trace.producing_invocation is trace.row
    await session.refresh(trace.row)
    assert trace.row.outcome == "succeeded"
    assert trace.row.response_capture["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert trace.row.reconciliation["actor_ref"].startswith("worker:inference:")
    envelope = invocation_from_record(trace.row)
    assert envelope.request == body
    assert envelope.input_spans
    assert await session.scalar(select(Invocation.id)) == trace.row.id


async def test_provider_error_retains_status_but_not_unreviewed_error_body(traced):
    trace, body = traced
    await trace.before_dispatch(body)
    await trace.on_http_response(b"SECRET provider echo", 500)
    assert trace.row.outcome == "failed"
    assert trace.producing_invocation is None
    assert trace.row.response_capture["parsed"] == {"http_status": 500}
    assert "SECRET" not in str(trace.row.response_capture)


async def test_oversized_response_records_actual_status_and_cannot_bind(traced):
    trace, body = traced
    await trace.before_dispatch(body)
    with pytest.raises(InferenceError, match="response_too_large_for_provenance"):
        await trace.on_http_response(b"x" * (MAX_CHAT_RESPONSE_BYTES + 1), 200)
    assert trace.row.outcome == "failed"
    assert trace.row.response_capture["parsed"]["http_status"] == 200
    assert trace.row.response_capture["parsed"]["raw_size"] == MAX_CHAT_RESPONSE_BYTES + 1
    assert trace.producing_invocation is None


async def test_request_origin_disagreement_fails_before_inserting_call(session, traced):
    trace, body = traced
    body["messages"][1]["content"] = "other source"
    with pytest.raises(ValueError, match="differ"):
        await trace.before_dispatch(body)
    assert await session.scalar(select(Invocation.id)) is None
