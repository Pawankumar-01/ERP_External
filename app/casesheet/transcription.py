
import logging
import os
import tempfile
from typing import Optional

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

_whisper_model = None


def _get_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            model_size  = os.getenv("WHISPER_MODEL_SIZE", "small")
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
    model = _get_model()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        effective_lang = language or os.getenv("WHISPER_LANGUAGE", "en")

        transcribe_kwargs = dict(
            language=effective_lang,
            beam_size=2,
            temperature=0.0,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 1000, "speech_pad_ms": 300},
            condition_on_previous_text=False,
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
    if not audio_bytes:
        raise ValueError("Empty audio bytes received")

    transcript = await run_in_threadpool(
        _transcribe_sync, audio_bytes, language, initial_prompt
    )

    if not transcript:
        raise ValueError("Transcription returned empty result")

    return transcript
