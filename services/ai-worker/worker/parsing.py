"""Document parsing (Docling) — server-side so torch stays in one process."""
from __future__ import annotations

import logging

log = logging.getLogger("dam.parsing")
_converter = None


def _conv():
    global _converter
    if _converter is None:
        # Docling's default already sets do_ocr=True, but no OCR engine is installed, so it
        # extracts only the text LAYER (fine for digital PDFs). Scanned/image-only PDFs get
        # a VLM-OCR fallback in the document pipeline instead (multilingual; reuses Qwen3-VL).
        from docling.document_converter import DocumentConverter
        _converter = DocumentConverter()
    return _converter


def parse_to_markdown(path: str) -> str:
    """Parse a document into Markdown. PDFs are extracted PER PAGE with `[[PAGE n]]`
    sentinels so search hits can deep-link to the exact page (the pipeline maps each text
    chunk to its page; the UI opens the PDF at `#page=n`). Scanned/image-only PDFs fall
    back to VLM OCR of the rendered pages (also page-tagged). Non-PDFs use Docling."""
    if path.lower().endswith(".pdf"):
        md = _pdf_pages_markdown(path)
        if md:
            return md
        # Scanned PDF (no text layer): return "" — the WORKER orchestrates per-page OCR with
        # checkpoints (one short call per page, resume skips finished pages). The old design
        # (all pages inside THIS one call) was an all-or-nothing timeout bomb: 4 failed runs,
        # each losing every completed page.
        return ""
    md = ""
    try:
        md = _conv().convert(path).document.export_to_markdown()
    except Exception as e:
        log.warning("Docling parse failed (%s); plain-text fallback", e)
        try:
            with open(path, "rb") as f:
                md = f.read().decode("utf-8", errors="ignore")
        except Exception:
            md = ""
    return md


def _pdf_pages_markdown(path: str) -> str:
    """Per-page text of a digital PDF, tagged with `[[PAGE n]]` sentinels. Returns '' for
    scanned PDFs (no real text layer) so the VLM-OCR fallback takes over."""
    try:
        import fitz
        doc = fitz.open(path)
        try:
            pages = [(i + 1, (doc[i].get_text("text") or "").strip()) for i in range(doc.page_count)]
        finally:
            doc.close()
        total = sum(len(t) for _, t in pages)
        if total < 40 * max(len(pages), 1):   # ~no text layer → scanned
            return ""
        return "\n\n".join(f"[[PAGE {n}]]\n{t}" for n, t in pages if t)
    except Exception as e:
        log.warning("per-page PDF extract failed (%s)", e)
        return ""


def _vlm_ocr_scanned_pdf(path: str, md: str, max_pages: int = 15) -> str:
    """If a PDF has ~no extracted text (scanned/image-only), render its pages and OCR them
    with the multilingual VLM. Digital PDFs (real text layer) are skipped. Best-effort."""
    try:
        import os
        import tempfile
        import fitz  # PyMuPDF
        from . import caption
        doc = fitz.open(path)
        out = []
        try:
            n = doc.page_count
            if len(md.strip()) >= 40 * max(n, 1):   # has a genuine text layer → not scanned
                return ""
            with tempfile.TemporaryDirectory() as tmp:
                for i in range(min(n, max_pages)):
                    p = os.path.join(tmp, f"p{i}.jpg")
                    doc[i].get_pixmap(dpi=150).save(p)
                    t = caption.ocr(p)
                    if t:
                        # Page-tagged so OCR'd scanned PDFs also deep-link to the page.
                        out.append(f"[[PAGE {i + 1}]]\n{t}")
        finally:
            doc.close()   # PyMuPDF keeps the file mmap'd until closed — was leaked every call
        if out:
            log.info("VLM-OCR'd scanned PDF %s (%d/%d pages)", os.path.basename(path), len(out), n)
        return "\n\n".join(out)
    except Exception as e:
        log.warning("VLM OCR fallback failed (%s)", e)
        return ""
