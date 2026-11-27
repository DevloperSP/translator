"""Multilingual sentence-aware text splitting for XTTS-v2 synthesis."""

from __future__ import annotations

import re

# Sentence terminators across Latin, Devanagari (।), Arabic (؟ / ۔), and CJK (。！？)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।؟۔。！？\n])\s+|(?<=[。！？])")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;،、:])\s+")


def _split_long_segment(segment: str, max_chars: int) -> list[str]:
    """Break a single over-long sentence on clauses, whitespace, or hard character bounds."""
    if len(segment) <= max_chars:
        return [segment]

    # First try splitting on clause punctuation (, ; ، etc.)
    clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(segment) if c.strip()]
    if len(clauses) > 1:
        merged: list[str] = []
        current = ""
        for clause in clauses:
            if len(clause) > max_chars:
                if current:
                    merged.append(current)
                    current = ""
                merged.extend(_split_by_words(clause, max_chars))
                continue
            candidate = f"{current} {clause}".strip() if current else clause
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    merged.append(current)
                current = clause
        if current:
            merged.append(current)
        return merged

    return _split_by_words(segment, max_chars)


def _split_by_words(text: str, max_chars: int) -> list[str]:
    words = text.split()
    if len(words) <= 1:
        # No spaces (e.g. long CJK run): hard slice cleanly
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    parts: list[str] = []
    current = ""
    for word in words:
        if len(word) > max_chars:
            if current:
                parts.append(current)
                current = ""
            parts.extend(word[i : i + max_chars] for i in range(0, len(word), max_chars))
            continue
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = word
    if current:
        parts.append(current)
    return parts


def split_text_for_tts(text: str, *, max_chars: int = 240) -> list[str]:
    """Split text into natural sentence chunks not exceeding `max_chars`."""
    normalized = " ".join(text.strip().split())
    if not normalized:
        return []
    if max_chars < 20:
        max_chars = 20
    if len(normalized) <= max_chars:
        return [normalized]

    raw_sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    sentences: list[str] = []
    for sent in raw_sentences:
        compact = " ".join(sent.split())
        if len(compact) <= max_chars:
            sentences.append(compact)
        else:
            sentences.extend(_split_long_segment(compact, max_chars))

    chunks: list[str] = []
    current = ""
    for sent in sentences:
        candidate = f"{current} {sent}".strip() if current else sent
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = sent
    if current:
        chunks.append(current)

    return chunks
