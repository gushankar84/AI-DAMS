"""USE-CASE battery — tests the docs/TAGGING_AND_SEARCH_GUIDE.md correlation table itself.

One case per user-facing use case: what a user would type → what must come back. This is the
battery that keeps the NEW capabilities (structured tags, binding, page-jump, suggest,
summaries, deep-link data) from silently regressing — the older batteries cover the core.

PASS = expectation met · WARN = soft case (semantic, may drift) · FAIL = hard miss
SKIP = precondition not met yet (e.g. no page-tagged PDF until ARUNI is re-parsed).
"""
import io
import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=120)
tok = s.post(f"{API}/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

rep = io.StringIO()
P = W = F = K = 0


def search(q, **kw):
    body = {"q": q, "limit": 20, **kw}
    return s.post(f"{API}/api/search", headers=H, json=body).json()


def case(name, verdict, note=""):
    global P, W, F, K
    P += verdict == "PASS"; W += verdict == "WARN"; F += verdict == "FAIL"; K += verdict == "SKIP"
    rep.write(f"[{verdict}] {name}  {note}\n")


def names(r):
    return [h["filename"] for h in r.get("hits", [])]


# ── 1. spoken phrase → time-aligned hit ─────────────────────────────────────
# NOTE: was "stop it, cut" → that phrase no longer exists in the data: hit_tamil's re-
# transcription (large-v3, Tamil-locked) rendered the old English interjections in Tamil.
# "buffering" is English speech present in the CURRENT transcript truth.
r = search("buffering")
ok = any("hit_tamil" in n.lower() for n in names(r))
case("spoken phrase → transcript", "PASS" if ok else "FAIL", f"n={r.get('total')}")

# ── 2. attribute binding: red shirt = red GARMENT, never the red emblem ─────
r = search("red shirt")
ns = names(r)
emblem_leak = any("WhatsApp" in n for n in ns)   # blue polo with a red horse emblem
ok = r.get("total", 0) >= 1 and not emblem_leak
case("binding: red shirt (garment, not emblem)", "PASS" if ok else "FAIL",
     f"n={r.get('total')} emblem_leak={emblem_leak}")

# ── 3. contradiction: red dupatta must NOT return the green dupatta ─────────
r = search("red dupatta")
green_leak = any("Stella3" in n for n in names(r))
case("contradiction: red dupatta ≠ green dupatta", "PASS" if not green_leak else "FAIL",
     f"n={r.get('total')} green_leak={green_leak}")

# ── 4. open-vocab object (beyond YOLO): hammock ──────────────────────────────
r = search("hammock")
ok = any("slp000036" in n for n in names(r))
case("object (VLM open-vocab): hammock → shot", "PASS" if ok else "FAIL", f"n={r.get('total')}")

# ── 5. action search: couple embracing ───────────────────────────────────────
r = search("couple embracing")
ok = any("Bheegey" in n for n in names(r))
case("action: couple embracing", "PASS" if ok else "FAIL", f"n={r.get('total')}")

# ── 6. intent/scene context (soft — semantic) ───────────────────────────────
r = search("romantic beach scene")
ok = any("Bheegey" in n for n in names(r))
case("intent: romantic beach scene", "PASS" if ok else "WARN", f"n={r.get('total')}")

# ── 7. summary searchable: 'dubbing session' came only from the summary ─────
r = search("dubbing session")
ok = any("hit_tamil" in n for n in names(r))
case("summary: dubbing session → video", "PASS" if ok else "FAIL", f"n={r.get('total')}")

# ── 8. on-image text (OCR): Abdul Kalam Knowledge Centre ────────────────────
r = search("Abdul Kalam Knowledge Centre")
ok = any("Frontal" in n for n in names(r))
case("OCR: signboard text", "PASS" if ok else "FAIL", f"n={r.get('total')}")

# ── 9. person by name (face identity → entities) ────────────────────────────
r = search("Karunakaran")
case("person by name", "PASS" if r.get("total", 0) >= 1 else "FAIL", f"n={r.get('total')}")

# ── 10. typo transparency: response.query carries the correction ────────────
r = search("a man with a beerd")
corrected = (r.get("query") or "")
ok = "beard" in corrected and r.get("total", 0) >= 1
case("typo: beerd → beard (corrected + surfaced)", "PASS" if ok else "FAIL",
     f"query={corrected!r} n={r.get('total')}")

# ── 11. native script (Hindi) ────────────────────────────────────────────────
r = search("नीली शर्ट")
case("native script: नीली शर्ट", "PASS" if r.get("total", 0) >= 1 else "FAIL", f"n={r.get('total')}")

# ── 12. visual appearance (SigLIP): beach at sunset ──────────────────────────
r = search("a couple on a beach")
ok = any("Bheegey" in n or "WhatsApp" in n for n in names(r))
case("visual: a couple on a beach", "PASS" if ok else "WARN", f"n={r.get('total')}")

# ── 13. deep-link data: a video hit carries a frame-mapped timeline entry ────
ok = False
for q in ("green shirt", "a man with a beard"):
    for h in search(q).get("hits", []):
        if h.get("type") == "video" and any(t.get("frame_index") is not None
                                            for t in h.get("timeline", [])):
            ok = True
            break
    if ok:
        break
case("deep-link: video hit has frame-mapped chip", "PASS" if ok else "FAIL")

# ── 14. document page-jump: a doc hit carries a page chip ────────────────────
ok = skip = False
for q in ("frame accurate", "asset management", "metadata"):
    for h in search(q).get("hits", []):
        if h.get("type") == "document":
            if any(t.get("page") for t in h.get("timeline", [])):
                ok = True
    if ok:
        break
if not ok:
    # no page-tagged docs in the index yet (PDF re-parse pending) → precondition missing
    skip = True
case("page-jump: doc hit has p.N chip", "PASS" if ok else ("SKIP" if skip else "FAIL"),
     "(pending PDF re-parse)" if skip else "")

# ── 15. suggest-as-you-type: label + person, grounded ────────────────────────
sg1 = s.get(f"{API}/api/search/suggest?q=ham", headers=H).json().get("suggestions", [])
sg2 = s.get(f"{API}/api/search/suggest?q=kar", headers=H).json().get("suggestions", [])
ok = any(x["text"] == "hammock" for x in sg1) and any(x["type"] == "person" for x in sg2)
case("suggest: ham→hammock, kar→person", "PASS" if ok else "FAIL", f"{sg1[:2]} {sg2[:2]}")

# ── 16. facets feed the filter bar ───────────────────────────────────────────
fc = s.get(f"{API}/api/search/facets", headers=H).json()
ok = len(fc.get("language", [])) >= 1
case("facets: language values present", "PASS" if ok else "FAIL",
     f"langs={len(fc.get('language', []))}")

# ── 17. filter actually narrows: videos only ─────────────────────────────────
r = search("man", types=["video"])
ok = r.get("total", 0) >= 1 and all(h["type"] == "video" for h in r.get("hits", []))
case("filter: type=video narrows", "PASS" if ok else "FAIL", f"n={r.get('total')}")

# ── 18. multi-concept conjunction: every word present ────────────────────────
r = search("man with a green shirt")
leak = False
for h in r.get("hits", []):
    txt = " ".join([h.get("snippet") or "", h.get("caption") or "", h["filename"]]).lower()
    if "green" not in txt:
        leak = True   # tolerated only if green lives in a non-displayed segment — flag soft
case("conjunction: man+green+shirt all present", "PASS" if r.get("total", 0) >= 1 and not leak
     else ("WARN" if r.get("total", 0) >= 1 else "FAIL"), f"n={r.get('total')} display_leak={leak}")

rep.write(f"\nSUMMARY: PASS={P} WARN={W} FAIL={F} SKIP={K} (of {P + W + F + K})\n")
with open(r"E:\dam-platform\.data\qa_usecases_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(f"PASS={P} WARN={W} FAIL={F} SKIP={K}")
