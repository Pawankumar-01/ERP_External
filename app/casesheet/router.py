"""
Casesheet API Router
────────────────────
Endpoints:
  POST   /start                    → init session, return session_id
  POST   /{session_id}/audio       → accept audio chunk, transcribe + extract in background
  GET    /{session_id}/draft       → fetch current draft JSON
  PATCH  /{session_id}/draft       → manually update specific sections
  POST   /{session_id}/retranscribe/{section} → re-process corrected transcript
  POST   /{session_id}/finalize    → push final draft to ERPNext Patient Encounter
  GET    /                         → list sessions (paginated, filter by doctor/patient)

Architecture:
  - Audio processing (Whisper + LLM) runs in BackgroundTasks — never blocks event loop
  - Draft state lives in PostgreSQL (JSON column) — no in-memory state
  - start: lookup patient by mobile number
  - finalize: creates SGP Encounter in ERPNext with all casesheet fields
"""

import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.casesheet.models import CasesheetSession, CasesheetDraft, SessionStatus
from app.casesheet.transcription import transcribe_audio
from app.casesheet.llm_service import llm_service
from app.casesheet.prompts import VALID_SECTIONS, WHISPER_INITIAL_PROMPTS
from app.erp_bridge.service import erp_bridge_service
from app.events.logger import event_logger, EventType

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Schemas ────────────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    mobile:             str
    doctor_id:          str
    skip_payment_check: bool = False

class StartSessionResponse(BaseModel):
    session_id:       str
    patient_id:       str
    doctor_id:        str
    appointment_id:   Optional[str]
    status:           str
    payment_verified: bool
    payment_entry:    Optional[str]
    paid_amount:      Optional[float]


class SessionListItem(BaseModel):
    session_id:       str
    patient_id:       str
    doctor_id:        str
    appointment_id:   Optional[str]
    status:           str
    erp_encounter_id: Optional[str]
    created_at:       str
    model_config = {"from_attributes": True}


class DraftPatchRequest(BaseModel):
    updates: Dict[str, Any]


class RetranscribeRequest(BaseModel):
    transcript: str


class FinalizeResponse(BaseModel):
    session_id:         str
    erp_encounter_id:   Optional[str]
    appointment_closed: bool
    payment_linked:     bool
    status:             str
    message:            str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[SessionListItem])
