r"""Scenario harness — search INPUT VARIATIONS a real user would type.
Each row: (group, query, types, expect_substr|None, must_have_results).
PASS = expectation met; FAIL = clear miss; WARN = exploratory/soft.
"""
import io

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=60)
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

S = [
    # ── typos / misspellings (fuzzy BM25 + semantic should still find it) ──
    ("typo", "blu shirt", ["image"], None, True),
    ("typo", "a man with a beerd", ["image"], "Raghupathy", True),
    ("typo", "green sari", None, None, True),
    ("typo", "eyeglases", ["image"], None, True),
    # ── question form ──
    ("question", "who is wearing a blue shirt?", ["image"], None, True),
    ("question", "is there a man with a beard?", ["image"], "Raghupathy", True),
    ("question", "what document explains frame accurate timecode?", ["document"], "BRD", True),
    # ── native-script, cross-lingual (Hindi query → English captions) ──
    ("native", "नीली शर्ट", ["image"], None, True),          # "blue shirt" in Hindi
    ("native", "दाढ़ी वाला आदमी", ["image"], None, None),     # "bearded man" in Hindi
    # ── compound / multi-attribute ──
    ("compound", "an older man wearing glasses", ["image"], None, True),
    ("compound", "a woman in a green outfit", ["image"], "Stella", True),
    ("compound", "people standing outdoors at sunset", ["image"], "2026-06-08", True),
    ("compound", "two women on a stage dancing", ["video"], "Pacarku", True),
    # ── synonyms / paraphrase ──
    ("synonym", "spectacles", ["image"], None, None),
    ("synonym", "a vehicle on the road", ["video"], "hit_tamil", None),
    ("synonym", "a person reading or talking about books", ["audio"], "rashmika", True),
    # ── casing / whitespace ──
    ("casing", "BLUE SHIRT", ["image"], None, True),
    ("casing", "   green    saree   ", None, None, True),
]

rep = io.StringIO()
p = w = f = 0
for grp, q, types, expect, must in S:
    body = {"q": q, "limit": 6}
    if types:
        body["types"] = types
    try:
        r = s.post(f"{API}/api/search", headers=H, json=body).json()
        hits = r["hits"]
        names = [h["filename"] for h in hits]
        verdict = "PASS"; note = ""
        if expect is not None and not any(expect in n for n in names):
            verdict, note = "FAIL", f"expected '{expect}' missing"
        elif must is True and not hits:
            verdict, note = "FAIL", "expected results, got 0"
        elif must is None and expect is None:
            verdict = "PASS" if hits else "WARN"  # exploratory: just shouldn't error
        p += verdict == "PASS"; w += verdict == "WARN"; f += verdict == "FAIL"
        top = ", ".join(n[:20] for n in names[:3]) or "(none)"
        qd = q.strip()[:30]
        rep.write(f"[{verdict}] ({grp}) '{qd}' -> {r['total']}: {top}  {note}\n")
    except Exception as e:
        f += 1
        rep.write(f"[ERROR] ({grp}) '{q[:30]}' -> {type(e).__name__}: {str(e)[:90]}\n")

rep.write(f"\nSUMMARY: PASS={p} WARN={w} FAIL={f} (of {len(S)})\n")
with open(r"E:\dam-platform\.data\sc_variations_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(f"PASS={p} WARN={w} FAIL={f}")
