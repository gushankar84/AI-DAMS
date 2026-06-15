"""Scene/activity captioning + on-image OCR via a multimodal VLM served by Ollama.

Disabled unless settings.caption_backend == "ollama" and the model is pulled.
Returns empty results otherwise — enrichment, never a hard dependency.

`describe()` does BOTH the descriptive caption and verbatim text extraction in a
SINGLE VLM pass (the call dominates ingest time), on a downscaled image (fewer
vision tokens => faster). `caption()`/`ocr()` remain for incremental backfills.
"""
from __future__ import annotations

import base64
import io
import logging
import re

import httpx

from .config import settings

log = logging.getLogger("dam.caption")

# STRUCTURED prompt: the VLM returns labelled FIELDS (parsed apart below) so each becomes a
# searchable/filterable facet — objects, people, actions, intent, on-image text. Neutral: it
# lists what it ACTUALLY sees, never answering a leading question ("find the drum" biases it to
# confirm one). Objects/actions/intent get EQUAL billing with people, so a background instrument
# or the scene's activity is captured. Garment colours stay in PEOPLE so "red shirt" binding works.
DESCRIBE_PROMPT = (
    "Describe this image for a search index. State ONLY what is clearly visible — never guess, "
    "infer, or add what you would expect. Respond in EXACTLY this format, one line per field:\n"
    "PEOPLE: <count + each person's gender/approx age + garments by SPECIFIC name and COLOUR "
    "(blue shirt, red dupatta, green saree, yellow kurta); or NONE>\n"
    "OBJECTS: <comma-separated prominent objects you can clearly see, by name (drum, lamp, "
    "basket, flag, bicycle, vehicle, weapon, food, instrument, tool); or NONE>\n"
    "ACTIONS: <comma-separated what the subjects are doing (walking, dancing, playing a drum, "
    "cooking, praying, riding, fighting), AND any CLEARLY-VISIBLE facial expression only if "
    "obvious (smiling, laughing, crying, frowning) — do NOT infer mood you cannot see; or NONE>\n"
    "INTENT: <one short phrase for the scene's context or purpose (festival, procession, market, "
    "wedding, portrait, confrontation, performance)>\n"
    "TEXT: <ALL visible on-image text/signage/subtitles VERBATIM, in any language or script "
    "(Latin, Devanagari, Telugu, Tamil, Arabic); or NONE>\n"
    "Be concrete and specific. Only state what is visible; do not guess identities or add opinions."
)

# Standalone prompts (kept for the OCR-only / caption-only backfill paths).
PROMPT = (
    "Describe this image factually for a search index in 3-5 sentences, stating ONLY what is "
    "clearly visible (never guess): people (count, gender, approx age, garments by name and "
    "colour); every prominent object by name; the action the subjects are doing; the setting "
    "and the apparent context/intent of the scene. Be specific; do not invent."
)
OCR_PROMPT = (
    "You are an OCR engine. Transcribe ALL text visible in this image EXACTLY as it appears, "
    "preserving reading order. Include signage, posters, captions, subtitles, labels, document "
    "body text, and handwriting, in ANY language or script (Latin, Devanagari, Telugu, Tamil, "
    "Arabic, etc.). Output ONLY the transcribed text. If there is no readable text, output: NONE"
)


def _downscaled_b64(image_path: str, max_side: int = 1024) -> str:
    """Base64 JPEG with the longest side capped at max_side. Fewer vision tokens =>
    materially faster VLM inference, no meaningful loss for captioning/OCR of clear
    content. Falls back to the raw bytes if PIL/decoding fails."""
    try:
        from PIL import Image
        with Image.open(image_path) as src:   # close the file handle (was leaked every call)
            im = src.convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
        im.close()
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        log.warning("downscale failed (%s); sending original bytes", e)
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode()


def _generate(prompt: str, b64: str) -> str:
    # Retry once on an empty/failed response: after a video job evicts the VLM (keep_alive=0
    # for Whisper), the next image's describe can cold-load and the first call may come back
    # empty — which would silently drop the caption on (re)ingest. A short retry recovers it.
    import time
    for attempt in (1, 2):
        try:
            r = httpx.post(
                f"{settings.ollama_url}/api/generate",
                json={"model": settings.ollama_vlm_model, "prompt": prompt,
                      "images": [b64], "stream": False, "keep_alive": "10m"},
                timeout=180,
            )
            r.raise_for_status()
            resp = (r.json().get("response") or "").strip()
            if resp:
                return resp
        except Exception as e:
            log.warning("ollama VLM call failed (attempt %d): %s", attempt, e)
        if attempt == 1:
            time.sleep(3)  # give a cold-loading model a moment, then retry
    return ""


