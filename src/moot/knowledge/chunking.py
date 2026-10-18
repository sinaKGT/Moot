"""Splitting text into passages small enough to embed, map and quote, at paragraph or
sentence boundaries where possible, overlapping a little so no fact is cut in half."""

from __future__ import annotations

import re
from dataclasses import dataclass

from moot.knowledge.parsers import Section

CHUNK_CHARS = 1200
OVERLAP_CHARS = 150


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    page: int | None = None


def _pieces(text: str, size: int) -> list[str]:
    """Paragraphs, with long ones split at sentences (and very long sentences at spaces)."""
    pieces: list[str] = []
    for paragraph in re.split(r"\n\s*\n|\n(?=[-*•#] )", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= size:
            pieces.append(paragraph)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            while len(sentence) > size:
                cut = sentence.rfind(" ", 0, size)
                cut = cut if cut > size // 2 else size
                pieces.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                pieces.append(sentence)
    return pieces


def chunk_sections(
    sections: list[Section], size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS
) -> list[Chunk]:
    """Passages of about `size` characters; a passage never spans two PDF pages."""
    chunks: list[Chunk] = []
    for section in sections:
        current: list[str] = []
        length = 0
        for piece in _pieces(section.text, size):
            if current and length + len(piece) + 1 > size:
                chunks.append(Chunk(len(chunks), "\n".join(current), section.page))
                # carry the end of the last passage over, at a word boundary
                tail = current[-1][-overlap:] if overlap else ""
                tail = tail[tail.find(" ") + 1 :] if " " in tail else tail
                current, length = ([tail] if tail else []), len(tail)
            current.append(piece)
            length += len(piece) + 1
        if current and any(p.strip() for p in current):
            text = "\n".join(current)
            if not chunks or chunks[-1].text != text:
                chunks.append(Chunk(len(chunks), text, section.page))
    return chunks
