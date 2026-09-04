
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import String, DateTime, JSON, Text, Index, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database import Base


class SessionStatus(str, Enum):
    ACTIVE     = "ACTIVE"
    PAUSED     = "PAUSED"
    PROCESSING = "PROCESSING"
    FINALIZED  = "FINALIZED"
    FAILED     = "FAILED"


class CasesheetSession(Base):
    __tablename__ = "casesheet_sessions"
    __table_args__ = (
        Index("ix_session_doctor_status", "doctor_id", "status"),
    )

    id:             Mapped[str] = mapped_column(String(36), primary_key=True,
                                                default=lambda: str(uuid.uuid4()))
    patient_id:     Mapped[str] = mapped_column(String(100), nullable=False)
    patient_name:   Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    doctor_id:      Mapped[str] = mapped_column(String(100), nullable=False)
    appointment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lead_id:        Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status:         Mapped[str] = mapped_column(String(20), default=SessionStatus.ACTIVE)
    erp_encounter_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_error:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processing_progress: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=None)
    created_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                      server_default=func.now(), onupdate=func.now())

    draft: Mapped[Optional["CasesheetDraft"]] = relationship(
        back_populates="session", uselist=False, lazy="selectin"
    )


class CasesheetDraft(Base):
    __tablename__ = "casesheet_drafts"

    id:         Mapped[str] = mapped_column(String(36), primary_key=True,
                                            default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("casesheet_sessions.id"),
                                            nullable=False, unique=True)
    draft:      Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now(), onupdate=func.now())

    session: Mapped["CasesheetSession"] = relationship(back_populates="draft")
