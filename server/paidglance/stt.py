"""Local speech-to-text with faster-whisper. Audio never leaves the server."""
from __future__ import annotations

import os
import threading

import numpy as np

_model = None
_lock = threading.Lock()
MODEL = os.environ.get("PAIDGLANCE_WHISPER", "base.en")

# Bias decoding toward the counter vocabulary.
PROMPT = "Charge 4.50. Charge twelve dollars. Total. Served. Cancel."


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe_pcm16(pcm: bytes, rate: int = 16000) -> str:
    """PCM s16le mono 16 kHz (the G2 microphone format) -> text."""
    if len(pcm) < rate // 5:  # under 0.1 s
        return ""
    audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    segments, _ = _get_model().transcribe(audio, language="en", beam_size=1,
                                          initial_prompt=PROMPT, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def warm_up() -> None:
    threading.Thread(target=_get_model, daemon=True).start()