async def list_sessions(
    doctor_id:  Optional[str] = Query(None),
    patient_id: Optional[str] = Query(None),
    status:     Optional[str] = Query(None),
    limit:      int           = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(CasesheetSession).order_by(desc(CasesheetSession.created_at)).limit(limit)
    if doctor_id:
        query = query.where(CasesheetSession.doctor_id == doctor_id)
    if patient_id:
        query = query.where(CasesheetSession.patient_id == patient_id)
    if status:
        query = query.where(CasesheetSession.status == status)
    result = await db.execute(query)
    sessions = result.scalars().all()
    return [
        SessionListItem(
            session_id=s.id,
            patient_id=s.patient_id,
            doctor_id=s.doctor_id,
            appointment_id=s.appointment_id,
            status=s.status,
            erp_encounter_id=s.erp_encounter_id,
            created_at=s.created_at.isoformat(),
        )
        for s in sessions
    ]


@router.get("/patient-lookup")
async def lookup_patient(mobile: str):
    """Lookup patient by mobile number before starting session."""
    patient = await erp_bridge_service.get_patient_by_mobile(mobile)
    if not patient:
        raise HTTPException(status_code=404, detail="No patient found with this mobile number.")
    return patient


@router.post("/start", response_model=StartSessionResponse, status_code=201)
async def start_session(
    req: StartSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    doctor_id        = req.doctor_id
    payment_verified = False
    payment_entry_id = None
    paid_amount      = None

    # ── Step 1: Lookup patient by mobile ──────────────────────────────────
    patient = await erp_bridge_service.get_patient_by_mobile(req.mobile)
    if not patient:
        raise HTTPException(
            status_code=404,
            detail=f"No patient found with mobile {req.mobile}."
        )
    patient_id   = patient.get("name")
    patient_name = patient.get("patient_name")
    logger.info(f"Patient found: {patient_id} ({patient_name})")

    if not doctor_id:
        raise HTTPException(status_code=400, detail="doctor_id is required.")

    # ── Step 2: Payment gate (optional — free consultations allowed) ───────
    if not req.skip_payment_check:
        payment = await erp_bridge_service.get_payment_for_patient(patient_id)
        if not payment:
            logger.warning(f"No payment found for {patient_id} — allowing free consultation")
            payment_verified = False
        else:
            payment_verified = True
            payment_entry_id = payment.get("name")
            paid_amount      = payment.get("paid_amount")
            await event_logger.log(
                entity_type="casesheet",
                entity_id=patient_id,
                event_type=EventType.PAYMENT_VERIFIED,
                payload={"patient_id": patient_id, "payment_entry": payment_entry_id, "paid_amount": paid_amount},
                triggered_by="api",
            )
            logger.info(f"Payment verified: {payment_entry_id} amount={paid_amount}")
    else:
        payment_verified = True

    # ── Step 3: Create session + draft ────────────────────────────────────
    session = CasesheetSession(
        id=str(uuid.uuid4()),
        patient_id=patient_id,
        doctor_id=doctor_id,
        appointment_id=None,
        lead_id=patient.get("custom_sgp_lead"),
        status=SessionStatus.ACTIVE,
    )
    draft = CasesheetDraft(session_id=session.id, draft={})
    db.add(session)
    db.add(draft)
    await db.commit()

    await event_logger.log(
        entity_type="casesheet",
        entity_id=session.id,
        event_type=EventType.CASESHEET_STARTED,
        payload={"patient_id": patient_id, "doctor_id": doctor_id, "payment_verified": payment_verified},
        triggered_by="api",
    )
    logger.info(f"Session {session.id} started | patient={patient_id} | doctor={doctor_id}")

    return StartSessionResponse(
        session_id=session.id,
        patient_id=session.patient_id,
        doctor_id=session.doctor_id,
        appointment_id=session.appointment_id,
        lead_id=session.lead_id,
        status=session.status,
        payment_verified=payment_verified,
        payment_entry=payment_entry_id,
        paid_amount=paid_amount,
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
    if section not in VALID_SECTIONS:
        raise HTTPException(status_code=400, detail=f"Invalid section '{section}'. Valid: {', '.join(sorted(VALID_SECTIONS))}")

    session = await _get_session(db, session_id)
    if session.status == SessionStatus.FINALIZED:
        raise HTTPException(status_code=409, detail="Session already finalized")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file received")

    logger.info(f"Audio received: session={session_id} section={section} size={len(audio_bytes)} bytes lang={language}")

    background_tasks.add_task(
        _process_audio_background,
        session_id=session_id,
        section=section,
        audio_bytes=audio_bytes,
        language=language,
    )

    return {"status": "processing", "session_id": session_id, "section": section, "message": "Audio received. Processing in background."}


@router.get("/{session_id}/draft")
async def get_draft(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _get_session(db, session_id)
    result = await db.execute(select(CasesheetDraft).where(CasesheetDraft.session_id == session_id))
    draft_row = result.scalar_one_or_none()
    return {
        "session_id": session_id,
        "patient_id": session.patient_id,
        "doctor_id":  session.doctor_id,
        "status":     session.status,
        "draft":      draft_row.draft if draft_row else {},
        "updated_at": draft_row.updated_at.isoformat() if draft_row else None,
    }


@router.patch("/{session_id}/draft")
async def patch_draft(session_id: str, req: DraftPatchRequest, db: AsyncSession = Depends(get_db)):
    session = await _get_session(db, session_id)
    if session.status == SessionStatus.FINALIZED:
        raise HTTPException(status_code=409, detail="Cannot edit a finalized session")
    result = await db.execute(select(CasesheetDraft).where(CasesheetDraft.session_id == session_id))
    draft_row = result.scalar_one_or_none()
    if not draft_row:
        raise HTTPException(status_code=404, detail="Draft not found")
    current = dict(draft_row.draft or {})
    current.update(req.updates)
    draft_row.draft = current
    await db.commit()
    logger.info(f"Draft updated: session={session_id} sections={list(req.updates.keys())}")
    return {"status": "updated", "session_id": session_id, "sections_updated": list(req.updates.keys())}


@router.post("/{session_id}/retranscribe/{section}", status_code=202)
async def retranscribe_section(
    session_id: str,
    section: str,
    req: RetranscribeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    if section not in VALID_SECTIONS:
        raise HTTPException(status_code=400, detail=f"Invalid section '{section}'.")
    session = await _get_session(db, session_id)
    if session.status == SessionStatus.FINALIZED:
        raise HTTPException(status_code=409, detail="Session already finalized")
    transcript = req.transcript.strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="transcript must not be empty")
    result = await db.execute(select(CasesheetDraft).where(CasesheetDraft.session_id == session_id))
    draft_row = result.scalar_one_or_none()
    if draft_row:
        current = dict(draft_row.draft or {})
        current[section] = {"status": "processing"}
        if "_raw_transcripts" not in current:
            current["_raw_transcripts"] = {}
        current["_raw_transcripts"][section] = transcript
        draft_row.draft = current
        await db.commit()
    background_tasks.add_task(_reprocess_transcript_background, session_id=session_id, section=section, transcript=transcript)
    return {"status": "processing", "session_id": session_id, "section": section, "message": "Re-extraction running."}


@router.post("/{session_id}/finalize", response_model=FinalizeResponse)
async def finalize_session(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await _get_session(db, session_id)

    if session.status == SessionStatus.FINALIZED:
        return FinalizeResponse(
            session_id=session_id,
            erp_encounter_id=session.erp_encounter_id,
            appointment_closed=False,
            payment_linked=False,
            status="already_finalized",
            message="This session was already submitted to ERPNext.",
        )

    result = await db.execute(select(CasesheetDraft).where(CasesheetDraft.session_id == session_id))
    draft_row = result.scalar_one_or_none()
    draft_data = draft_row.draft if draft_row else {}

    if not draft_data:
        raise HTTPException(status_code=400, detail="Draft is empty. Record at least one section before finalizing.")

    encounter_payload = _map_draft_to_encounter(
        patient_id=session.patient_id,
        doctor_id=session.doctor_id,
        appointment_id=session.appointment_id,
        draft=draft_data,
        lead_id=session.lead_id,
        )

    encounter = await erp_bridge_service.create_encounter(encounter_payload)

    if not encounter:
        session.status = SessionStatus.FAILED
        await db.commit()
        raise HTTPException(status_code=502, detail="Failed to create encounter in ERPNext. Draft preserved — retry /finalize.")

    encounter_id             = encounter.get("name")                                
    session.status           = SessionStatus.FINALIZED
    session.erp_encounter_id = encounter_id
    await db.commit()

    appointment_closed = False
    payment_linked     = False

    await event_logger.log(
        entity_type="casesheet",
        entity_id=session.id,
        event_type=EventType.ENCOUNTER_CREATED,
        payload={"encounter_id": encounter_id, "patient_id": session.patient_id, "doctor_id": session.doctor_id},
        triggered_by="api",
    )
    logger.info(f"Session {session_id} finalized → encounter {encounter_id}")

    return FinalizeResponse(
        session_id=session_id,
        erp_encounter_id=encounter_id,
        appointment_closed=appointment_closed,
        payment_linked=payment_linked,
        status="finalized",
        message=f"Casesheet submitted. Encounter ID: {encounter_id}",
    )


# ── Background tasks ──────────────────────────────────────────────────────────

async def _process_audio_background(session_id: str, section: str, audio_bytes: bytes, language: Optional[str]) -> None:
    from app.config.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            whisper_prompt = WHISPER_INITIAL_PROMPTS.get(section)
            transcript = await transcribe_audio(audio_bytes, language=language, initial_prompt=whisper_prompt)
            logger.info(f"Background: transcript len={len(transcript)} section={section}")
            section_data = await llm_service.extract_section(section, transcript)
            result = await db.execute(select(CasesheetDraft).where(CasesheetDraft.session_id == session_id))
            draft_row = result.scalar_one_or_none()
            if draft_row:
                current = dict(draft_row.draft or {})
                current[section] = section_data
                if "_raw_transcripts" not in current:
                    current["_raw_transcripts"] = {}
                current["_raw_transcripts"][section] = transcript
                draft_row.draft = current
                await db.commit()
        except Exception as e:
            logger.error(f"Background failed: session={session_id} section={section} error={e}")
            try:
                result = await db.execute(select(CasesheetDraft).where(CasesheetDraft.session_id == session_id))
                draft_row = result.scalar_one_or_none()
                if draft_row:
                    current = dict(draft_row.draft or {})
                    current[section] = {"_error": str(e), "_raw": ""}
                    draft_row.draft = current
                    await db.commit()
            except Exception:
                pass


async def _reprocess_transcript_background(session_id: str, section: str, transcript: str) -> None:
    from app.config.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            section_data = await llm_service.extract_section(section, transcript)
            result = await db.execute(select(CasesheetDraft).where(CasesheetDraft.session_id == session_id))
            draft_row = result.scalar_one_or_none()
            if draft_row:
                current = dict(draft_row.draft or {})
                current[section] = section_data
                if "_raw_transcripts" not in current:
                    current["_raw_transcripts"] = {}
                current["_raw_transcripts"][section] = transcript
                draft_row.draft = current
                await db.commit()
        except Exception as e:
            logger.error(f"Retranscribe failed: session={session_id} section={section} error={e}")


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_session(db: AsyncSession, session_id: str) -> CasesheetSession:
    result = await db.execute(select(CasesheetSession).where(CasesheetSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session


def _today() -> str:
    return date.today().isoformat()


def _join(lst, sep=", ") -> str:
    if not lst:
        return ""
    return sep.join(str(x) for x in lst if x)


def _map_draft_to_encounter(
    patient_id: str,
    doctor_id: str,
    appointment_id: Optional[str],
    draft: Dict[str, Any],
    lead_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Map structured casesheet draft → SGP Encounter DocType fields.
    All field names match the SGP Encounter doctype exactly (no custom_ prefix needed
    since SGP Encounter is our own doctype in sgp_clinical_core).
    Full JSON preserved in notes for audit.
    """
    import json as _json

    chief = draft.get("chief_complaint") or {}
    anamn = draft.get("anamnesis") or {}
    vpk   = draft.get("overall_vpk") or {}
    pulse = draft.get("pulse_diagnosis") or []
    ayur  = draft.get("ayurvedic_supplements") or []
    panca = draft.get("panchakarma") or {}
    treat = draft.get("treatment_and_background") or {}
    pers  = draft.get("personal_history") or {}
    pmh   = draft.get("past_medical_history") or {}
    ap    = draft.get("assessment_and_plan") or {}
    ros   = draft.get("review_of_systems") or {}
    sysex = draft.get("systemic_examination") or {}

    plan = ap.get("plan") or {} if isinstance(ap, dict) else {}
    diet = plan.get("diet_advice") or {} if isinstance(plan, dict) else {}
    fup  = plan.get("follow_up") or {} if isinstance(plan, dict) else {}

    # Chief complaint
    cc_summary = chief.get("summary") or ""
    if chief.get("ayurvedic_name"):
        cc_summary += f" ({chief['ayurvedic_name']})"
    if chief.get("duration"):
        cc_summary += f" — {chief['duration']}"

    # Anamnesis
    anamn_text = anamn.get("summary") or anamn.get("history") or (_join(list(anamn.values())) if isinstance(anamn, dict) else str(anamn))

    # Allopathic medications
    allop_meds = _join(
        [" ".join(filter(None, [m.get("name"), m.get("dose"), m.get("frequency"), m.get("route")]))
         for m in (treat.get("current_medications") or []) if isinstance(m, dict)],
        sep="\n",
    )

    # SGP Rx
    sgp_rx = _join(
        [f"{s.get('name','')} M:{s.get('dose_morning','-')} A:{s.get('dose_afternoon','-')} E:{s.get('dose_evening','-')} N:{s.get('dose_night','-')}"
         for s in (ayur if isinstance(ayur, list) else []) if isinstance(s, dict)],
        sep="\n",
    )

    # Panchakarma
    pk_sessions = _join(
        [" ".join(filter(None, [
            s.get("procedure"),
            "+" + s.get("companion_procedure", "") if s.get("companion_procedure") else None,
            f"x{s.get('session_count')}" if s.get("session_count") else None,
            "with " + _join(s.get("oils_or_ingredients") or []) if s.get("oils_or_ingredients") else None,
            f"{s.get('temperature_celsius')}°C" if s.get("temperature_celsius") else None,
        ]))
         for s in (panca.get("sessions") or []) if isinstance(s, dict)],
        sep="\n",
    )

    # Pulse summary
    pulse_lines = []
    for p in (pulse if isinstance(pulse, list) else []):
        if not isinstance(p, dict):
            continue
        parts = [p.get("system", "")]
        if p.get("vata"):  parts.append(f"V={p['vata']}")
        if p.get("pitta"): parts.append(f"P={p['pitta']}")
        if p.get("kapha"): parts.append(f"K={p['kapha']}")
        pulse_lines.append(" ".join(parts))
    pulse_summary = "  |  ".join(pulse_lines)

    # Follow-up
    fup_parts = []
    if fup.get("daily"):   fup_parts.append(f"Daily: {fup['daily']}")
    if fup.get("weekly"):  fup_parts.append(f"Weekly: {fup['weekly']}")
    if fup.get("monthly"): fup_parts.append(f"Monthly: {fup['monthly']}")
    fup_str = "  |  ".join(fup_parts)

    # Review of systems
    ros_text = ros.get("summary") or (_join([f"{k}: {v}" for k, v in ros.items()], sep="\n") if isinstance(ros, dict) else str(ros))

    # Systemic examination
    sysex_text = sysex.get("summary") or (_join([f"{k}: {v}" for k, v in sysex.items()], sep="\n") if isinstance(sysex, dict) else str(sysex))

    return {
        # ── Core fields ────────────────────────────────────────────────────
        "patient":                patient_id,
        "doctor":                 doctor_id,
        "appointment":            appointment_id,
        "lead":                   lead_id,
        "encounter_date":         _today(),
        "status":                 "Draft",
        "case_type":              "Ayurvedic",
        # ── Clinical ──────────────────────────────────────────────────────
        "chief_complaint":        cc_summary,
        "anamnesis":              anamn_text,
        "vpk_dominance":          vpk.get("dominance") or "",
        "pulse_diagnosis":        pulse_summary,
        "ayurvedic_diagnosis":    ap.get("ayurvedic_diagnosis") or "",
        "allopathic_diagnosis":   _join(ap.get("allopathic_diagnosis") or []),
        "review_of_systems":      ros_text,
        "systemic_examination":   sysex_text,
        # ── Treatment ─────────────────────────────────────────────────────
        "sgp_rx":                 sgp_rx,
        "allopathic_medicines":   allop_meds,
        "panchakarma":            pk_sessions,
        "home_remedies":          _join(plan.get("home_remedies") or []),
        # ── Diet & Lifestyle ──────────────────────────────────────────────
        "diet_include":           _join(diet.get("include") or []),
        "diet_exclude":           _join(diet.get("exclude") or []),
        "lifestyle_advice":       _join(plan.get("lifestyle_advice") or []),
        "investigations_advised": _join(plan.get("investigations") or []),
        # ── History ───────────────────────────────────────────────────────
        "personal_history_diet":  str(pers.get("diet") or ""),
        "personal_history_sleep": str(pers.get("sleep_hours") or ""),
        "past_medical_history":   _join(pmh.get("medical") or []),
        "family_history":         _join(pmh.get("family_history") or []),
        "allergies":              _join(treat.get("allergies") or pmh.get("allergies") or []),
        # ── Follow up ─────────────────────────────────────────────────────
        "follow_up":              fup_str,
        "prognosis":              ap.get("prognosis") or "",
        # ── Raw JSON backup ───────────────────────────────────────────────
        "notes":                  _json.dumps(draft, ensure_ascii=False, indent=2),
    }