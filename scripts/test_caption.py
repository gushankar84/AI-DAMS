r"""Validate the VLM caption on a specific image before mass reprocessing."""
import base64
import sys

import httpx

IMG = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\PCPL\Downloads\WhatsApp Image 2026-06-08 at 2.18.30 PM.jpeg"
MODEL = "qwen3-vl:8b"
PROMPT = (
    "Describe this image factually for a search index. In 2-4 sentences include, when present:\n"
    "- People: how many, men/women, approximate age.\n"
    "- Clothing: each person's garments with SPECIFIC names and COLOURS "
    "(e.g. blue shirt, red dupatta, green saree, black suit, yellow kurta).\n"
    "- Main objects (phone, car, building, instrument, etc.).\n"
    "- Setting/scene: indoor or outdoor, location type, time of day, notable background (sunset, lake, studio).\n"
    "- Any activity or action (talking, dancing, singing, walking, an interview).\n"
    "- Transcribe any visible text or signage VERBATIM.\n"
    "Be concrete and specific. Do not guess people's identities and do not add opinions."
)

with open(IMG, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

r = httpx.post("http://127.0.0.1:11434/api/generate",
               json={"model": MODEL, "prompt": PROMPT, "images": [b64], "stream": False},
               timeout=300)
cap = r.json().get("response", "").strip()
with open(r"E:\dam-platform\.data\caption_test.txt", "w", encoding="utf-8") as f:
    f.write(cap)
print("caption written, length", len(cap))
