"""Frame-accurate timecode (TSA §5) — the hard contract.

All temporal metadata is stored as a frame index + exact rational PTS + SMPTE
string + source fps/timebase, never as bare milliseconds. Any model that
localises an event to a wall-clock time is immediately snapped to the nearest
real frame PTS before storage.

Drop-frame timecode is handled for NTSC 29.97 / 59.94 content.
"""
from __future__ import annotations

import bisect
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction

FFPROBE = shutil.which("ffprobe") or "ffprobe"


@dataclass
class FrameMap:
    """Authoritative timing for one stream."""
    fps_num: int
    fps_den: int
    timebase: str | None = None
    duration_frames: int | None = None
    is_drop_frame: bool = False
    # For VFR sources: the real per-frame PTS list (seconds). Empty => assume CFR.
    pts_list: list[float] = field(default_factory=list)

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den if self.fps_den else 0.0

    @property
    def nominal_fps(self) -> int:
        """Integer timecode base (30 for 29.97, 25 for 25, ...)."""
        return round(self.fps) if self.fps else 0

    def seconds_to_frame(self, seconds: float) -> int:
        """Snap a wall-clock time to the nearest real frame index."""
        if self.pts_list:  # VFR: nearest actual PTS, never assume constant spacing
            i = bisect.bisect_left(self.pts_list, seconds)
            if i <= 0:
                return 0
            if i >= len(self.pts_list):
                return len(self.pts_list) - 1
            before, after = self.pts_list[i - 1], self.pts_list[i]
            return i if (after - seconds) < (seconds - before) else i - 1
        frame = round(seconds * self.fps)
        if self.duration_frames is not None:
            frame = min(frame, self.duration_frames - 1)
        return max(0, frame)

    def frame_to_pts(self, frame: int) -> Fraction:
        if self.pts_list and 0 <= frame < len(self.pts_list):
            return Fraction(self.pts_list[frame]).limit_denominator(1_000_000)
        return Fraction(frame * self.fps_den, self.fps_num) if self.fps_num else Fraction(0)

    def frame_to_smpte(self, frame: int) -> str:
        return frames_to_smpte(frame, self.nominal_fps, self.is_drop_frame)


def seconds_to_clock(seconds: float) -> str:
    """HH:MM:SS.mmm wall-clock string — used for audio (which has no video timecode)."""
    if seconds < 0:
        seconds = 0.0
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}.{ms:03d}"


def frames_to_smpte(frame: int, nominal_fps: int, drop_frame: bool) -> str:
    """Convert an absolute frame index to an SMPTE timecode string.

    Drop-frame (29.97/59.94) drops `2*(fps//30)` frame-numbers each minute except
    every tenth minute, keeping the timecode aligned to real elapsed time.
    """
    if nominal_fps <= 0:
        return "00:00:00:00"
    sep = ";" if drop_frame else ":"
    if drop_frame:
        drop = 2 * (nominal_fps // 30)
        frames_per_min = nominal_fps * 60
        frames_per_10min = frames_per_min * 10 - drop * 9
        d, m = divmod(frame, frames_per_10min)
        if m < drop:
            m += drop
        frame += drop * 9 * d + drop * ((m - drop) // (frames_per_min - drop))
    ff = frame % nominal_fps
    s = (frame // nominal_fps) % 60
    mm = (frame // (nominal_fps * 60)) % 60
    hh = frame // (nominal_fps * 3600)
    return f"{hh:02d}:{mm:02d}:{s:02d}{sep}{ff:02d}"


def probe_frame_map(path: str, want_pts: bool = False) -> dict[str, FrameMap]:
    """Probe a media file with ffprobe and return {stream_kind: FrameMap}.

    `want_pts=True` extracts the full per-frame PTS list (needed for VFR / exact
    seeking) — heavier, so off by default.
    """
    out: dict[str, FrameMap] = {}
    meta = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout)

    for st in meta.get("streams", []):
        codec_type = st.get("codec_type")
        if codec_type == "video":
            num, den = _parse_rate(st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/1")
            nb = st.get("nb_frames")
            fm = FrameMap(
                fps_num=num or 30, fps_den=den or 1, timebase=st.get("time_base"),
                duration_frames=int(nb) if nb and nb.isdigit() else None,
                is_drop_frame=_is_drop_frame(num, den),
            )
            if want_pts:
                fm.pts_list = _extract_pts(path, st.get("index", 0))
                if fm.duration_frames is None and fm.pts_list:
                    fm.duration_frames = len(fm.pts_list)
            out["video"] = fm
        elif codec_type == "audio":
            sr = int(st.get("sample_rate", 0) or 0)
            out["audio"] = FrameMap(fps_num=sr or 16000, fps_den=1, timebase=st.get("time_base"),
                                    duration_frames=None)
    return out


def _parse_rate(rate: str) -> tuple[int, int]:
    if "/" in rate:
        a, b = rate.split("/")
        return int(a), int(b or 1)
    return int(float(rate)), 1


def _is_drop_frame(num: int, den: int) -> bool:
    if not num or not den:
        return False
    fps = num / den
    return den == 1001 and round(fps) in (30, 60)


def _extract_pts(path: str, stream_index: int) -> list[float]:
    """Full per-frame PTS list (seconds) — authoritative for VFR sources."""
    res = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", f"v:{stream_index}",
         "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    pts = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if line and line not in ("N/A",):
            try:
                pts.append(float(line))
            except ValueError:
                pass
    return sorted(pts)
