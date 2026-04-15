"""
Casesheet API Router
────────────────────
Endpoints:
  POST   /start                    → init session, return session_id
  POST   /{session_id}/audio       → accept audio chunk, transcribe + extract in background
  GET    /{session_id}/draft       → fetch current draft JSON
  PATCH  /{session_id}/draft       → manually update specific sections
  POST   /{session_id}/finalize    → push final draft to ERPNext Patient Encounter

Architecture:
  - Audio processing (Whisper + LLM) runs in BackgroundTasks — never blocks event loop
  - Draft state lives in PostgreSQL (JSON column) — no in-memory state
  - ERPNext sync happens synchronously on /finalize (doctor waits for confirmation)
"""

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.casesheet.models import CasesheetSession, CasesheetDraft, SessionStatus
from app.casesheet.transcription import transcribe_audio
from app.casesheet.llm_service import llm_service
from app.casesheet.prompts import VALID_SECTIONS
from app.erp_bridge.service import erp_bridge_service

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Schemas ────────────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    patient_id:     str
    doctor_id:      str
    appointment_id: Optional[str] = None
    lead_id:        Optional[str] = None


class StartSessionResponse(BaseModel):
    session_id:     str
    patient_id:     str
    doctor_id:      str
    status:         str


class DraftPatchRequest(BaseModel):
    """Patch one or more sections of the draft manually."""
    updates: Dict[str, Any]


class FinalizeResponse(BaseModel):
    session_id:      str
    erp_encounter_id: Optional[str]
    status:          str
    message:         str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/start", response_model=StartSessionResponse, status_code=201)
async def start_session(
    req: StartSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Initialize a new casesheet session.
    Returns session_id — the Flutter app holds this for the entire encounter.
    """
    session = CasesheetSession(
        id=str(uuid.uuid4()),
        patient_id=req.patient_id,
        doctor_id=req.doctor_id,
        appointment_id=req.appointment_id,
        lead_id=req.lead_id,
        status=SessionStatus.ACTIVE,
    )
    draft = CasesheetDraft(
        session_id=session.id,
        draft={},
    )
    db.add(session)
    db.add(draft)
    await db.commit()

    logger.info(f"Casesheet session {session.id} started for patient {req.patient_id}")

    return StartSessionResponse(
        session_id=session.id,
        patient_id=session.patient_id,
        doctor_id=session.doctor_id,
        status=session.status,
    )


@router.post("/{session_id}/audio", status_code=202)
async def upload_audio(
    session_id: str,
    background_tasks: BackgroundTasks,
    section: str = Form(...),
    language: Optional[str] = Form(None),
    audio: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept an audio chunk from Flutter app.
    Triggers transcription + LLM extraction as a BackgroundTask.
    Returns 202 immediately — doctor keeps dictating while processing happens.

    section: one of VALID_SECTIONS (chief_complaint, overall_vpk, etc.)
    language: optional ISO code (e.g. "te" for Telugu, "hi" for Hindi, "en" for English)
    """
    if section not in VALID_SECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid section '{section}'. Valid: {', '.join(sorted(VALID_SECTIONS))}"
        )

    session = await _get_session(db, session_id)
    if session.status == SessionStatus.FINALIZED:
        raise HTTPException(status_code=409, detail="Session already finalized")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file received")

    logger.info(
        f"Audio received: session={session_id} section={section} "
        f"size={len(audio_bytes)} bytes lang={language}"
    )

    # Dispatch to background — response returns immediately
    background_tasks.add_task(
        _process_audio_background,
        session_id=session_id,
        section=section,
        audio_bytes=audio_bytes,
        language=language,
    )

    return {
        "status":     "processing",
        "session_id": session_id,
        "section":    section,
        "message":    "Audio received. Transcription and extraction running in background.",
    }


