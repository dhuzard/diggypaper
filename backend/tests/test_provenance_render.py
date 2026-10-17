"""Compare mapped composition to real legacy composition, including replacement quirks."""

import uuid
from types import SimpleNamespace

import pytest

from app.article_text import compose_text, fill_prompt, title_abstract
from app.provenance import (
    Components,
    ModelIdentity,
    PromptIdentity,
    RawArtifact,
    SourceDescription,
    SourceSnapshotV1,
    digest,
    prepare_invocation,
)
from app.provenance_render import (
    composed_view,
    legacy_methods_view,
    render_chat,
    title_abstract_view,
)


def components(**changes):
    return Components.model_validate(
        {
            "title": "Café 🐭",
            "abstract": "Current abstract.",
            "methods_text": "LEGACY {methods_text} {article_text}",
            "sections": [
                {"title": "Introduction", "kind": "introduction", "text": "Background."},
                {"title": "Methods", "kind": "methods", "text": "before {methods_text} after"},
                {"title": "Results", "kind": "results", "text": "No intervention."},
            ],
            **changes,
        }
    )


def frozen(source, kinds=None):
    view = composed_view(source, kinds)
    snapshot = SourceSnapshotV1(
        project_id=uuid.uuid4(),
        article_id=uuid.uuid4(),
        source=SourceDescription(type="fixture"),
        components=source,
        raw_artifact=RawArtifact(availability="not_captured"),
        views=(view, legacy_methods_view(source), title_abstract_view(source)),
    )
    return snapshot, view


@pytest.mark.parametrize(
    "kinds",
    [
        None,
        ["methods"],
        ["results"],
        ["abstract"],
        ["all"],
        ["results", "abstract"],
        ["methods", "methods"],
    ],
)
@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"sections": []},
        {"sections": [], "methods_text": None},
        {"sections": [], "abstract": None, "methods_text": None},
        {"sections": [{"title": "", "kind": "methods", "text": ""}]},
    ],
)
def test_composed_views_reproduce_existing_selection_fallback_and_empty_sections(kinds, changes):
    source = components(**changes)
    article = SimpleNamespace(**source.model_dump(mode="json"))
    view = composed_view(source, kinds)
    assert view.text == compose_text(article, kinds)
    assert title_abstract_view(source).text == title_abstract(article)
    frozen(source, kinds)  # SourceSnapshot validator checks every component/view origin.


@pytest.mark.parametrize(
    "template",
    [
        "{article_text}\n{methods_text}",
        "{methods_text}",
        "{article_text}|{article_text}|{methods_text}",
        "text without markers",
        "{methods_text}{article_text}{methods_text}",
        "",
        None,
    ],
)
def test_rendered_request_matches_legacy_sequential_expansion_and_validates_origins(template):
    source = components()
    snapshot, view = frozen(source)
    rendered = render_chat(snapshot, view.id, "SYS", template)
    article = SimpleNamespace(**source.model_dump(mode="json"))
    assert rendered.user.text == fill_prompt(template, article)
    call = prepare_invocation(
        snapshot,
        group_id=uuid.uuid4(),
        stage="main",
        call_kind="chat",
        sequence=0,
        attempt=1,
        prompt=PromptIdentity(
            system_template="SYS",
            user_template=template,
            field_schema={},
            field_schema_sha256=digest({}),
        ),
        model=ModelIdentity(backend="OLLAMA", identifier="test"),
        request={"model": "test", "messages": rendered.messages},
        source_view_ids=rendered.source_view_ids,
        input_spans=rendered.input_spans,
        quotation_producer="may_produce",
    )
    assert call.request["messages"] == rendered.messages


def test_literal_placeholder_expansion_points_to_legacy_component_not_replaced_article_marker():
    source = components()
    snapshot, view = frozen(source)
    rendered = render_chat(snapshot, view.id, "", "{article_text}|{article_text}|{methods_text}")
    legacy = [s for s in rendered.input_spans if s.component_path == ("methods_text",)]
    assert len(legacy) == 3
    for span in legacy:
        assert rendered.user.text[span.start : span.end] == source.methods_text
        assert (span.source_start, span.source_end) == (0, len(source.methods_text))
    assert rendered.user.text.count("{methods_text}") == 3  # no recursive replacement within a pass
    assert rendered.user.text.count("{article_text}") == 3  # methods inserted after article pass


def test_template_and_examples_are_never_classified_by_matching_source_substrings():
    source = components(
        sections=[{"title": "Methods", "kind": "methods", "text": "Identical source phrase"}]
    )
    snapshot, view = frozen(source)
    same = source.sections[0].text
    system = " \nSYS " + same + " \n"
    shots = "\nExample: " + same + "\n"
    rendered = render_chat(
        snapshot,
        view.id,
        system,
        same + "|{article_text}",
        examples=shots,
        example_ref="gold-fixture:1",
    )
    assert rendered.system.text == (system + "\n\n" + shots).strip()
    system_spans = [s for s in rendered.input_spans if s.payload_path[1] == 0]
    assert all(s.origin != "target" for s in system_spans)
    assert any(s.origin == "example" and s.example_ref == "gold-fixture:1" for s in system_spans)
    first_user = next(s for s in rendered.input_spans if s.payload_path[1] == 1)
    assert first_user.origin == "template" and first_user.view_id is None
    assert rendered.user.text.startswith(same + "|")


def test_synthetic_abstract_heading_and_unicode_offsets_are_explicit():
    source = components()
    snapshot, view = frozen(source, ["abstract"])
    rendered = render_chat(snapshot, view.id, "", "{article_text}")
    spans = rendered.input_spans
    assert spans[0].origin == "synthetic"
    assert rendered.user.text[spans[0].start : spans[0].end] == "Abstract\n"
    assert spans[1].component_path == ("abstract",)
    scorer = title_abstract_view(source)
    assert scorer.origins[0].end == len("Café 🐭") == 6
    assert scorer.text[scorer.origins[0].start : scorer.origins[0].end] == "Café 🐭"
