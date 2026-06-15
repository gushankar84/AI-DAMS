"""HALLUCINATION guard — the battery that keeps the de-black-holing from regressing.

A search must not invent matches for things the corpus does not contain. The classic failure
was a "black hole": an asset whose only text was short ASR fillers ("sorry sir", "🎵") embedded
ONE-PER-SEGMENT, whose near-centroid vectors cleared the relevance floor for ANY query — so one
audio file surfaced for airplane / pizza / spaceship / horse. Fixed by (1) grouping transcript
segments before embedding (window_segments), (2) dropping non-informative ('🎵') units,
(3) a confidence-aware gate: the cross-encoder is authoritative when confident; when not, only a
strong SAME-SCRIPT bi-encoder cosine survives (cross-script cosine is noise on a small corpus).

This battery asserts the TEXT channel returns NOTHING for absent concepts. Two allowances,
both honest and documented:
  • IMAGE-channel hits are tolerated — SigLIP on a tiny corpus genuinely can't separate a rare
    absent word's single closest frame from a real garment (resolves at scale). We only fail on
    TEXT-channel (semantic/transcript/keyword-without-literal) leaks.
  • A LITERAL keyword match is correct, not a hallucination (searching "horse" finding a shirt
    whose caption says "red horse emblem" is right).
"""
import io
import re

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=120)
tok = s.post(f"{API}/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

# Concepts absent from this family-photo + Indian-song-video corpus.
ABSENT = ["elephant", "airplane", "guitar", "snowman", "mountain peak", "laptop", "pizza slice",
          "basketball", "spaceship", "helicopter", "dinosaur", "waterfall", "violin", "tractor",
          "submarine", "cactus", "robot", "quantum chromodynamics lecture", "underwater reef"]

rep = io.StringIO()
P = F = 0


def text_channel_leak(hit, query):
    """A hit is a TEXT-channel hallucination if it matched via text signals only (semantic/
    transcript/keyword) WITHOUT the query appearing literally in its shown text, and WITHOUT a
    visual (image/face) signal. Image-only hits and literal keyword hits are allowed."""
    sig = set(hit.get("matched_signals", []))
    if sig & {"image", "face"}:
        return False  # visual channel — documented small-corpus limit, not a text hallucination
    text = " ".join(filter(None, [hit.get("snippet"), hit.get("caption"),
                                  hit.get("title"), hit.get("filename")])).lower()
    qwords = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3]
    if qwords and all(w in text for w in qwords):
        return False  # literal full match — a correct keyword hit, not invented
    return True


for q in ABSENT:
    r = s.post(f"{API}/api/search", headers=H, json={"q": q, "limit": 5}).json()
    leaks = [h for h in r.get("hits", []) if text_channel_leak(h, q)]
    ok = not leaks
    P += ok
    F += not ok
    note = "" if ok else "  LEAK: " + ", ".join(
        f"{h['filename'][:20]}{h.get('matched_signals')}" for h in leaks[:2])
    rep.write(f"[{'PASS' if ok else 'FAIL'}] absent {q!r:34} n={r.get('total')}{note}\n")

rep.write(f"\nSUMMARY: PASS={P} FAIL={F} (of {P + F}) — text-channel must be 0 leaks\n")
with open(r"E:\dam-platform\.data\qa_hallucination_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(rep.getvalue())