@router.get("/{session_id}/draft")
async def get_draft(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch the current casesheet draft from PostgreSQL.
    Flutter app polls this to show the doctor the evolving structured output.
    """
    session = await _get_session(db, session_id)

    result = await db.execute(
        select(CasesheetDraft).where(CasesheetDraft.session_id == session_id)
    )
    draft_row = result.scalar_one_or_none()

    return {
        "session_id":    session_id,
        "patient_id":    session.patient_id,
        "doctor_id":     session.doctor_id,
        "status":        session.status,
        "draft":         draft_row.draft if draft_row else {},
        "updated_at":    draft_row.updated_at.isoformat() if draft_row else None,
    }


@router.patch("/{session_id}/draft")
async def patch_draft(
    session_id: str,
    req: DraftPatchRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually update one or more sections of the draft.
    Doctor can correct LLM output from the Flutter app before finalizing.
    """
    session = await _get_session(db, session_id)
    if session.status == SessionStatus.FINALIZED:
        raise HTTPException(status_code=409, detail="Cannot edit a finalized session")

    result = await db.execute(
        select(CasesheetDraft).where(CasesheetDraft.session_id == session_id)
    )
    draft_row = result.scalar_one_or_none()
    if not draft_row:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Merge updates into existing draft
    current = dict(draft_row.draft or {})
    current.update(req.updates)
    draft_row.draft = current
    await db.commit()

    logger.info(f"Draft manually updated: session={session_id} sections={list(req.updates.keys())}")

    return {
        "status":     "updated",
        "session_id": session_id,
        "sections_updated": list(req.updates.keys()),
    }


@router.post("/{session_id}/finalize", response_model=FinalizeResponse)
async def finalize_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Finalize the casesheet session:
    1. Fetch the completed draft from PostgreSQL
    2. Create a Patient Encounter in ERPNext with the draft as notes
    3. Mark session as FINALIZED
    4. Return the ERPNext encounter ID

    Doctor taps "Submit" in Flutter app → this runs synchronously so they
    see confirmation before leaving the encounter screen.
    """
    session = await _get_session(db, session_id)

    if session.status == SessionStatus.FINALIZED:
        return FinalizeResponse(
            session_id=session_id,
            erp_encounter_id=session.erp_encounter_id,
            status="already_finalized",
            message="This session was already submitted to ERPNext.",
        )

    result = await db.execute(
        select(CasesheetDraft).where(CasesheetDraft.session_id == session_id)
    )
    draft_row = result.scalar_one_or_none()
    draft_data = draft_row.draft if draft_row else {}

    if not draft_data:
        raise HTTPException(
            status_code=400,
            detail="Draft is empty. Please record at least one audio section before finalizing."
        )

    # Create Patient Encounter in ERPNext
    import json as _json
    encounter = await erp_bridge_service.create_encounter({
        "patient":         session.patient_id,
        "practitioner":    session.doctor_id,
        "appointment":     session.appointment_id,
        "encounter_date":  _today(),
        "status":          "Draft",
        "notes":           _json.dumps(draft_data, ensure_ascii=False, indent=2),
    })

    if not encounter:
        # Mark as failed but don't lose the draft
        session.status = SessionStatus.FAILED
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail="Failed to create encounter in ERPNext. Draft is preserved — retry /finalize."
        )

    encounter_id = encounter.get("name")
    session.status          = SessionStatus.FINALIZED
    session.erp_encounter_id = encounter_id
    await db.commit()

    logger.info(
        f"Session {session_id} finalized → ERPNext encounter {encounter_id} "
        f"for patient {session.patient_id}"
    )

    return FinalizeResponse(
        session_id=session_id,
        erp_encounter_id=encounter_id,
        status="finalized",
        message=f"Case sheet submitted to ERPNext. Encounter ID: {encounter_id}",
    )


# ── Background task ────────────────────────────────────────────────────────────

async def _process_audio_background(
    session_id: str,
    section:    str,
    audio_bytes: bytes,
    language:   Optional[str],
) -> None:
    """
    Background task: transcribe audio → extract clinical section → save to DB.
    Runs outside the request lifecycle — has its own DB session.
    """
    from app.config.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            # 1. Transcribe audio (runs in threadpool inside transcribe_audio)
            logger.info(f"Background: transcribing section={section} session={session_id}")
            transcript = await transcribe_audio(audio_bytes, language=language)
            logger.info(f"Background: transcript length={len(transcript)} chars")

            # 2. Extract structured section via LLM
            logger.info(f"Background: LLM extraction for section={section}")
            section_data = await llm_service.extract_section(section, transcript)

            # 3. Save to draft
            result = await db.execute(
                select(CasesheetDraft).where(CasesheetDraft.session_id == session_id)
            )
            draft_row = result.scalar_one_or_none()

            if draft_row:
                current = dict(draft_row.draft or {})
                current[section] = section_data
                # Also preserve raw transcript in a private key
                if "_raw_transcripts" not in current:
                    current["_raw_transcripts"] = {}
                current["_raw_transcripts"][section] = transcript
                draft_row.draft = current
                await db.commit()
                logger.info(
                    f"Background: section '{section}' saved to draft "
                    f"session={session_id}"
                )
            else:
                logger.error(f"Background: draft row not found for session {session_id}")

        except Exception as e:
            logger.error(
                f"Background processing failed: session={session_id} "
                f"section={section} error={e}"
            )
            # Save error marker to draft so Flutter app knows processing failed
            try:
                result = await db.execute(
                    select(CasesheetDraft).where(CasesheetDraft.session_id == session_id)
                )
                draft_row = result.scalar_one_or_none()
                if draft_row:
                    current = dict(draft_row.draft or {})
                    current[section] = {"_error": str(e), "_raw": ""}
                    draft_row.draft = current
                    await db.commit()
            except Exception:
                pass


# ── Helper ─────────────────────────────────────────────────────────────────────

async def _get_session(db: AsyncSession, session_id: str) -> CasesheetSession:
    result = await db.execute(
        select(CasesheetSession).where(CasesheetSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session


def _today() -> str:
    from datetime import date
    return date.today().isoformat()
