"""Private, disabled-by-default artifacts for casesheet accuracy evaluation.

The capture layer is intentionally best-effort: an unavailable evaluation disk
must never change the clinical pipeline result. Files are created with private
permissions and the configured directory is not exposed by FastAPI.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings


logger = logging.getLogger(__name__)

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")
_TRACE_CONTEXT: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    "casesheet_evaluation_trace", default=None
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _component(value: Any) -> str:
    cleaned = _SAFE_COMPONENT.sub("_", str(value or "").strip()).strip("._")
    if not cleaned:
        raise ValueError("empty evaluation artifact path component")
    return cleaned[:180]


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")


class EvaluationCapture:
    """Write immutable per-job artifacts and append-only correction events."""

    def __init__(self, enabled: bool, root: str | Path, max_audio_mb: int = 100):
        self.enabled = bool(enabled)
        self.root = Path(root).expanduser().resolve()
        self.max_audio_bytes = max(1, int(max_audio_mb)) * 1024 * 1024
        self._locks_guard = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {}

    def _lock_for(self, session_id: str) -> threading.Lock:
        key = _component(session_id)
        with self._locks_guard:
            return self._session_locks.setdefault(key, threading.Lock())

    def session_dir(self, session_id: str) -> Path:
        return self.root / _component(session_id)

    def job_dir(self, session_id: str, batch_index: int, job_id: str) -> Path:
        if batch_index not in (1, 2, 3):
            raise ValueError("batch_index must be 1, 2 or 3")
        return self.session_dir(session_id) / f"batch_{batch_index}" / _component(job_id)

    @staticmethod
    def _private_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        EvaluationCapture._private_directory(path.parent)
        if path.exists():
            raise FileExistsError(f"evaluation artifact already exists: {path.name}")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def _ensure_manifest(self, session_id: str) -> None:
        directory = self.session_dir(session_id)
        self._private_directory(directory)
        manifest = directory / "manifest.json"
        if manifest.exists():
            return
        self._atomic_write(
            manifest,
            _json_bytes({
                "capture_version": "1.0.0",
                "session_id": _component(session_id),
                "created_at": _utc_now(),
                "contains_clinical_data": True,
                "storage": "private_filesystem",
            }),
        )

    def _append_json_line(self, path: Path, value: Any) -> None:
        self._private_directory(path.parent)
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write((json.dumps(value, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    async def _best_effort(self, operation: str, session_id: str, function: Any) -> bool:
        if not self.enabled:
            return False
        try:
            # Capture runs only in an explicitly enabled evaluation session.
            # Keep the write synchronous so every artifact is durable before
            # the clinical job advances; production takes the zero-I/O path.
            function()
            return True
        except Exception:
            logger.exception("Evaluation capture failed: operation=%s session=%s", operation, session_id)
            return False

    async def capture_batch_audio(
        self,
        session_id: str,
        batch_index: int,
        job_id: str,
        audio_bytes: bytes,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        def write() -> None:
            with self._lock_for(session_id):
                self._ensure_manifest(session_id)
                directory = self.job_dir(session_id, batch_index, job_id)
                if len(audio_bytes) > self.max_audio_bytes:
                    self._append_json_line(
                        self.session_dir(session_id) / "events.jsonl",
                        {
                            "event": "audio_capture_skipped",
                            "at": _utc_now(),
                            "batch_index": batch_index,
                            "job_id": job_id,
                            "size_bytes": len(audio_bytes),
                            "limit_bytes": self.max_audio_bytes,
                        },
                    )
                    return
                self._atomic_write(directory / "original.wav", audio_bytes)
                payload = {
                    "captured_at": _utc_now(),
                    "batch_index": batch_index,
                    "job_id": job_id,
                    "size_bytes": len(audio_bytes),
                    "sha256": hashlib.sha256(audio_bytes).hexdigest(),
                    **(metadata or {}),
                }
                self._atomic_write(directory / "audio_metadata.json", _json_bytes(payload))
                self._append_json_line(
                    self.session_dir(session_id) / "events.jsonl",
                    {"event": "audio_captured", **payload},
                )

        return await self._best_effort("capture_batch_audio", session_id, write)

    async def write_job_artifact(
        self,
        session_id: str,
        batch_index: int,
        job_id: str,
        artifact_name: str,
        payload: Any,
    ) -> bool:
        name = _component(artifact_name)
        if not name.endswith(".json"):
            name += ".json"

        def write() -> None:
            with self._lock_for(session_id):
                self._ensure_manifest(session_id)
                self._atomic_write(
                    self.job_dir(session_id, batch_index, job_id) / name,
                    _json_bytes(payload),
                )

        return await self._best_effort("write_job_artifact", session_id, write)

    async def write_revision_snapshot(
        self, session_id: str, revision: int, draft: dict[str, Any]
    ) -> bool:
        def write() -> None:
            with self._lock_for(session_id):
                self._ensure_manifest(session_id)
                path = self.session_dir(session_id) / "revisions" / f"revision_{int(revision):06d}.json"
                self._atomic_write(path, _json_bytes(draft))

        return await self._best_effort("write_revision_snapshot", session_id, write)

    async def write_finalization_artifact(
        self, session_id: str, attempt_id: str, artifact_name: str, payload: Any
    ) -> bool:
        name = _component(artifact_name)
        if not name.endswith(".json"):
            name += ".json"

        def write() -> None:
            with self._lock_for(session_id):
                self._ensure_manifest(session_id)
                path = (
                    self.session_dir(session_id)
                    / "finalization"
                    / _component(attempt_id)
                    / name
                )
                self._atomic_write(path, _json_bytes(payload))

        return await self._best_effort("write_finalization_artifact", session_id, write)

    async def append_correction(self, session_id: str, correction: dict[str, Any]) -> bool:
        payload = {"captured_at": _utc_now(), **correction}

        def write() -> None:
            with self._lock_for(session_id):
                self._ensure_manifest(session_id)
                self._append_json_line(
                    self.session_dir(session_id) / "correction_history.jsonl", payload
                )

        return await self._best_effort("append_correction", session_id, write)

    async def append_event(self, session_id: str, event: str, payload: Optional[dict[str, Any]] = None) -> bool:
        value = {"event": event, "at": _utc_now(), **(payload or {})}

        def write() -> None:
            with self._lock_for(session_id):
                self._ensure_manifest(session_id)
                self._append_json_line(self.session_dir(session_id) / "events.jsonl", value)

        return await self._best_effort("append_event", session_id, write)


_configured_root = Path(settings.CASE_EVAL_CAPTURE_ROOT).expanduser()
if not _configured_root.is_absolute():
    _configured_root = Path(__file__).resolve().parents[2] / _configured_root

evaluation_capture = EvaluationCapture(
    enabled=settings.CASE_EVAL_CAPTURE_ENABLED,
    root=_configured_root,
    max_audio_mb=settings.CASE_EVAL_CAPTURE_MAX_AUDIO_MB,
)


def begin_llm_trace(session_id: str, batch_index: int, job_id: str) -> contextvars.Token:
    """Start an in-memory trace inherited by concurrent section tasks."""
    context = None
    if evaluation_capture.enabled:
        context = {
            "session_id": session_id,
            "batch_index": batch_index,
            "job_id": job_id,
            "entries": [],
        }
    return _TRACE_CONTEXT.set(context)


def record_llm_trace(label: str, stage: str, payload: Any) -> None:
    """Record the exact trace payload without writing on the LLM hot path."""
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    context["entries"].append(
        {
            "captured_at": _utc_now(),
            "label": label,
            "stage": stage,
            "payload": deepcopy(payload),
        }
    )


def end_llm_trace(token: contextvars.Token) -> list[dict[str, Any]]:
    context = _TRACE_CONTEXT.get()
    entries = list(context.get("entries", [])) if context else []
    _TRACE_CONTEXT.reset(token)
    return entries
