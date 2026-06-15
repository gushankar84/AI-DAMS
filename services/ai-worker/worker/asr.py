"""Speech-to-text (TSA §4.5) via faster-whisper (CTranslate2).

Word-level timestamps are produced so the API can snap each word to the frame
grid for Smart Timeline Search. Diarization (pyannote) is optional and only runs
if installed and a HF token is configured — it's a BRD 'Should', not a 'Must'.
"""
from __future__ import annotations

import logging
import threading

from .config import settings

log = logging.getLogger("dam.asr")
_lock = threading.Lock()
_model = None


def _have_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _prep_cuda_dlls() -> None:
    """Make torch's bundled cuDNN/cuBLAS/cuFFT DLLs discoverable by CTranslate2
    (faster-whisper's backend) on Windows."""
    try:
        import os as _os

        import torch
        lib = _os.path.join(_os.path.dirname(_os.path.abspath(torch.__file__)), "lib")
        if _os.path.isdir(lib):
            _os.add_dll_directory(lib)
    except Exception as e:  # pragma: no cover
        log.warning("could not add torch lib dir to DLL path: %s", e)


def _load():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from faster_whisper import WhisperModel
                if _have_cuda() and not settings.asr_force_cpu:
                    try:
                        _prep_cuda_dlls()
                        # VRAM guard: only take the GPU if there is genuinely room (the user
                        # runs their own GPU jobs on this box). Whisper int8 needs ~2GB weights
                        # + ~1GB activations; below the floor we CPU-fallback for this call —
                        # release() after each use means the next call re-checks (adaptive).
                        import torch
                        free_gb = torch.cuda.mem_get_info()[0] / 1e9
                        if free_gb < settings.asr_min_free_vram_gb:
                            log.warning("only %.1fGB VRAM free (< %.1f floor) — CPU ASR for this call",
                                        free_gb, settings.asr_min_free_vram_gb)
                        else:
                            log.info("loading Whisper %s on cuda/%s (%.1fGB free)",
                                     settings.asr_model, settings.asr_compute_type, free_gb)
                            _model = WhisperModel(settings.asr_model, device="cuda",
                                                  compute_type=settings.asr_compute_type)
                            return _model
                    except Exception as e:
                        # On Windows this is usually a commit/paging-file limit when
                        # several CUDA processes are up. Fall back to a SMALL CPU model
                        # (large-v3 on CPU exhausts MKL memory).
                        log.warning("Whisper CUDA load failed (%s); CPU fallback (small)", e)
                cpu_model = "small" if settings.asr_model.startswith("large") else settings.asr_model
                log.info("loading Whisper %s on cpu/int8", cpu_model)
                _model = WhisperModel(cpu_model, device="cpu", compute_type="int8")
    return _model


def release() -> None:
    """Free the Whisper model's GPU memory. On Windows WDDM, GPU allocations are
    backed by system commit, so releasing CTranslate2's ~5 GB before running the
    embedding model avoids out-of-commit errors when VRAM is otherwise free."""
    global _model
    with _lock:
        _model = None
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def transcribe(path: str, vad_filter: bool | None = None) -> tuple[list[dict], str | None]:
    """Return (segments, language). Each segment: {start, end, text, words:[{start,end,word}]}.

    vad_filter: None -> use settings.asr_vad_filter. The Silero threshold is lowered
    (settings.asr_vad_threshold) so SUNG vocals over music aren't discarded as
    non-speech. condition_on_previous_text=False curbs repeat-loop hallucinations on
    songs with repeated lyrics.
    """
    model = _load()
    use_vad = settings.asr_vad_filter if vad_filter is None else vad_filter
    kwargs: dict = {"word_timestamps": True, "vad_filter": use_vad,
                    "condition_on_previous_text": False}
    if use_vad:
        kwargs["vad_parameters"] = {"threshold": settings.asr_vad_threshold}
    segments, info = model.transcribe(path, **kwargs)
    out: list[dict] = []
    for seg in segments:
        words = [{"start": w.start, "end": w.end, "word": w.word}
                 for w in (seg.words or []) if w.start is not None]
        out.append({"start": seg.start, "end": seg.end, "text": seg.text.strip(), "words": words})
    return out, getattr(info, "language", None)
