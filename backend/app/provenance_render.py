"""Exact legacy text composition with origins carried during rendering.

This module is pure: it captures neither a mutable Article nor a model call.
Placeholder searches implement replacement itself; source attribution is carried
through slices, never inferred by searching for article text in the final prompt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Literal

from app.article_text import DEFAULT_KINDS, KINDS
from app.provenance import (
    Components,
    InputSpan,
    OriginSpan,
    SourceSnapshotV1,
    TextView,
    text_digest,
)

COMPOSITION_VERSION = "legacy-compose-v1"
RENDER_VERSION = "legacy-sequential-placeholders-v1"


@dataclass(frozen=True)
class _Piece:
    text: str
    origin: Literal["target", "template", "example", "synthetic"]
    component_path: tuple | None = None
    source_start: int = 0
    view_id: uuid.UUID | None = None
    view_start: int = 0
    example_ref: str | None = None

    def slice(self, start: int, end: int) -> _Piece:
        return replace(
            self,
            text=self.text[start:end],
            source_start=self.source_start + start,
            view_start=self.view_start + start,
        )


@dataclass(frozen=True)
class _Text:
    pieces: tuple[_Piece, ...] = ()

    @property
    def text(self) -> str:
        return "".join(piece.text for piece in self.pieces)

    def __add__(self, other: _Text) -> _Text:
        return _Text(self.pieces + other.pieces)

    def slice(self, start: int, end: int) -> _Text:
        out, cursor = [], 0
        for piece in self.pieces:
            left, right = max(0, start - cursor), min(len(piece.text), end - cursor)
            if left < right:
                out.append(piece.slice(left, right))
            cursor += len(piece.text)
            if cursor >= end:
                break
        return _Text(tuple(out))

    def substitute(self, marker: str, replacement: _Text) -> _Text:
        # Python str.replace does not recursively expand its own replacement.
        original = self.text
        out, cursor = _Text(), 0
        while (index := original.find(marker, cursor)) != -1:
            out += self.slice(cursor, index) + replacement
            cursor = index + len(marker)
        return out + self.slice(cursor, len(original))

    def strip(self) -> _Text:
        text = self.text
        return self.slice(len(text) - len(text.lstrip()), len(text.rstrip()))


def _literal(text: str, origin="synthetic", example_ref=None) -> _Text:
    return _Text((_Piece(text, origin, example_ref=example_ref),)) if text else _Text()


def _component(text: str | None, path: tuple) -> _Text:
    return _Text((_Piece(text, "target", component_path=path),)) if text else _Text()


def _join(parts: list[_Text], separator: str) -> _Text:
    out = _Text()
    for index, part in enumerate(parts):
        if index:
            out += _literal(separator)
        out += part
    return out


def _view(kind, text: _Text, selection, fallback=None) -> TextView:
    origins, cursor = [], 0
    for piece in text.pieces:
        if not piece.text:
            continue
        end = cursor + len(piece.text)
        origins.append(
            OriginSpan(
                start=cursor,
                end=end,
                origin="component" if piece.component_path is not None else "synthetic",
                component_path=piece.component_path,
                source_start=piece.source_start if piece.component_path is not None else None,
                source_end=piece.source_start + len(piece.text)
                if piece.component_path is not None
                else None,
            )
        )
        cursor = end
    return TextView(
        kind=kind,
        availability="retained",
        text=text.text,
        sha256=text_digest(text.text),
        selection=tuple(selection),
        fallback=fallback,
        composition_version=COMPOSITION_VERSION,
        origins=tuple(origins),
    )


def composed_view(components: Components, kinds: list[str] | None = None) -> TextView:
    """Reproduce compose_text, preserving selected-section order and exact fallback."""
    kinds = kinds or DEFAULT_KINDS
    parts, fallback = [], None
    for kind in KINDS if "all" in kinds else kinds:
        if kind == "abstract":
            if components.abstract:
                parts.append(
                    _literal("Abstract\n") + _component(components.abstract, ("abstract",))
                )
            continue
        found = [(i, s) for i, s in enumerate(components.sections) if s.kind == kind]
        if found:
            for index, section in found:
                parts.append(
                    _component(section.title, ("sections", index, "title"))
                    + _literal("\n")
                    + _component(section.text, ("sections", index, "text"))
                )
        elif kind == "methods" and components.methods_text:
            parts.append(
                _literal("Methods\n") + _component(components.methods_text, ("methods_text",))
            )
            fallback = "legacy_methods"
    text = _join(parts, "\n\n")
    if not text.text:
        if components.methods_text:
            text, fallback = _component(components.methods_text, ("methods_text",)), "methods_text"
        elif components.abstract:
            text, fallback = _component(components.abstract, ("abstract",)), "abstract"
    return _view("composed", text, kinds, fallback)


def legacy_methods_view(components: Components) -> TextView:
    return _view(
        "legacy_methods",
        _component(components.methods_text, ("methods_text",)),
        ("legacy_methods",),
    )


def title_abstract_view(components: Components) -> TextView:
    parts = [
        _component(value, (name,))
        for name, value in (("title", components.title), ("abstract", components.abstract))
        if value
    ]
    return _view("title_abstract", _join(parts, "\n"), ("title", "abstract"))


def _from_view(view: TextView) -> _Text:
    if view.availability != "retained":
        raise ValueError("rendering requires a retained exact view")
    return _Text(
        tuple(
            _Piece(
                view.text[span.start : span.end],
                "target" if span.origin == "component" else "synthetic",
                component_path=span.component_path,
                source_start=span.source_start or 0,
                view_id=view.id if span.origin == "component" else None,
                view_start=span.start,
            )
            for span in view.origins
        )
    )


@dataclass(frozen=True)
class RenderedChat:
    system: _Text
    user: _Text

    @property
    def messages(self) -> list[dict]:
        return [
            {"role": "system", "content": self.system.text},
            {"role": "user", "content": self.user.text},
        ]

    @property
    def source_view_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(
            dict.fromkeys(
                piece.view_id
                for text in (self.system, self.user)
                for piece in text.pieces
                if piece.origin == "target"
            )
        )

    @property
    def input_spans(self) -> tuple[InputSpan, ...]:
        out = []
        for message_index, text in enumerate((self.system, self.user)):
            cursor = 0
            for piece in text.pieces:
                if not piece.text:
                    continue
                refs = {}
                if piece.origin == "target":
                    refs = dict(
                        view_id=piece.view_id,
                        view_start=piece.view_start,
                        view_end=piece.view_start + len(piece.text),
                        component_path=piece.component_path,
                        source_start=piece.source_start,
                        source_end=piece.source_start + len(piece.text),
                    )
                out.append(
                    InputSpan(
                        payload_path=("messages", message_index, "content"),
                        start=cursor,
                        end=cursor + len(piece.text),
                        origin=piece.origin,
                        example_ref=piece.example_ref,
                        **refs,
                    )
                )
                cursor += len(piece.text)
        return tuple(out)


def render_chat(
    snapshot: SourceSnapshotV1,
    view_id: uuid.UUID,
    system_template: str | None,
    user_template: str | None,
    *,
    examples: str = "",
    example_ref: str | None = None,
) -> RenderedChat:
    """Preserve fill_prompt's sequential, non-recursive-per-pass replacements."""
    selected = next((view for view in snapshot.views if view.id == view_id), None)
    legacy = next((view for view in snapshot.views if view.kind == "legacy_methods"), None)
    if selected is None or legacy is None:
        raise ValueError("selected and legacy-method views must be frozen before rendering")
    user = _literal(user_template or "", "template")
    user = user.substitute("{article_text}", _from_view(selected))
    user = user.substitute("{methods_text}", _from_view(legacy))
    system = _literal(system_template or "", "template")
    if examples:
        system = (
            system + _literal("\n\n", "template") + _literal(examples, "example", example_ref)
        ).strip()
    return RenderedChat(system, user)
