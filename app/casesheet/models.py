"""
Casesheet Module — Database Models
────────────────────────────────────
Two tables:
  casesheet_sessions  — one row per clinical encounter session
  casesheet_drafts    — one row per session, JSON column holds evolving draft
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import String, DateTime, JSON, Text, Index, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database import Base


class SessionStatus(str, Enum):
    ACTIVE    = "ACTIVE"     # doctor is currently dictating
    PAUSED    = "PAUSED"     # doctor paused mid-session
    FINALIZED = "FINALIZED"  # pushed to ERPNext encounter
    FAILED    = "FAILED"     # finalization failed


class CasesheetSession(Base):
    """
    One row per clinical session.
    Linked to ERPNext: patient, appointment, doctor.
    """
    __tablename__ = "casesheet_sessions"
    __table_args__ = (
        Index("ix_session_doctor_status", "doctor_id", "status"),
    )

    id:             Mapped[str] = mapped_column(String(36), primary_key=True,
                                                default=lambda: str(uuid.uuid4()))
    patient_id:     Mapped[str] = mapped_column(String(100), nullable=False)  # ERPNext Patient name
    patient_name:   Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # cached display name
    doctor_id:      Mapped[str] = mapped_column(String(100), nullable=False)  # ERPNext Practitioner name
    appointment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lead_id:        Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status:         Mapped[str] = mapped_column(String(20), default=SessionStatus.ACTIVE)
    erp_encounter_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_error:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # stores finalization failure reason
    created_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                      server_default=func.now(), onupdate=func.now())

    draft: Mapped[Optional["CasesheetDraft"]] = relationship(
        back_populates="session", uselist=False, lazy="selectin"
    )


class CasesheetDraft(Base):
    """
    One row per session. JSON column stores the evolving clinical draft.

    Draft structure (sections filled progressively as audio chunks arrive):
    {
        "chief_complaint":          { ... },
        "pulse_diagnosis":          { ... },
        "panchakarma":              { ... },
        "anamnesis":                { ... },
        "ayurvedic_supplements":    { ... },
        "treatment_and_background": { ... },
        "personal_history":         { ... },
        "systemic_examination":     { ... },
        "past_medical_history":     { ... },
        "assessment_and_plan":      { ... },
        "_raw_transcripts":         { section: "raw text fallback" }
    }
    """
    __tablename__ = "casesheet_drafts"

    id:         Mapped[str] = mapped_column(String(36), primary_key=True,
                                            default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("casesheet_sessions.id"),
                                            nullable=False, unique=True)
    draft:      Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now(), onupdate=func.now())

    session: Mapped["CasesheetSession"] = relationship(back_populates="draft")
