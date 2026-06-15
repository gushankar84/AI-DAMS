"""Shared pipeline helpers: text chunking, simple language guess."""
from __future__ import annotations

import re


def chunk_text(text: str, target_chars: int = 1200, overlap: int = 150) -> list[str]:
    """Split text into overlapping chunks on paragraph/sentence boundaries.

    Chunk-level embeddings give finer retrieval granularity than one vector per
    document and let snippets point near the matched passage.
    """
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= target_chars:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= target_chars:
                buf = p
            else:  # paragraph longer than a chunk: hard-split
                for i in range(0, len(p), target_chars - overlap):
                    chunks.append(p[i:i + target_chars])
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def window_segments(seg_texts: list[str], anchors: list,
                    min_words: int = 6) -> list[tuple[str, object]]:
    """Group transcript segments into dense-embedding units that are each specific enough.

    One dense vector PER ASR segment turns short fillers ("sorry sir", "मैं वो", "🎵") into
    standalone match units. Their near-centroid embeddings clear the relevance floor for
    almost ANY query (cosine 0.48-0.55 vs unrelated text) — a search "black hole" that
    surfaces for everything (one audio file matched airplane/pizza/spaceship/horse).

    Strategy: a SUBSTANTIAL segment (>= min_words) stands ALONE — keeping focused content
    intact preserves its specific embedding and a clean cross-encoder score (merging it into a
    big mixed window would dilute both). Only RUNS of short segments are merged together, until
    they reach min_words, so no individual filler becomes a standalone vector. Each unit keeps
    its FIRST segment's frame anchor.

    Only the SEMANTIC vectors are grouped; the per-segment transcript rows and timed OpenSearch
    segments are untouched, so timeline seeking stays segment-accurate.
    Returns [(text, anchor), ...] — non-informative units (no real word) dropped."""
    windows: list[tuple[str, object]] = []
    buf: list[str] = []
    anchor = None
    wc = 0

    def flush():
        nonlocal buf, anchor, wc
        if buf:
            windows.append((" ".join(buf), anchor))
            buf, anchor, wc = [], None, 0

    for text, anc in zip(seg_texts, anchors):
        if len(text.split()) >= min_words:
            flush()                          # close any pending short-run first
            windows.append((text, anc))      # substantial segment stands alone
        else:
            if not buf:
                anchor = anc
            buf.append(text)
            wc += len(text.split())
            if wc >= min_words:
                flush()
    flush()
    # Drop units with no real words — Whisper emits "🎵" / punctuation for music-only or silent
    # audio; such a vector is a pure black hole (cosine ~0.5 vs ANY query) with no content.
    return [(t, a) for (t, a) in windows if is_informative(t)]


def is_informative(text: str) -> bool:
    """True if the text has at least one run of 2+ letters — i.e. real words, not just emoji,
    digits, or punctuation. Guards the dense index against degenerate '🎵' transcript vectors."""
    return bool(re.search(r"[^\W\d_]{2,}", text or ""))


def first_heading(markdown: str) -> str | None:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()[:200]
        if line:
            return line[:200]
    return None
