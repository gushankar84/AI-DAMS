r"""VLM bake-off: caption the same images with each model, record quality + latency.
All local via Ollama. Writes a UTF-8 report for side-by-side comparison.
"""
import base64
import io
import time

import httpx

PROMPT = (
    "Describe this image factually for a search index. In 2-4 sentences include, when present:\n"
    "- People: how many, men/women, approximate age.\n"
    "- Clothing: each person's garments with SPECIFIC names and COLOURS "
    "(e.g. blue shirt, red dupatta, green saree, black suit, yellow kurta).\n"
    "- Main objects.\n- Setting/scene: indoor/outdoor, location, time of day, background.\n"
    "- Any activity or action.\n- Transcribe any visible text or signage VERBATIM.\n"
    "Be concrete and specific. Do not guess identities or add opinions."
)

IMAGES = [
    ("memorial (English text + couple)", r"C:\Users\PCPL\Downloads\WhatsApp Image 2026-06-08 at 2.18.30 PM.jpeg"),
    ("song keyframe (Bheegey Honth Tere)", r"E:\dam-platform\.data\song_keyframe.jpg"),
]
MODELS = ["qwen3-vl:8b", "gemma4:e4b"]
URL = "http://127.0.0.1:11434/api/generate"


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def gen(model, prompt, image_b64=None, timeout=300):
    payload = {"model": model, "prompt": prompt, "stream": False}
    if image_b64:
        payload["images"] = [image_b64]
    r = httpx.post(URL, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json().get("response", "").strip()


rep = io.StringIO()
imgs = [(n, b64(p)) for n, p in IMAGES]
for model in MODELS:
    rep.write(f"\n{'='*70}\nMODEL: {model}\n{'='*70}\n")
    try:
        gen(model, "hi", timeout=300)  # warmup / load
    except Exception as e:
        rep.write(f"  WARMUP FAILED: {e}\n")
    for name, image in imgs:
        try:
            t0 = time.perf_counter()
            cap = gen(model, PROMPT, image)
            dt = time.perf_counter() - t0
            rep.write(f"\n--- {name}  ({dt:.1f}s) ---\n{cap}\n")
        except Exception as e:
            rep.write(f"\n--- {name} ---\n  ERROR: {e}\n")

with open(r"E:\dam-platform\.data\bakeoff_report.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print("bakeoff written")
