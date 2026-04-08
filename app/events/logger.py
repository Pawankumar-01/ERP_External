"""
Event Logger — Structured Audit Trail
──────────────────────────────────────
Every significant action in the system emits a domain event.
Events are:
  1. Logged to console (always)
  2. Persisted to PostgreSQL event_logs table (when DB is available)

This provides a full, immutable audit trail for healthcare compliance.
Event logging NEVER raises — it catches all exceptions internally.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy import String, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from pydantic import BaseModel

from app.config.database import Base

logger = logging.getLogger(__name__)


# ─── Event Types ──────────────────────────────────────────────────────────────

class EventType(str, Enum):
    # Lead lifecycle
    LEAD_CREATED              = "lead_created"
    LEAD_STATUS_UPDATED       = "lead_status_updated"

    # Orientation session lifecycle
    ORIENTATION_SESSION_CREATED = "orientation_session_created"
    ORIENTATION_SESSION_STARTED = "orientation_session_started"
    ORIENTATION_SESSION_ENDED   = "orientation_session_ended"

    # Participant attendance
    ORIENTATION_PARTICIPANT_REGISTERED = "orientation_participant_registered"
    ORIENTATION_PARTICIPANT_JOINED     = "orientation_participant_joined"
    ORIENTATION_PARTICIPANT_LEFT       = "orientation_participant_left"
    ORIENTATION_COMPLETED              = "orientation_completed"

    # LiveKit room events (from webhook)
    ROOM_STARTED  = "room_started"
    ROOM_FINISHED = "room_finished"

    # ERP Bridge calls
    ERP_CALL_SUCCESS     = "erp_call_success"       # ← new
    ERP_CALL_FAILED      = "erp_call_failed"
    ERP_ATTENDANCE_CREATED = "erp_attendance_created"

    # Downstream clinical events
    APPOINTMENT_CREATED = "appointment_created"
    ENCOUNTER_CREATED   = "encounter_created"       # ← new


# ─── ORM Model ────────────────────────────────────────────────────────────────

class EventLog(Base):
    """Persistent event log stored in PostgreSQL for audit and compliance."""
    __tablename__ = "event_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    entity_type:  Mapped[str]          = mapped_column(String(100), nullable=False)
    entity_id:    Mapped[str]          = mapped_column(String(100), nullable=False)
    event_type:   Mapped[str]          = mapped_column(String(100), nullable=False)
    payload:      Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # JSON
    triggered_by: Mapped[str]          = mapped_column(String(100), nullable=False)
    timestamp:    Mapped[datetime]     = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ─── Pydantic Response Schema ─────────────────────────────────────────────────

class EventLogResponse(BaseModel):
    id:           str
    entity_type:  str
    entity_id:    str
    event_type:   str
    payload:      Optional[Dict[str, Any]]
    triggered_by: str
    timestamp:    datetime

    model_config = {"from_attributes": True}


# ─── Logger Service ───────────────────────────────────────────────────────────

class EventLogger:
    """
    Emits structured domain events to console and PostgreSQL.
    Safe to call from any context — never raises.
    """

    def __init__(self):
        self._db_enabled = False

    async def initialize(self):
        """Called at app startup. Creates tables and enables DB persistence."""
        try:
            from app.config.database import create_all_tables
            await create_all_tables()
            self._db_enabled = True
            logger.info("Event logger: DB persistence enabled")
        except Exception as e:
            logger.warning(f"Event logger: DB unavailable, console-only mode ({e})")

    async def shutdown(self):
        pass

    async def log(
        self,
        entity_type:  str,
        entity_id:    str,
        event_type:   EventType,
        payload:      Dict[str, Any],
        triggered_by: str = "system",
    ) -> None:
        """
        Emit a structured domain event.
        Always writes to console. Persists to DB when available.
        Exceptions are caught and logged — never propagated.
        """
        payload_str = json.dumps(payload, default=str)

        # 1. Console (always)
        logger.info(
            f"[EVENT] {event_type.value} | "
            f"entity={entity_type}:{entity_id} | "
            f"by={triggered_by} | {payload_str}"
        )

        # 2. PostgreSQL (when available)
        if self._db_enabled:
            try:
                from app.config.database import AsyncSessionLocal
                async with AsyncSessionLocal() as db:
                    db.add(EventLog(
                        entity_type=entity_type,
                        entity_id=entity_id,
                        event_type=event_type.value,
                        payload=payload_str,
                        triggered_by=triggered_by,
                    ))
                    await db.commit()
            except Exception as e:
                # Never crash the main request flow due to logging failure
                logger.error(f"Failed to persist event to DB: {e}")


event_logger = EventLogger()