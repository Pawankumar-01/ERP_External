"""
Transcription Service
──────────────────────
Uses faster-whisper (CTranslate2) for local, optimized STT.

CRITICAL: faster-whisper inference is CPU/GPU-bound and synchronous.
It MUST be called via run_in_threadpool to avoid blocking the async event loop.

Model is loaded once at module level (singleton) to avoid repeated
disk reads. First call may take a few seconds to load the model.
"""

import io
import logging
import tempfile
import os
from typing import Optional

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# ── Model singleton ────────────────────────────────────────────────────────────
# Loaded lazily on first transcription request.
# Keeps the model in memory for subsequent calls (fast inference).
_whisper_model = None


def _get_model():
    """Load faster-whisper model once and cache it."""
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            model_size = os.getenv("WHISPER_MODEL_SIZE", "base")
            device     = os.getenv("WHISPER_DEVICE", "cpu")
            compute    = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
            logger.info(f"Loading faster-whisper model: {model_size} on {device} ({compute})")
            _whisper_model = WhisperModel(model_size, device=device, compute_type=compute)
            logger.info("faster-whisper model loaded successfully")
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Run: pip install faster-whisper"
            )
    return _whisper_model


def _transcribe_sync(audio_bytes: bytes, language: Optional[str] = None) -> str:
    """
    Synchronous transcription — runs in threadpool.
    Writes audio bytes to a temp file (faster-whisper requires a file path).
    Returns the full transcript as a single string.
    """
    model = _get_model()

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments, info = model.transcribe(
            tmp_path,
            language=language,
            beam_size=5,
            vad_filter=True,           # skip silence automatically
            vad_parameters={"min_silence_duration_ms": 500},
        )
        transcript = " ".join(seg.text.strip() for seg in segments)
        logger.info(
            f"Transcribed {info.duration:.1f}s of audio "
            f"(lang={info.language}, prob={info.language_probability:.2f})"
        )
        return transcript.strip()
    finally:
        os.unlink(tmp_path)


async def transcribe_audio(
    audio_bytes: bytes,
    language: Optional[str] = None
) -> str:
    """
    Async wrapper — dispatches blocking transcription to threadpool.
    Safe to call from FastAPI route handlers and background tasks.
    """
    if not audio_bytes:
        raise ValueError("Empty audio bytes received")

    transcript = await run_in_threadpool(_transcribe_sync, audio_bytes, language)

    if not transcript:
        raise ValueError("Transcription returned empty result")

    return transcript
