"""Shared media handling for audio/video: probe + persist the frame map.

This is the P0 frame-accuracy work — every media asset gets an authoritative
stream record (fps, timebase, drop-frame) so later detections (P2/P3) can be
snapped to real frame indices.
"""
from __future__ import annotations

import logging
import os
import tempfile

from .. import stores
from ..timecode import probe_frame_map

log = logging.getLogger("dam.pipeline.media")


async def probe_and_record_streams(asset: dict, want_pts: bool = False) -> dict[str, dict]:
    """Download, ffprobe, and persist stream rows. Returns {kind: {stream_id, frame_map}}."""
    asset_id = asset["id"]
    result: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, asset["filename"])
        stores.download_to(asset["storage_uri"], local)
        fmaps = probe_frame_map(local, want_pts=want_pts)
        for kind, fm in fmaps.items():
            sid = await stores.create_stream(
                asset_id=asset_id, kind=kind,
                fps_num=fm.fps_num, fps_den=fm.fps_den,
                duration_frames=fm.duration_frames, timebase=fm.timebase,
                is_drop_frame=fm.is_drop_frame,
                sample_rate=(fm.fps_num if kind == "audio" else None),
            )
            result[kind] = {"stream_id": sid, "frame_map": fm}
    return result
