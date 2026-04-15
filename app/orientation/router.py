"""
Orientation Router — HTTP Endpoints
Session creation, participant management, token generation, lifecycle control.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.orientation.models import (
    SessionCreate,
    SessionResponse,
    AddParticipantRequest,
    ParticipantResponse,
    TokenRequest,
    TokenResponse,
)
from app.orientation.service import orientation_service

router = APIRouter()


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(data: SessionCreate, db: AsyncSession = Depends(get_db)):
    """
    Create a new orientation session.
    Provisions a LiveKit room and returns session details.
    """
    try:
        session = await orientation_service.create_session(db, data)
        return session
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions", response_model=List[SessionResponse])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """List all orientation sessions ordered by creation date."""
    return await orientation_service.list_sessions(db)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single orientation session with all participants."""
    session = await orientation_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/sessions/{session_id}/participants")
async def get_participants(session_id: str, db: AsyncSession = Depends(get_db)):
    """Get all participants for a session with their attendance status."""
    session = await orientation_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.participants


@router.post("/sessions/{session_id}/participants", response_model=ParticipantResponse, status_code=201)
async def add_participant(
    session_id: str,
    req: AddParticipantRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a lead as a participant in an orientation session."""
    try:
        participant = await orientation_service.add_participant(db, session_id, req)
        return participant
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/token", response_model=TokenResponse)
async def get_participant_token(
    session_id: str,
    req: TokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a LiveKit JWT for a participant to join the orientation room.
    Returns token + WebSocket URL for the frontend SDK.
    """
    try:
        result = await orientation_service.generate_token(
            db, session_id, req.lead_id, req.lead_name, mobile=req.mobile
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/host-token")
async def get_host_token(
    session_id: str,
    host_name: str = "Doctor",
    db: AsyncSession = Depends(get_db),
):
    """Generate a privileged host/presenter token for the doctor."""
    try:
        return await orientation_service.generate_host_token(db, session_id, host_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/sessions/{session_id}/start", response_model=SessionResponse)
async def start_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """Mark session as LIVE and record the start time."""
    try:
        return await orientation_service.start_session(db, session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sessions/{session_id}/end")
async def end_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """
    End the session. Called by host frontend.
    If session already ended (e.g. by room_finished webhook), returns gracefully.
    """
    try:
        result = await orientation_service.end_session(db, session_id)
        if result is None:
            return {"status": "already_ended", "session_id": session_id}
        return {"status": "ended", "session_id": session_id}
    except ValueError as e:
        # Already ended or not found — not an error from host perspective
        return {"status": "already_ended", "session_id": session_id, "detail": str(e)}