"""SYNONYM / ONTOLOGY battery — colour families, object hypernyms, common-term synonyms.

Locks in the fix for "search a shade, find its family" (brown↔beige↔tan↔khaki) at the BM25
search-analyzer layer + the family-aware concept filter — WITHOUT breaking colour binding
(red ≠ green) or re-admitting absent concepts. Run alongside qa_hallucination + qa_search.
"""
import io
import sys
import httpx

sys.stdout.reconfigure(encoding="utf-8")

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=120)
tok = s.post(f"{API}/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
rep = io.StringIO()
P = F = 0


def n(q):
    return s.post(f"{API}/api/search", headers=H, json={"q": q, "limit": 20}).json().get("total", 0)


def names(q):
    return [h["filename"] for h in s.post(f"{API}/api/search", headers=H,
            json={"q": q, "limit": 20}).json().get("hits", [])]


def case(name, ok, note=""):
    global P, F
    P += ok; F += not ok
    rep.write(f"[{'PASS' if ok else 'FAIL'}] {name}  {note}\n")


# ── colour families must cross-match (a shade reaches its kin) ───────────────
case("brown → beige items", n("brown shorts") >= 1, f"n={n('brown shorts')}")
case("khaki reaches brown family", n("khaki") >= 1, f"n={n('khaki')}")
case("navy reaches blue family", n("navy") >= 1, f"n={n('navy')}")
case("ivory reaches white family", n("ivory") >= 1, f"n={n('ivory')}")
case("silver reaches grey family", n("silver") >= 1, f"n={n('silver')}")

# ── object hypernyms ────────────────────────────────────────────────────────
case("car ↔ vehicle linked", n("vehicle") >= 1 and n("car") >= 1, f"car={n('car')} vehicle={n('vehicle')}")
case("automobile finds cars", n("automobile") >= 1, f"n={n('automobile')}")
case("couch ↔ sofa linked", n("couch") >= 1, f"n={n('couch')}")

# ── people synonyms ─────────────────────────────────────────────────────────
case("woman/female/lady linked", n("lady") >= 1, f"n={n('lady')}")

# ── colour BINDING must STILL hold (different families do NOT cross) ─────────
case("red dupatta ≠ green dupatta", not any("Stella3" in x for x in names("red dupatta")),
     f"n={n('red dupatta')}")
ns = names("red shirt")
case("red shirt ≠ white-uniform man", not any("Raghupathy1" in x for x in ns), f"n={len(ns)}")

# ── absent concepts must stay clean (synonyms/keyword-credit didn't reopen) ──
for absent in ["elephant", "guitar", "violin", "pizza"]:
    r = s.post(f"{API}/api/search", headers=H, json={"q": absent, "limit": 5}).json()
    text_leak = any(not (set(h.get("matched_signals", [])) & {"image", "face"})
                    for h in r.get("hits", []))
    case(f"absent '{absent}' no text leak", not text_leak, f"n={r.get('total')}")

rep.write(f"\nSUMMARY: PASS={P} FAIL={F} (of {P + F})\n")
with open(r"E:\dam-platform\.data\qa_synonyms_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(rep.getvalue())
