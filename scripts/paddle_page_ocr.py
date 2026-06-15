"""Single-page OCR via PaddleOCR — run INSIDE the .paddle-venv (its numpy<2 pin conflicts
with the worker venv, so the worker shells out to this script as a subprocess).

Usage:  .paddle-venv/Scripts/python.exe scripts/paddle_page_ocr.py <image_path>
Output: UTF-8 JSON on stdout: {"lines": [...], "text": "..."}

Runs the Latin model + Devanagari model and merges unique lines (the corpus is mixed-script:
music scores, signboards, Indic book scans). det_limit_side_len=2000 — the default 960
downscale blinds detection on large document scans (measured: 0 → 12 regions).
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
path = sys.argv[1]

from paddleocr import PaddleOCR  # noqa: E402

lines: list[str] = []
seen: set[str] = set()
for lang in ("en", "devanagari"):
    try:
        ocr = PaddleOCR(lang=lang, use_angle_cls=False, show_log=False,
                        det_limit_side_len=2000, det_limit_type="max")
        res = ocr.ocr(path, cls=False)
        for page in (res or []):
            for line in (page or []):
                try:
                    t = line[1][0].strip()
                except Exception:
                    continue
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    lines.append(t)
    except Exception as e:
        print(json.dumps({"error": f"{lang}: {e}"}), file=sys.stderr)

print(json.dumps({"lines": lines, "text": "\n".join(lines)}, ensure_ascii=False))
