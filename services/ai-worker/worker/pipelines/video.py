"""Video pipeline (TSA §4.4).

    Probe (fps/timecode) -> shot detect (PySceneDetect) -> per-shot keyframe
        -> enrich (faces/objects/scene) frame-mapped to the VIDEO frame grid
    + ASR (server) aligned to the same frame grid
    -> markers + transcript + dense vectors -> searchable

Every detection is stored as a frame index + SMPTE on the video's own frame grid,
so a result at frame N seeks to frame N in the player (frame-accurate contract §5).
"""
from __future__ import annotations

import base64
import logging
import math
import os
import shutil
import subprocess
import tempfile
import uuid

from .. import serving_client, stores
from ..config import QDRANT_FACE, QDRANT_IMAGE, QDRANT_TEXT, settings
from ..timecode import probe_frame_map
from .common import window_segments
from .enrich import InJobFaces, enrich
from .media_common import probe_and_record_streams

log = logging.getLogger("dam.pipeline.video")
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
# A 2nd keyframe per long shot catches mid-shot content but ~doubles VLM calls. Off by default
# because the VLM is slow on this box (~30-45s/call); flip on where ingest time isn't a concern.
SPLIT_LONG_SHOTS = False


def _detect_shots(path: str, fps: float) -> list[tuple[int, int]]:
    """Return [(start_frame, end_frame)] shot boundaries via PySceneDetect."""
    try:
        from scenedetect import ContentDetector, detect
        scenes = detect(path, ContentDetector())
        if scenes:
            return [(s.get_frames(), e.get_frames()) for s, e in scenes]
    except Exception as e:
        log.warning("shot detection failed (%s); treating whole video as one shot", e)
    return []


def _extract_keyframe(path: str, seconds: float, dst: str) -> bool:
    try:
        subprocess.run([FFMPEG, "-y", "-ss", f"{seconds:.3f}", "-i", path,
                        "-frames:v", "1", "-q:v", "3", dst],
                       capture_output=True, check=True)
        return os.path.exists(dst)
    except Exception as e:
        log.warning("keyframe extract failed at %.2fs: %s", seconds, e)
        return False


