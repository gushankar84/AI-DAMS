"""Audio pipeline (TSA §4.5).

    Probe (FFmpeg) -> ASR (server) -> snap words to frame grid
                   -> Postgres transcript + OpenSearch timed segments (Smart Timeline)
                   + dense transcript vectors (semantic) -> searchable

ASR + embedding run in the serving process; this module orchestrates + indexes.
For audio-only assets the frame grid is the sample clock (sample-accurate).
"""
from __future__ import annotations

import logging
import uuid

from .. import serving_client, stores
from ..config import QDRANT_TEXT
from ..timecode import seconds_to_clock
from .common import window_segments
from .media_common import probe_and_record_streams

log = logging.getLogger("dam.pipeline.audio")


async def process(asset: dict) -> None:
    asset_id = asset["id"]
    await stores.set_status(asset_id, "processing")

    # 1. Frame-accurate stream record (audio: fps = sample rate).
    stream_id, sample_rate = None, 16000
    try:
        streams = await probe_and_record_streams(asset)
        if "audio" in streams:
            stream_id = streams["audio"]["stream_id"]
            sample_rate = streams["audio"]["frame_map"].fps_num or 16000
    except Exception as e:
        log.warning("audio probe failed for %s: %s", asset_id, e)

    # 2. Transcribe (server downloads + runs Whisper + releases its VRAM).
    await stores.set_status(asset_id, "extracting")
    segments, language = serving_client.transcribe(asset["storage_uri"], asset["filename"])

    # 3. Frame-mapped records.
    transcript_rows, os_segments, seg_texts, seg_frames = [], [], [], []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start_f = int(round(seg["start"] * sample_rate))
        end_f = int(round(seg["end"] * sample_rate))
        clock = seconds_to_clock(seg["start"])
        transcript_rows.append({
            "asset_id": asset_id, "stream_id": stream_id,
            "start_frame": start_f, "end_frame": end_f,
            "start_pts_num": int(round(seg["start"] * 1000)), "start_pts_den": 1000,
            "speaker": None, "language": language, "text": text,
        })
        os_segments.append({
            "asset_id": asset_id, "asset_type": "audio", "text": text,
            "speaker": None, "start_frame": start_f, "end_frame": end_f, "smpte": clock,
        })
        seg_texts.append(text)
        seg_frames.append((start_f, clock))

    if transcript_rows:
        await stores.insert_transcripts(transcript_rows)
        stores.index_transcript_segments(os_segments)

    # 4. Dense semantic over transcript WINDOWS (best-effort). Segments are merged into
    #    coherent windows before embedding so short ASR fillers don't become standalone
    #    black-hole vectors (see window_segments). Timeline seeking stays segment-accurate
    #    (it uses the transcript rows above, not these vectors).
    if seg_texts:
        try:
            windows = window_segments(seg_texts, seg_frames)
            wtexts = [w[0] for w in windows]
            vectors = serving_client.embed_texts(wtexts)
            points = [{
                "id": str(uuid.uuid4()), "vector": vec,
                "payload": {"asset_id": asset_id, "asset_type": "audio", "kind": "transcript",
                            "department": asset.get("department"), "project": asset.get("project"),
                            "snippet": wtexts[i][:300],
                            "start_frame": windows[i][1][0], "smpte": windows[i][1][1]},
            } for i, vec in enumerate(vectors)]
            stores.upsert_vectors(QDRANT_TEXT, points)
        except Exception as e:
            log.warning("transcript embedding skipped for %s (%s)", asset_id, e)

    # 5. Asset-level keyword doc (full transcript body).
    full_text = " ".join(seg_texts)
    stores.index_asset_doc({
        "asset_id": asset_id, "asset_type": "audio",
        "title": asset.get("title") or asset["filename"],
        "description": asset.get("description"), "body": full_text[:200_000],
        "spoken_text": full_text[:200_000],   # audio: everything is SAID
        "tags": asset.get("tags") or [], "department": asset.get("department"),
        "project": asset.get("project"), "language": language,
        "created_at": asset["created_at"].isoformat() if asset.get("created_at") else None,
    })

    await stores.set_asset_text(asset_id, asset.get("title"), language)
    await stores.set_status(asset_id, "searchable")
    log.info("audio indexed: %s (%d segments, lang=%s)", asset_id, len(seg_texts), language)
