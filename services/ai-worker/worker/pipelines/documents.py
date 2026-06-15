"""Document pipeline (TSA §4.2).

    Parse (Docling, server-side) -> structured Markdown -> chunk + embed (BGE-M3)
                                 -> Qdrant dam_text + OpenSearch BM25 body

Visual multi-vector page retrieval (ColQwen3) is layered on in P1-advanced. All
ML runs in the serving process; this module only orchestrates + indexes.
"""
from __future__ import annotations

import logging
import re
import uuid

from .. import serving_client, stores
from ..config import QDRANT_TEXT, settings
from .common import chunk_text, first_heading

log = logging.getLogger("dam.pipeline.documents")

_PAGE_RE = re.compile(r"\[\[PAGE (\d+)\]\]\n?")


def _paged_chunks(markdown: str) -> tuple[str, list[tuple[str, int | None]]]:
    """Split `[[PAGE n]]`-tagged markdown into (clean_markdown, [(chunk, page)]).
    Each chunk knows which PAGE it came from, so a search hit can open the document AT
    that page. Untagged docs (docx etc.) chunk normally with page=None."""
    if not _PAGE_RE.search(markdown):
        return markdown, [(c, None) for c in chunk_text(markdown)]
    parts = _PAGE_RE.split(markdown)          # [pre, n1, text1, n2, text2, ...]
    pairs: list[tuple[str, int | None]] = []
    clean: list[str] = [parts[0]] if parts[0].strip() else []
    for k in range(1, len(parts) - 1, 2):
        page, text = int(parts[k]), parts[k + 1]
        if text.strip():
            clean.append(text)
            pairs += [(c, page) for c in chunk_text(text)]
    return "\n\n".join(clean), pairs


def _paddle_ocr(image_path: str) -> str:
    """One page through PaddleOCR in its own venv (subprocess). '' on any failure —
    the VLM tier covers it."""
    import json
    import subprocess
    try:
        r = subprocess.run([settings.paddle_python, settings.paddle_script, image_path],
                           capture_output=True, timeout=180)
        if r.returncode == 0:
            return json.loads(r.stdout.decode("utf-8", errors="ignore")).get("text", "")
        log.warning("paddle OCR rc=%d: %s", r.returncode, r.stderr[:200])
    except Exception as e:
        log.warning("paddle OCR failed (%s)", e)
    return ""


def _ocr_pdf_pages(asset: dict, max_pages: int = 15) -> str:
    """Per-page, CHECKPOINTED OCR for scanned PDFs. Each page is ONE short serving call and
    its text is persisted immediately (MinIO sidecar) — a crash/timeout/reboot costs one page,
    and a re-run RESUMES from the first unfinished page instead of redoing all of them.
    Replaces the all-pages-in-one-call design that failed 4 consecutive runs."""
    import os
    import tempfile
    import fitz
    aid = asset["id"]
    out: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "doc.pdf")
        stores.download_to(asset["storage_uri"], local)
        doc = fitz.open(local)
        try:
            n = min(doc.page_count, max_pages)
            for i in range(1, n + 1):
                txt = stores.get_text(f"docpages/{aid}/p{i}.txt")   # resume checkpoint
                if txt is None:
                    img = os.path.join(tmp, f"p{i}.jpg")
                    doc[i - 1].get_pixmap(dpi=150).save(img)
                    uri = stores.upload_file(img, f"docpages/{aid}/p{i}.jpg", "image/jpeg")
                    txt = ""
                    # TIER 1: PaddleOCR (CPU subprocess, ~3-7s, no GPU contention). Measured on
                    # this corpus: caught full headers + Devanagari fragments where the VLM
                    # returned empty. Own venv (numpy pin) → subprocess.
                    if settings.ocr_paddle_first:
                        txt = _paddle_ocr(img)
                    # TIER 2: VLM — only when Paddle found ~nothing on this page.
                    if len(txt.strip()) < settings.ocr_min_chars:
                        vlm = serving_client.ocr(uri, f"p{i}.jpg") or ""
                        if len(vlm.strip()) > len(txt.strip()):
                            txt = vlm
                    if txt.strip():
                        # only checkpoint REAL text — an empty result (cold VLM) retries next run
                        stores.put_text(f"docpages/{aid}/p{i}.txt", txt)
                if txt.strip():
                    out.append(f"[[PAGE {i}]]\n{txt}")
                log.info("doc OCR %s: page %d/%d %s", aid, i, n, "ok" if txt.strip() else "(empty)")
        finally:
            doc.close()
    return "\n\n".join(out)


async def process(asset: dict) -> None:
    asset_id = asset["id"]
    await stores.set_status(asset_id, "processing")

    await stores.set_status(asset_id, "extracting")
    raw = serving_client.parse_document(asset["storage_uri"], asset["filename"])
    if not raw.strip() and asset["filename"].lower().endswith(".pdf"):
        # scanned PDF → per-page checkpointed OCR (see _ocr_pdf_pages)
        raw = _ocr_pdf_pages(asset)
    markdown, paged = _paged_chunks(raw)      # sentinels never reach the index/snippets

    title = asset.get("title") or first_heading(markdown) or asset["filename"]
    # One-line summary of the document ("what is this about") — searchable + shown in the UI.
    summary = serving_client.summarize(markdown[:4000], "document") if markdown.strip() else ""

    if paged:
        vectors = serving_client.embed_texts([c for c, _ in paged])
        points = [{
            "id": str(uuid.uuid4()),
            "vector": vec,
            "payload": {
                "asset_id": asset_id, "asset_type": "document",
                "department": asset.get("department"), "project": asset.get("project"),
                "chunk_index": i, "snippet": paged[i][0][:300],
                "page": paged[i][1],          # → "p.N ▶" chip; PDF opens at #page=N
            },
        } for i, vec in enumerate(vectors)]
        stores.upsert_vectors(QDRANT_TEXT, points)

    stores.index_asset_doc({
        "asset_id": asset_id, "asset_type": "document", "title": title,
        "description": summary or asset.get("description"), "body": markdown[:200_000],
        "summary": summary,
        "tags": asset.get("tags") or [], "department": asset.get("department"),
        "project": asset.get("project"), "language": asset.get("language"),
        "created_at": asset["created_at"].isoformat() if asset.get("created_at") else None,
    })

    if summary:
        await stores.set_description(asset_id, summary)
    await stores.set_asset_text(asset_id, title, asset.get("language"))
    await stores.set_status(asset_id, "searchable")
    npages = len({p for _, p in paged if p is not None})
    log.info("document indexed: %s (%d chunks, %d pages mapped)", asset_id, len(paged), npages)