_FIELDS = ("PEOPLE", "OBJECTS", "ACTIONS", "INTENT", "TEXT")


# A VLM often narrates what is ABSENT ("No prominent objects like phones, cars, or buildings are
# visible"), which then makes those absent nouns falsely searchable (search "car" returns a photo
# whose caption says there is NO car). Drop a sentence ONLY when it LEADS with a negation AND has a
# presence/visibility verb — i.e. it is a pure absence statement. A descriptive sentence that ends
# with a small absence clause ("a four-step workflow with no people visible") is KEPT, so we never
# lose real content like "workflow". (Captions only — document bodies, where "no X" is real
# content, are never passed through this.)
_NEG = r"\b(?:no|not|without|none|nothing|absent|absence|lack|free of|n['’]t)\b"
_LEAD_NEG = re.compile(r"""^\s*["'“”']?\s*(?:there\s+(?:are|is)\s+)?(?:no|none|nothing|without)\b""", re.I)
_PRESENCE = (r"\b(?:visible|present|presence|seen|depicted|observable|observed|detected|"
             r"discernible|displayed|shown)\b")
_SENT = re.compile(r"[^.!?]*[.!?]")


def _is_absence(s: str) -> bool:
    return bool(_LEAD_NEG.search(s) and re.search(_PRESENCE, s, re.I))


def strip_absence(text: str) -> str:
    """Remove pure 'absence of X' sentences so absent objects don't become searchable keywords."""
    if not text:
        return text
    kept = [s for s in _SENT.findall(text) if not _is_absence(s)]
    out = " ".join(s.strip() for s in kept).strip()
    tail = _SENT.sub("", text).strip()           # trailing fragment with no end punctuation
    if tail and not _is_absence(tail):
        out = f"{out} {tail}".strip()
    return out


def _none(v: str) -> str:
    return "" if v.strip().upper().rstrip(". ") == "NONE" else v.strip()


def _list(v: str) -> list[str]:
    v = _none(v)
    return [x.strip().lower() for x in re.split(r"[;,]", v) if x.strip()] if v else []


def _parse_structured(resp: str) -> dict:
    """Parse the labelled PEOPLE/OBJECTS/ACTIONS/INTENT/TEXT response into structured fields,
    plus a derived `caption` (for the dense vector + the result snippet). The caption keeps the
    PEOPLE text verbatim so garment phrases ('red shirt') survive for attribute binding."""
    def grab(label: str) -> str:
        m = re.search(rf"(?is)\b{label}\s*:(.*?)(?=\n\s*(?:{'|'.join(_FIELDS)})\s*:|\Z)", resp)
        return m.group(1).strip() if m else ""
    people = strip_absence(_none(grab("PEOPLE")))
    # Object/action lists: drop any item that is itself an absence phrase ("no cars", "none").
    objects = [o for o in _list(grab("OBJECTS")) if not re.search(_NEG, o, re.I)]
    actions = [a for a in _list(grab("ACTIONS")) if not re.search(_NEG, a, re.I)]
    intent = strip_absence(_none(grab("INTENT")))
    text = _none(grab("TEXT"))
    parts = [people]
    if actions:
        parts.append("Activity: " + ", ".join(actions) + ".")
    if objects:
        parts.append("Objects: " + ", ".join(objects) + ".")
    if intent:
        parts.append(intent + ".")
    caption = " ".join(p for p in parts if p).strip()
    return {"caption": caption, "text": text, "people": people,
            "objects": objects, "actions": actions, "intent": intent}


def describe(image_path: str, max_side: int = 1024) -> dict:
    """ONE VLM pass returning STRUCTURED scene fields + verbatim on-image text:
    {caption, text, people, objects[], actions[], intent}. Objects/actions/intent become
    searchable facets; `caption` is the readable roll-up for the dense vector + snippet.
    max_side: VLM vision tokens scale with input size — measured 1024px≈69s / 768≈49s /
    512≈30s per call on this box, with tags (garment colours, objects) intact at 512.
    Video keyframes pass 512 (≈2× faster tagging); images keep 1024 for OCR quality."""
    if settings.caption_backend != "ollama":
        return {"caption": "", "text": "", "people": "", "objects": [], "actions": [], "intent": ""}
    return _parse_structured(_generate(DESCRIBE_PROMPT, _downscaled_b64(image_path, max_side)))