async def process(asset: dict) -> None:
    asset_id = asset["id"]
    await stores.set_status(asset_id, "processing")

    # 1. Frame-accurate stream record.
    stream_id = None
    try:
        streams = await probe_and_record_streams(asset)
        if "video" in streams:
            stream_id = streams["video"]["stream_id"]
    except Exception as e:
        log.warning("video probe failed for %s: %s", asset_id, e)

    await stores.set_status(asset_id, "extracting")
    all_markers: list[dict] = []
    face_points: list[dict] = []
    labels: set[str] = set()
    captions: list[tuple[int, str, str]] = []  # (frame, smpte, caption)
    kf_uris: list[str] = []                     # keyframe URIs, for a representative thumbnail
    img_points: list[dict] = []                 # per-keyframe SigLIP vectors (visual search)
    face_cache = InJobFaces()                   # cluster a recurring face across keyframes into ONE person

    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, asset["filename"])
        stores.download_to(asset["storage_uri"], local)
        fmaps = probe_frame_map(local)
        vfm = fmaps.get("video")
        fps = vfm.fps if vfm else 25.0
        fps_num = vfm.fps_num if vfm else 25
        fps_den = vfm.fps_den if vfm else 1

        # 2. Shots
        shots = _detect_shots(local, fps)
        if not shots:
            total = vfm.duration_frames if (vfm and vfm.duration_frames) else int(fps * 10)
            shots = [(0, total)]
        if len(shots) > settings.video_max_shots:
            # uniformly sample shots to cap compute, logging what we drop
            step = len(shots) / settings.video_max_shots
            sampled = [shots[int(i * step)] for i in range(settings.video_max_shots)]
            log.info("video %s: %d shots -> sampling %d (cap)", asset_id, len(shots), len(sampled))
            shots = sampled

        # Pick which shots get the expensive VLM caption: ~1 per `video_caption_sec_per` seconds,
        # clamped to [min, max] — so a 5-min video is ~25-30 captions (~25-30 min) not one-per-shot
        # (1-2 h), while short clips are captioned in full. Faces/objects still run on EVERY shot;
        # only the ~30-45s/shot VLM describe is sampled. Tags accumulate for the summary + doc.
        dur_sec = (vfm.duration_frames / fps) if (vfm and vfm.duration_frames and fps) else (len(shots) * 4)
        n_cap = min(len(shots), settings.video_max_captions,
                    max(settings.video_min_captions, math.ceil(dur_sec / settings.video_caption_sec_per)))
        cap_idx = {int(i * len(shots) / n_cap) for i in range(n_cap)} if n_cap else set()
        log.info("video %s: %d shots, captioning %d (~1/%ds)", asset_id, len(shots), len(cap_idx),
                 settings.video_caption_sec_per)
        LONG_SHOT_SEC = 5.0
        shot_objects: set[str] = set()
        shot_actions: set[str] = set()
        shot_intents: list[str] = []

        # 3. Per-shot keyframe enrichment, frame-mapped to the video grid
        for i, (start_f, end_f) in enumerate(shots):
            mid_frame = (start_f + end_f) // 2
            mid_sec = mid_frame / fps if fps else 0
            kf = os.path.join(tmp, f"kf_{start_f}.jpg")
            if not _extract_keyframe(local, mid_sec, kf):
                continue
            kf_key = f"video/{asset_id}/keyframes/{start_f}.jpg"
            kf_uri = stores.upload_file(kf, kf_key, "image/jpeg")
            kf_uris.append(kf_uri)
            smpte = vfm.frame_to_smpte(start_f) if vfm else None
            # Keyframe -> SigLIP image vector, so the video is findable by APPEARANCE
            # (visual search), not only by caption/transcript text — the gap that made a
            # silent green-saree clip invisible to "green top". Frame-mapped so a visual
            # hit seeks to (and can show) the exact shot. b64 from the local frame avoids
            # a storage round-trip.
            try:
                with open(kf, "rb") as _fh:
                    _b64 = base64.b64encode(_fh.read()).decode()
                ivec = serving_client.embed_image_b64(_b64)
                img_points.append({
                    "id": str(uuid.uuid4()), "vector": ivec,
                    "payload": {"asset_id": asset_id, "asset_type": "video", "region": "frame",
                                "frame_index": start_f, "smpte": smpte, "frame_uri": kf_uri,
                                "department": asset.get("department"), "project": asset.get("project")},
                })
            except Exception as ex:
                log.warning("keyframe embed failed at frame %s: %s", start_f, ex)
            # shot marker
            all_markers.append({
                "id": str(uuid.uuid4()), "asset_id": asset_id, "stream_id": stream_id,
                "kind": "shot", "frame_index": start_f, "end_frame": end_f,
                "smpte": smpte, "fps_num": fps_num, "fps_den": fps_den, "payload": {},
            })
            e = await enrich(asset_id, kf_uri, f"{start_f}.jpg", stream_id=stream_id,
                             frame_index=start_f, end_frame=end_f, smpte=smpte,
                             fps_num=fps_num, fps_den=fps_den, do_caption=(i in cap_idx),
                             face_cache=face_cache, vlm_max_side=settings.vlm_video_max_side)
            all_markers.extend(e["markers"])
            face_points.extend(e["face_points"])
            labels.update(e["labels"])
            shot_objects.update(e.get("objects") or [])
            shot_actions.update(e.get("actions") or [])
            if e.get("intent"):
                shot_intents.append(e["intent"])
            if e["caption"]:
                captions.append((start_f, smpte, e["caption"]))

            # Long shot → sample a 2nd frame ~3/4 through and MERGE its objects/actions/caption
            # (group-of-frames tagging), so content appearing mid-shot isn't missed. Caption-only
            # (no repeat face/object detection) to keep the extra cost to one VLM call.
            if SPLIT_LONG_SHOTS and fps and (end_f - start_f) / fps > LONG_SHOT_SEC:
                kfb = os.path.join(tmp, f"kf_{start_f}_b.jpg")
                if _extract_keyframe(local, (start_f + 3 * (end_f - start_f) // 4) / fps, kfb):
                    kfb_uri = stores.upload_file(kfb, f"video/{asset_id}/keyframes/{start_f}_b.jpg", "image/jpeg")
                    try:
                        d2 = serving_client.describe(kfb_uri, f"{start_f}_b.jpg",
                                                     max_side=settings.vlm_video_max_side)
                        shot_objects.update(d2.get("objects") or [])
                        shot_actions.update(d2.get("actions") or [])
                        labels.update((d2.get("objects") or []) + (d2.get("actions") or []))
                        if d2.get("caption"):
                            captions.append((start_f, smpte, d2["caption"]))
                    except Exception as ex:
                        log.warning("long-shot 2nd-frame describe failed at %s: %s", start_f, ex)

    # Representative thumbnail = a middle keyframe (avoids black/title opening frames),
    # so video assets show a real frame in search/explorer instead of a generic icon.
    if kf_uris:
        pool = await stores.pg()
        await pool.execute("UPDATE asset SET thumbnail_uri=$2 WHERE id=$1",
                           asset_id, kf_uris[len(kf_uris) // 2])

    if all_markers:
        await stores.insert_markers(all_markers)
    if face_points:
        stores.upsert_vectors(QDRANT_FACE, face_points)
    if img_points:
        stores.upsert_vectors(QDRANT_IMAGE, img_points)  # video now visually searchable

    # 4. Speech: ASR aligned to the VIDEO frame grid.
    transcript_rows, os_segments, seg_texts, seg_frames = [], [], [], []
    try:
        segments, language = serving_client.transcribe(asset["storage_uri"], asset["filename"])
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            sf = int(round(seg["start"] * fps))
            ef = int(round(seg["end"] * fps))
            smpte = vfm.frame_to_smpte(sf) if vfm else None
            transcript_rows.append({
                "asset_id": asset_id, "stream_id": stream_id, "start_frame": sf, "end_frame": ef,
                "start_pts_num": int(round(seg["start"] * 1000)), "start_pts_den": 1000,
                "speaker": None, "language": language, "text": text,
            })
            os_segments.append({"asset_id": asset_id, "asset_type": "video", "text": text,
                                "speaker": None, "start_frame": sf, "end_frame": ef, "smpte": smpte})
            seg_texts.append(text)
            seg_frames.append((sf, smpte))
    except Exception as e:
        language = None
        log.warning("video ASR skipped for %s: %s", asset_id, e)

    if transcript_rows:
        await stores.insert_transcripts(transcript_rows)
        stores.index_transcript_segments(os_segments)

    # 5. Dense semantic: transcript WINDOWS + scene captions (best-effort). Transcript
    #    segments are merged into coherent windows before embedding (see window_segments) so
    #    short ASR fillers don't become standalone black-hole vectors; scene captions are
    #    already specific full descriptions and embed one-per-shot (keeps the shot seek anchor).
    try:
        trans_windows = window_segments(seg_texts, seg_frames)   # [(text, (sf, smpte)), ...]
        dense_items = [(t, anc[0], anc[1], "transcript") for (t, anc) in trans_windows] + \
                      [(c[2], c[0], c[1], "scene") for c in captions]
        if dense_items:
            vecs = serving_client.embed_texts([d[0] for d in dense_items])
            pts = [{
                "id": str(uuid.uuid4()), "vector": v,
                "payload": {"asset_id": asset_id, "asset_type": "video", "kind": dense_items[i][3],
                            "snippet": dense_items[i][0][:300],
                            "start_frame": dense_items[i][1], "smpte": dense_items[i][2]},
            } for i, v in enumerate(vecs)]
            stores.upsert_vectors(QDRANT_TEXT, pts)
    except Exception as e:
        log.warning("video dense embedding skipped for %s: %s", asset_id, e)

    # 6. Asset-level SUMMARY — one sentence rolling up the shot tags + speech ("what is this
    #    video about"). Grounded only in the gathered notes; best-effort.
    summary = ""
    try:
        notes = []
        if shot_objects:
            notes.append("Objects seen: " + ", ".join(sorted(shot_objects)))
        if shot_actions:
            notes.append("Actions: " + ", ".join(sorted(shot_actions)))
        if shot_intents:
            notes.append("Scenes: " + "; ".join(shot_intents[:8]))
        if seg_texts:
            notes.append("Speech: " + (" ".join(seg_texts))[:1500])
        notes += [c[2] for c in captions[:6]]
        if notes:
            summary = serving_client.summarize("\n".join(notes), "video")
    except Exception as e:
        log.warning("video summary skipped for %s: %s", asset_id, e)

    # 7. Asset-level keyword doc — now includes the object/action tags + the summary, so a
    #    "drum" or "dancing" hits the doc and the summary answers "what is this about".
    body = " ".join(seg_texts + [c[2] for c in captions] + sorted(labels)
                    + sorted(shot_objects) + sorted(shot_actions) + ([summary] if summary else []))
    all_labels = sorted(set(labels) | shot_objects | shot_actions)
    stores.index_asset_doc({
        "asset_id": asset_id, "asset_type": "video",
        "title": asset.get("title") or asset["filename"],
        "description": summary or asset.get("description"), "body": body[:200_000],
        # Source-separated: lets a keyword hit say whether the term was SEEN or SAID.
        "visual_text": " ".join([c[2] for c in captions] + sorted(all_labels) + ([summary] if summary else []))[:200_000],
        "spoken_text": " ".join(seg_texts)[:200_000],
        "summary": summary,
        "tags": asset.get("tags") or [], "labels": all_labels,
        "department": asset.get("department"), "project": asset.get("project"),
        "language": language,
        "created_at": asset["created_at"].isoformat() if asset.get("created_at") else None,
    })

    if summary:
        await stores.set_description(asset_id, summary)   # surface in the asset detail UI
    await stores.set_asset_text(asset_id, asset.get("title"), language)
    await stores.set_status(asset_id, "searchable")
    log.info("video indexed: %s (%d shots, %d markers, %d segs, %d labels, %d objects, %d actions, summary=%s)",
             asset_id, len(shots), len(all_markers), len(seg_texts), len(all_labels),
             len(shot_objects), len(shot_actions), bool(summary))
