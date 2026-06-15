r"""Empirically find the VAD setting that recovers sung lyrics.
Transcribes one song segment three ways and writes the text for comparison.
"""
import io

from worker import asr

with open(r"E:\dam-platform\.data\song_sample_path.txt", encoding="utf-8-sig") as f:
    PATH = f.read().strip().lstrip("﻿")

model = asr._load()  # reuse robust loader (cuda w/ cpu fallback)

CONFIGS = [
    ("VAD off", dict(vad_filter=False)),
    ("VAD on, threshold=0.2 (new)", dict(vad_filter=True, vad_parameters={"threshold": 0.2})),
    ("VAD on, threshold=0.5 (old default)", dict(vad_filter=True, vad_parameters={"threshold": 0.5})),
]

rep = io.StringIO()
rep.write(f"file: {PATH}\n\n")
for name, kw in CONFIGS:
    segs, info = model.transcribe(PATH, word_timestamps=True,
                                  condition_on_previous_text=False, **kw)
    segs = list(segs)
    text = " ".join(s.text.strip() for s in segs)
    rep.write(f"=== {name} ===\n")
    rep.write(f"lang={getattr(info,'language',None)}  segments={len(segs)}  chars={len(text)}\n")
    rep.write(text[:600] + "\n\n")

with open(r"E:\dam-platform\.data\vad_report.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print("vad report written")
