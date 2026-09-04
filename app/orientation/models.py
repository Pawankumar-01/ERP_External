
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List

from sqlalchemy import String, DateTime, Enum as SAEnum, Integer, Float, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pydantic import BaseModel, Field

from app.config.database import Base



class SessionStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    ENDED = "ENDED"
    CANCELLED = "CANCELLED"


class AttendanceStatus(str, Enum):
    REGISTERED = "REGISTERED"
    JOINED = "JOINED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    ABSENT = "ABSENT"



class OrientationSession(Base):
    __tablename__ = "orientation_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    livekit_room_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        SAEnum(SessionStatus), default=SessionStatus.SCHEDULED, nullable=False
    )
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    participants: Mapped[List["OrientationParticipant"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", lazy="selectin"
    )


class OrientationParticipant(Base):
    __tablename__ = "orientation_participants"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orientation_sessions.id"), nullable=False
    )
    lead_id: Mapped[str] = mapped_column(String(36), nullable=False)
    livekit_identity: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    join_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    leave_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    watch_seconds: Mapped[int] = mapped_column(Integer, default=0)
    watch_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    attendance_status: Mapped[str] = mapped_column(
        SAEnum(AttendanceStatus), default=AttendanceStatus.REGISTERED, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped["OrientationSession"] = relationship(back_populates="participants")



class SessionCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    scheduled_at: Optional[datetime] = None


class AddParticipantRequest(BaseModel):
    lead_id: str
    lead_name: str


class TokenRequest(BaseModel):
    session_id: str
    lead_id:    str
    lead_name:  str
    mobile:     Optional[str] = None


class ParticipantResponse(BaseModel):
    id: str
    lead_id: str
    livekit_identity: Optional[str]
    join_time: Optional[datetime]
    leave_time: Optional[datetime]
    watch_seconds: int
    watch_percentage: float
    attendance_status: AttendanceStatus

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    id: str
    title: str
    livekit_room_name: str
    status: SessionStatus
    scheduled_at: Optional[datetime]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    duration_seconds: Optional[int]
    created_at: datetime
    participants: List[ParticipantResponse] = []

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    token: str
    livekit_url: str
    room_name: str
    identity: str
    session_id: str



class AssessmentQuestion(Base):
    __tablename__ = "assessment_questions"

    id:            Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    question_text: Mapped[str] = mapped_column(String, nullable=False)
    option_a:      Mapped[str] = mapped_column(String, nullable=False)
    option_b:      Mapped[str] = mapped_column(String, nullable=False)
    option_c:      Mapped[str] = mapped_column(String, nullable=False)
    option_d:      Mapped[str] = mapped_column(String, nullable=False)
    correct_option:Mapped[str] = mapped_column(String, nullable=False)
    order_index:   Mapped[int] = mapped_column(Integer, default=0)
    is_active:     Mapped[bool] = mapped_column(default=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssessmentResult(Base):
    __tablename__ = "assessment_results"

    id:             Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id:        Mapped[str] = mapped_column(String, nullable=False)
    session_id:     Mapped[str] = mapped_column(String, ForeignKey("orientation_sessions.id"), nullable=False)
    question_id:    Mapped[str] = mapped_column(String, ForeignKey("assessment_questions.id"), nullable=False)
    selected_option:Mapped[str] = mapped_column(String, nullable=False)
    correct_option: Mapped[str] = mapped_column(String, nullable=False)
    is_correct:     Mapped[bool] = mapped_column(default=False)
    language:       Mapped[str] = mapped_column(String, default="en")
    submitted_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())



class AssessmentQuestionResponse(BaseModel):
    id:             str
    question_text:  str
    option_a:       str
    option_b:       str
    option_c:       str
    option_d:       str
    order_index:    int

    class Config:
        from_attributes = True


class AssessmentAnswerInput(BaseModel):
    question_id:     str
    selected_option: str = Field(..., pattern="^[ABCD]$")


class AssessmentSubmitRequest(BaseModel):
    lead_id:    str
    session_id: str
    answers:    List[AssessmentAnswerInput]
    language:   str = "en"


class AssessmentSubmitResponse(BaseModel):
    total:    int
    correct:  int
    results:  List[dict]
    message:  str