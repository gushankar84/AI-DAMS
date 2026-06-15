r"""Settle whether the Hindi text is corrupt or just a console display issue.
Writes an ASCII-safe report (code points) so the verdict is readable anywhere.
"""
import io
import json
import unicodedata

import httpx

API = "http://127.0.0.1:8000"
OUT = r"E:\dam-platform\.data\encoding_report.txt"


def classify(s: str) -> str:
    dev = sum(1 for c in s if "ऀ" <= c <= "ॿ")          # Devanagari
    moji = sum(1 for c in s if c in "à¤¥¾¨")  # tell-tale mojibake bytes
    if dev > 5:
        return "VALID Devanagari (correct UTF-8)"
    if moji > 5:
        return "MOJIBAKE (UTF-8 bytes shown as Latin-1) -> data corrupt"
    return "unclear"


def main():
    c = httpx.Client(base_url=API, timeout=60)
    tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
                 headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    rep = io.StringIO()
    # find the Hindi audio asset
    audio = [a for a in c.get("/api/assets?type=audio&limit=20", headers=h).json()]
    target = next((a for a in audio if "Kanagana" in a["filename"] or "ElevenLabs" in a["filename"]), audio[0] if audio else None)
    d = c.get(f"/api/assets/{target['id']}", headers=h).json()
    seg = d["transcript"][0]["text"] if d["transcript"] else ""

    rep.write("=== STORED TRANSCRIPT (from API JSON) ===\n")
    rep.write(f"length: {len(seg)} chars\n")
    rep.write(f"classification: {classify(seg)}\n")
    rep.write("first 12 code points:\n")
    for ch in seg[:12]:
        rep.write(f"  U+{ord(ch):04X}  {unicodedata.name(ch, '?')}\n")
    rep.write(f"\nraw bytes (utf-8) first 24: {seg[:8].encode('utf-8').hex(' ')}\n")

    # Does search work on the actual Devanagari script?
    if seg:
        # take a Devanagari word from the transcript
        words = [w for w in seg.split() if any("ऀ" <= ch <= "ॿ" for ch in w)]
        probe = " ".join(words[:3]) if words else seg[:10]
        r = c.post("/api/search", headers=h, json={"q": probe, "limit": 3}).json()
        rep.write(f"\n=== SEARCH with Devanagari query (first 3 words) ===\n")
        rep.write(f"query bytes (utf-8): {probe.encode('utf-8').hex(' ')[:60]}...\n")
        rep.write(f"results: {r['total']}\n")
        for hit in r["hits"]:
            rep.write(f"  [{hit['type']}] {hit['filename']}  signals={hit['matched_signals']}\n")
        found = any(hit["asset_id"] == target["id"] for hit in r["hits"])
        rep.write(f"-> found the audio by its own Hindi text: {found}\n")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(rep.getvalue())
    print("report written")


if __name__ == "__main__":
    main()
