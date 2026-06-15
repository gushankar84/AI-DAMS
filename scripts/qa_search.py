r"""Production QA — comprehensive search battery.

Runs labelled queries across every modality/attribute, records top hits + latency,
and flags PASS / WARN / FAIL against expectations. Writes a UTF-8 report.

Usage: python scripts/qa_search.py
"""
import io
import statistics
import time

import httpx

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=60)
tok = c.post("/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

# (category, query, types, expect_substr_in_top3 | None, expect_results: True/False/None)
BATTERY = [
    # ── garment + colour (the original failure class) ──
    ("garment", "red dress", ["image"], None, True),
    ("garment", "blue shirt", ["image"], None, True),
    ("garment", "green saree", ["image"], None, True),
    ("garment", "pink dupatta", ["image"], "2026-06-08", True),
    ("garment", "white shirt", ["image"], None, True),
    # ── scene / relationship ──
    ("scene", "couple at sunset", ["image"], "2026-06-08", True),
    ("scene", "a person standing outdoors", ["image"], None, True),
    ("scene", "people on a stage", ["image"], None, None),
    # ── on-image text (OCR via caption) ──
    ("ocr", "forever in our hearts", ["image"], "2026-06-08", True),
    # ── objects ──
    ("object", "eyeglasses", ["image"], None, None),
    ("object", "a book", None, None, None),
    # ── people ──
    ("people", "a man with a beard", ["image"], None, True),
    ("people", "an older woman", ["image"], None, True),
    # ── documents (semantic) ──
    ("doc", "media asset management architecture", ["document"], None, True),
    ("doc", "frame accurate timecode", ["document"], None, None),
    # ── cross-lingual (English query -> Hindi audio) ──
    ("xling", "an orphan raised in a temple", ["audio"], None, None),
    # ── no-result / out-of-domain (should return few/none, not garbage) ──
    ("neg", "a spaceship orbiting Saturn", ["image"], None, False),
    ("neg", "underwater coral reef scuba diving", ["image"], None, False),
    # ── edge cases ──
    ("edge", "", None, None, None),                       # empty query
    ("edge", "asdfqwerzxcv", None, None, False),          # gibberish
    ("edge", "'; DROP TABLE asset;--", None, None, None),  # injection-like
    ("edge", "लाल साड़ी", ["image"], None, None),          # Devanagari query
    ("edge", "a " * 200, None, None, None),               # very long query
]


def run(q, types):
    body = {"q": q, "limit": 5}
    if types:
        body["types"] = types
    t0 = time.perf_counter()
    r = c.post("/api/search", headers=H, json=body)
    dt = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    return r.json(), dt


rep = io.StringIO()
lat = []
passes = warns = fails = 0
rep.write("=== QA SEARCH BATTERY ===\n\n")
for cat, q, types, expect_sub, expect_res in BATTERY:
    try:
        res, dt = run(q, types)
        lat.append(dt)
        hits = res["hits"]
        names = [h["filename"] for h in hits]
        top3 = names[:3]
        verdict = "PASS"
        note = ""
        if expect_sub is not None:
            if not any(expect_sub in n for n in top3):
                verdict, note = "FAIL", f"expected '{expect_sub}' in top3"
        if expect_res is True and len(hits) == 0:
            verdict, note = "FAIL", "expected results, got 0"
        if expect_res is False and len(hits) > 2:
            verdict, note = "WARN", f"expected ~none, got {len(hits)}"
        passes += verdict == "PASS"; warns += verdict == "WARN"; fails += verdict == "FAIL"
        qd = (q[:40] + "…") if len(q) > 40 else q
        rep.write(f"[{verdict}] ({cat}) '{qd}'  {dt:.0f}ms  n={res['total']}  {note}\n")
        rep.write(f"        top: {', '.join(top3) if top3 else '(none)'}\n")
    except Exception as e:
        fails += 1
        rep.write(f"[ERROR] ({cat}) '{q[:40]}'  -> {type(e).__name__}: {str(e)[:120]}\n")

rep.write("\n=== SUMMARY ===\n")
rep.write(f"PASS={passes}  WARN={warns}  FAIL={fails}  (of {len(BATTERY)})\n")
if lat:
    rep.write(f"latency ms: p50={statistics.median(lat):.0f}  max={max(lat):.0f}  mean={statistics.mean(lat):.0f}\n")

with open(r"E:\dam-platform\.data\qa_search_report.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print(f"PASS={passes} WARN={warns} FAIL={fails}; report written")