def _generate_text(prompt: str) -> str:
    """Text-only VLM call (no image) — used for asset summaries."""
    try:
        r = httpx.post(f"{settings.ollama_url}/api/generate",
                       json={"model": settings.ollama_vlm_model, "prompt": prompt,
                             "stream": False, "keep_alive": "10m"}, timeout=120)
        r.raise_for_status()
        return (r.json().get("response") or "").strip()
    except Exception as e:
        log.warning("summarize/text-gen failed: %s", e)
        return ""


def summarize(notes: str, kind: str = "video") -> str:
    """One-sentence asset summary from the per-shot tags (video) or extracted text (doc).
    Grounded only in `notes`; never invents."""
    if settings.caption_backend != "ollama" or not notes.strip():
        return ""
    prompt = (
        f"Summarise what this {kind} is about in ONE concise sentence for a search index — the "
        f"main subjects, objects, actions, and overall context/intent. Use ONLY the notes below; "
        f"do not invent anything not stated.\n\nNOTES:\n{notes[:4000]}\n\nSUMMARY:")
    return _generate_text(prompt).strip()


def ocr(image_path: str) -> str:
    """Dedicated text extraction only (for incremental OCR backfill of already-enriched
    assets). Multilingual via the VLM. Returns '' when there is no text."""
    if settings.caption_backend != "ollama":
        return ""
    text = _generate(OCR_PROMPT, _downscaled_b64(image_path))
    return "" if text.upper().rstrip(". ") == "NONE" else text


def caption(image_path: str) -> str:
    """Descriptive caption only (kept for backward compatibility)."""
    if settings.caption_backend != "ollama":
        return ""
    return _generate(PROMPT, _downscaled_b64(image_path))


def judge_relevance(query: str, items: list[str]) -> list[int]:
    """LLM relevance judge: given a search query and candidate result texts, return the
    indices (0-based) of the items that GENUINELY match the query's intent — judged by
    meaning, across languages (so it catches cross-lingual paraphrase the cross-encoder
    misses, and rejects tangential semantic noise). Best-effort: on any failure or a
    parse miss, returns ALL indices (an LLM hiccup must never empty the results)."""
    if settings.caption_backend != "ollama" or not items:
        return list(range(len(items)))
    numbered = "\n".join(f"{i + 1}. {(t or '')[:280]}" for i, t in enumerate(items))
    prompt = (
        f'A user searched a media library for: "{query}"\n\n'
        f"Below are candidate results (their transcript / caption / text):\n{numbered}\n\n"
        "Reply with ONLY the numbers of the items that genuinely match what the user is "
        "looking for, comma-separated (e.g. 2,5). Judge by MEANING and across languages. "
        "Be strict — exclude items only loosely or tangentially related. "
        "If NONE of them match, reply exactly: NONE"
    )
    try:
        r = httpx.post(f"{settings.ollama_url}/api/generate",
                       json={"model": settings.ollama_vlm_model, "prompt": prompt, "stream": False},
                       timeout=60)
        r.raise_for_status()
        resp = (r.json().get("response") or "").strip()
        if resp.upper().startswith("NONE"):
            return []
        nums = [int(n) for n in re.findall(r"\d+", resp)]
        idxs = [n - 1 for n in nums if 1 <= n <= len(items)]
        return idxs if idxs else list(range(len(items)))  # parse miss → keep all (safe)
    except Exception as e:
        log.warning("llm relevance judge unavailable: %s", e)
        return list(range(len(items)))


def unload() -> None:
    """Ask Ollama to evict the VLM (keep_alive=0) so a different heavy model
    (Whisper ASR) can claim VRAM/commit. On a memory-tight host the VLM and
    Whisper cannot coexist, so ASR calls this first."""
    if settings.caption_backend != "ollama":
        return
    try:
        import time
        httpx.post(f"{settings.ollama_url}/api/generate",
                   json={"model": settings.ollama_vlm_model, "keep_alive": 0}, timeout=30)
        # WAIT until Ollama has actually unloaded the VLM (poll /api/ps), not a fixed sleep —
        # the 6GB must be freed BEFORE Whisper loads or they coexist and OOM-crash the server.
        for _ in range(30):                       # up to ~15s
            time.sleep(0.5)
            try:
                models = httpx.get(f"{settings.ollama_url}/api/ps", timeout=5).json().get("models", [])
            except Exception:
                break
            if not any(settings.ollama_vlm_model.split(":")[0] in (m.get("model") or m.get("name") or "")
                       for m in models):
                log.info("VLM evicted; GPU freed for Whisper")
                break
        time.sleep(1.0)  # small grace for the OS to reclaim the commit
    except Exception as e:
        log.warning("ollama unload failed: %s", e)
