"""
Transcription Service
──────────────────────
Uses faster-whisper (CTranslate2) for local, optimized STT.

CRITICAL: faster-whisper inference is CPU/GPU-bound and synchronous.
It MUST be called via run_in_threadpool to avoid blocking the async event loop.

Model is loaded once at module level (singleton) to avoid repeated
disk reads. First call may take a few seconds to load the model.

initial_prompt:
  Pass a section-specific vocabulary hint so Whisper knows the domain
  context *before* it starts decoding — dramatically reduces errors on
  Ayurvedic terms, SGP medicine codes, and organ system abbreviations.
"""

import logging
import os
import tempfile
from typing import Optional

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# ── Model singleton ────────────────────────────────────────────────────────────
# Loaded lazily on first transcription request.
# "small" is the minimum recommended size for medical/domain vocabulary.
# It is ~244 MB vs 73 MB for "base" but dramatically more accurate for
# Ayurvedic terms, mixed-language dictation, and abbreviated codes.
_whisper_model = None


def _get_model():
    """Load faster-whisper model once and cache it in process memory."""
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            model_size  = os.getenv("WHISPER_MODEL_SIZE", "small")   # upgraded from "base"
            device      = os.getenv("WHISPER_DEVICE", "cpu")
            compute     = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
            logger.info(
                f"Loading faster-whisper model: {model_size} on {device} ({compute})"
            )
            _whisper_model = WhisperModel(model_size, device=device, compute_type=compute)
            logger.info("faster-whisper model loaded successfully")
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Run: pip install faster-whisper"
            )
    return _whisper_model


def _transcribe_sync(
    audio_bytes: bytes,
    language: Optional[str] = None,
    initial_prompt: Optional[str] = None,
) -> str:
    """
    Synchronous transcription — runs in threadpool.
    Writes audio bytes to a temp file (faster-whisper requires a file path).
    Returns the full transcript as a single string.

    Args:
        audio_bytes:    Raw WAV/WebM audio bytes from the Flutter app.
        language:       Optional ISO language code (e.g. "en", "hi", "te").
                        Pass None to let Whisper auto-detect.
        initial_prompt: Section-specific vocabulary hint. Whisper conditions
                        its first token predictions on this text, dramatically
                        improving accuracy for domain-specific terms.
    """
    model = _get_model()

    # faster-whisper requires a file path, not raw bytes
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        transcribe_kwargs = dict(
            language=language,
            beam_size=5,
            vad_filter=True,                              # skip silence automatically
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=True,              # helps with continuous dictation
        )
        if initial_prompt:
            transcribe_kwargs["initial_prompt"] = initial_prompt

        segments, info = model.transcribe(tmp_path, **transcribe_kwargs)

        transcript = " ".join(seg.text.strip() for seg in segments)
        logger.info(
            f"Transcribed {info.duration:.1f}s | "
            f"lang={info.language} (prob={info.language_probability:.2f}) | "
            f"prompt={'yes' if initial_prompt else 'no'}"
        )
        return transcript.strip()

    finally:
        os.unlink(tmp_path)


async def transcribe_audio(
    audio_bytes: bytes,
    language: Optional[str] = None,
    initial_prompt: Optional[str] = None,
) -> str:
    """
    Async wrapper — dispatches blocking transcription to a threadpool.
    Safe to call from FastAPI route handlers and background tasks.

    Args:
        audio_bytes:    Raw audio bytes from the Flutter app.
        language:       Optional ISO code for forced language detection.
        initial_prompt: Section-specific vocabulary hint for Whisper.
    """
    if not audio_bytes:
        raise ValueError("Empty audio bytes received")

    transcript = await run_in_threadpool(
        _transcribe_sync, audio_bytes, language, initial_prompt
    )

    if not transcript:
        raise ValueError("Transcription returned empty result")

    return transcript
