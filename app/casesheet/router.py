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
from datetime import date, datetime
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



def _today() -> str:
    return date.today().isoformat()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = []
        for key, val in value.items():
            if val in (None, "", [], {}):
                continue
            parts.append(f"{key}: {_clean(val)}")
        return "; ".join(parts)
    if isinstance(value, list):
        return ", ".join(_clean(item) for item in value if _clean(item))
    return str(value)


def _join(value: Any, sep: str = ", ") -> str:
    return sep.join(_clean(item) for item in _as_list(value) if _clean(item))


def _join_lines(value: Any) -> str:
    return _join(value, sep="\n")


def _num_or_none(value: Any):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def _format_pulse(pulse: Any) -> str:
    if isinstance(pulse, dict):
        overall = pulse.get("overall_vpk") or {}
        systems = pulse.get("systems") or []
        parts = []
        if isinstance(overall, dict) and overall.get("dominance"):
            parts.append(f"Overall VPK Dominance: {_clean(overall.get('dominance'))}")
        sys_parts = []
        for p in _as_list(systems):
            if not isinstance(p, dict):
                continue
            sys_info = []
            if p.get("system"):
                sys_info.append(_clean(p.get("system")))
            if p.get("vata"):
                sys_info.append(f"V={p.get('vata')}")
            if p.get("pitta"):
                sys_info.append(f"P={p.get('pitta')}")
            if p.get("kapha"):
                sys_info.append(f"K={p.get('kapha')}")
            if sys_info:
                sys_parts.append(" ".join(sys_info))
        if sys_parts:
            parts.append("Systems: " + " | ".join(sys_parts))
        return " • ".join(parts)

    lines = []
    for p in _as_list(pulse):
        if not isinstance(p, dict):
            continue
        parts = []
        if p.get("system"):
            parts.append(_clean(p.get("system")))
        if p.get("vata"):
            parts.append(f"V={p.get('vata')}")
        if p.get("pitta"):
            parts.append(f"P={p.get('pitta')}")
        if p.get("kapha"):
            parts.append(f"K={p.get('kapha')}")
        if parts:
            lines.append(" ".join(parts))
    return " | ".join(lines)


def _format_medicines_from_current(items: Any, system_filter: Optional[str] = None) -> str:
    lines = []
    for m in _as_list(items):
        if not isinstance(m, dict):
            if m:
                lines.append(_clean(m))
            continue
        if system_filter:
            system = _clean(m.get("system")).lower()
            if system_filter.lower() not in system:
                continue
        name = _clean(m.get("name"))
        if not name:
            continue
        parts = [name]
        for key in ["dose", "frequency", "route", "duration", "timing", "remarks"]:
            if m.get(key):
                parts.append(_clean(m.get(key)))
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _format_sgp_rx(items: Any) -> str:
    lines = []
    for m in _as_list(items):
        if not isinstance(m, dict):
            if m:
                lines.append(_clean(m))
            continue
        name = _clean(m.get("name"))
        if not name:
            continue
        doses = []
        if m.get("dose_morning"):
            doses.append(f"M:{m.get('dose_morning')}")
        if m.get("dose_afternoon"):
            doses.append(f"A:{m.get('dose_afternoon')}")
        if m.get("dose_evening"):
            doses.append(f"E:{m.get('dose_evening')}")
        if m.get("dose_night"):
            doses.append(f"N:{m.get('dose_night')}")
        if m.get("dose"):
            doses.append(_clean(m.get("dose")))
        if m.get("frequency"):
            doses.append(_clean(m.get("frequency")))
        if m.get("remarks"):
            doses.append(_clean(m.get("remarks")))
        lines.append(" ".join([name] + doses))
    return "\n".join(lines)


def _format_panchakarma(panca: Any) -> str:
    if not isinstance(panca, dict):
        return _clean(panca)
    lines = []
    for s in _as_list(panca.get("sessions")):
        if not isinstance(s, dict):
            if s:
                lines.append(_clean(s))
            continue
        parts = []
        for key in ["procedure", "companion_procedure", "body_site", "laterality"]:
            if s.get(key):
                parts.append(_clean(s.get(key)))
        if s.get("session_count"):
            parts.append(f"x{s.get('session_count')}")
        if s.get("oils_or_ingredients"):
            parts.append("with " + _join(s.get("oils_or_ingredients")))
        if s.get("temperature_celsius"):
            parts.append(f"{s.get('temperature_celsius')} deg C")
        if s.get("remarks"):
            parts.append(_clean(s.get("remarks")))
        if parts:
            lines.append(" ".join(parts))
    if panca.get("overall_remarks"):
        lines.append(_clean(panca.get("overall_remarks")))
    return "\n".join(lines)


def _format_allergy_list(items: Any) -> list:
    results = []
    for item in _as_list(items):
        if not isinstance(item, dict):
            val = _clean(item)
            if val and val.lower() not in [r.lower() for r in results]:
                results.append(val)
            continue
        substance = _clean(item.get("substance") or item.get("allergen") or item.get("name"))
        if not substance:
            continue
        severity = _clean(item.get("severity"))
        status = _clean(item.get("status"))
        details = [x for x in [severity, status] if x and x.lower() != "unknown"]
        formatted = f"{substance} ({', '.join(details)})" if details else substance
        if not any(substance.lower() in ex.lower() for ex in results):
            results.append(formatted)
    return results


def _format_exam_dict(exam: Any, prefix: str = "") -> str:
    if not isinstance(exam, dict):
        val = _clean(exam)
        return f"{prefix}: {val}" if val and prefix else val
    parts = []
    for key, val in exam.items():
        if key == "needs_doctor_confirmation" or val in (None, "", [], {}, "unknown", "absent"):
            continue
        clean_key = key.replace("_", " ").capitalize()
        clean_val = _clean(val)
        if clean_val:
            parts.append(f"{clean_key}: {clean_val}")
    res = "; ".join(parts)
    return f"{prefix}: {res}" if res and prefix else res


def _map_draft_to_encounter(
    patient_id: str,
    doctor_id: str,
    appointment_id: Optional[str],
    draft: Dict[str, Any],
    lead_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Map expanded case-sheet draft to SGP Encounter DocType fields.

    Priority:
    1. final composer erp_field_summaries, if present.
    2. section-level structured JSON from expanded prompts.
    3. older draft fields from the original implementation.
    """
    import json as _json

    final = draft.get("_final_case_sheet") or draft.get("final_case_sheet") or {}
    erp = final.get("erp_field_summaries") if isinstance(final, dict) else {}
    erp = erp or {}

    chief = draft.get("chief_complaint") or {}
    anamn = draft.get("anamnesis") or {}
    symptoms = draft.get("symptom_analysis") or {}
    vitals = draft.get("vitals_anthropometry") or {}
    vpk = (draft.get("pulse_diagnosis") or {}).get("overall_vpk") if isinstance(draft.get("pulse_diagnosis"), dict) else (draft.get("overall_vpk") or {})
    vpk = vpk or {}
    ayu_ext = draft.get("ayurvedic_assessment_extended") or {}
    pulse = (draft.get("pulse_diagnosis") or {}).get("systems") if isinstance(draft.get("pulse_diagnosis"), dict) else (draft.get("pulse_diagnosis") or [])
    pulse = pulse or []
    ayur = draft.get("ayurvedic_supplements") or []
    panca = draft.get("panchakarma") or {}
    treat = draft.get("treatment_and_background") or {}
    medhist = draft.get("medication_history") or {}
    disease = draft.get("disease_history") or {}
    pers = draft.get("personal_history") or {}
    pmh = draft.get("past_medical_history") or {}
    surg = draft.get("surgical_history") or {}
    allergy = draft.get("allergy_history") or {}
    family = draft.get("family_history_detailed") or {}
    genex = draft.get("general_examination") or {}
    local = draft.get("local_examination") or {}
    inv = draft.get("investigation_reports") or {}
    ap = draft.get("assessment_and_plan") or {}
    ros = draft.get("review_of_systems") or {}
    sysex = draft.get("systemic_examination") or {}
    encounter_ctx = draft.get("encounter_context") or {}

    plan = ap.get("plan") if isinstance(ap, dict) else {}
    plan = plan or {}
    diet = plan.get("diet_advice") if isinstance(plan, dict) else {}
    diet = diet or {}
    fup = plan.get("follow_up") if isinstance(plan, dict) else {}
    fup = fup or {}

    chief_fallback = _clean(chief.get("summary")) if isinstance(chief, dict) else _clean(chief)
    if isinstance(chief, dict) and chief.get("complaints") and not chief_fallback:
        chief_fallback = _join([c.get("complaint") for c in chief.get("complaints") if isinstance(c, dict)])

    anamn_fallback = _clean(anamn.get("summary")) or _clean(anamn.get("relevant_context")) if isinstance(anamn, dict) else _clean(anamn)
    if isinstance(symptoms, dict) and symptoms.get("overall_symptom_summary"):
        anamn_fallback = (anamn_fallback + "\n" if anamn_fallback else "") + _clean(symptoms.get("overall_symptom_summary"))

    known_conditions = []
    for item in _as_list(disease.get("known_conditions") if isinstance(disease, dict) else []):
        if isinstance(item, dict):
            condition = _clean(item.get("condition"))
            duration = _clean(item.get("duration"))
            status = _clean(item.get("status"))
            known_conditions.append(" ".join(x for x in [condition, duration, status] if x))
        else:
            known_conditions.append(_clean(item))
    pmh_text = _join(known_conditions + _as_list(pmh.get("medical") if isinstance(pmh, dict) else []))
    if isinstance(surg, dict):
        surgery_text = _join(surg.get("surgeries") or surg.get("hospitalizations") or [])
        if surgery_text:
            pmh_text = (pmh_text + "; " if pmh_text else "") + "Surgical/Hospitalization: " + surgery_text

    allopathic_from_treat = _format_medicines_from_current(treat.get("current_medications") if isinstance(treat, dict) else [])
    allopathic_from_medhist = _format_medicines_from_current(medhist.get("current_medicines") if isinstance(medhist, dict) else [], system_filter="allopathic")
    allopathic_medicines = "\n".join(x for x in [allopathic_from_treat, allopathic_from_medhist] if x)

    sgp_rx = _format_sgp_rx(ayur)

    allergies = []
    if isinstance(allergy, dict):
        allergies.extend(_format_allergy_list(allergy.get("allergies")))
        if allergy.get("no_known_allergies") is True and not allergies:
            allergies.append("No known allergies")
    if isinstance(treat, dict):
        for a in _format_allergy_list(treat.get("allergies")):
            if not any(a.lower() in ex.lower() or ex.lower() in a.lower() for ex in allergies):
                allergies.append(a)
    if isinstance(pmh, dict):
        for a in _format_allergy_list(pmh.get("allergies")):
            if not any(a.lower() in ex.lower() or ex.lower() in a.lower() for ex in allergies):
                allergies.append(a)

    family_text = ""
    if isinstance(family, dict) and family.get("family_conditions"):
        family_text = _join(family.get("family_conditions"))
    elif isinstance(pmh, dict):
        family_text = _join(pmh.get("family_history"))

    ros_text = _clean(ros.get("summary")) if isinstance(ros, dict) else _clean(ros)
    if not ros_text and isinstance(ros, dict):
        ros_text = _join([f"{key}: {_clean(val)}" for key, val in ros.items() if val and key != "needs_doctor_confirmation"], sep="\n")

    sysex_text = _clean(sysex.get("summary")) if isinstance(sysex, dict) else _clean(sysex)
    if not sysex_text and isinstance(sysex, dict):
        sysex_text = _format_exam_dict(sysex)
    if isinstance(genex, dict) and _clean(genex):
        gen_str = _format_exam_dict(genex, "General")
        if gen_str:
            sysex_text = (sysex_text + "\n\n" if sysex_text else "") + gen_str
    if isinstance(local, dict) and _clean(local):
        loc_str = _format_exam_dict(local, "Local")
        if loc_str:
            sysex_text = (sysex_text + "\n\n" if sysex_text else "") + loc_str

    fup_text = _join([v for v in [fup.get("daily"), fup.get("weekly"), fup.get("monthly"), fup.get("next_visit")] if v], sep=" | ") if isinstance(fup, dict) else ""
    if not fup_text and isinstance(draft.get("followup_details"), dict):
        fup_details = draft.get("followup_details") or {}
        fup_parts = [
            f"Assigned Doc: {_clean(fup_details.get('assigned_doc'))}" if fup_details.get("assigned_doc") else "",
            f"Next Visit: {_clean(fup_details.get('next_visit_duration') or fup_details.get('next_visit_date'))}" if (fup_details.get('next_visit_duration') or fup_details.get('next_visit_date')) else "",
            _clean(fup_details.get("followup_instructions"))
        ]
        fup_text = " | ".join(p for p in fup_parts if p)

    investigations_text = _join(plan.get("investigations") if isinstance(plan, dict) else [])
    if isinstance(inv, dict):
        inv_advised = _join(inv.get("investigations_advised"))
        if inv_advised:
            investigations_text = (investigations_text + ", " if investigations_text else "") + inv_advised

    full_notes_payload = {
        **draft,
        "case_sheet_markdown": final.get("case_sheet_markdown") if isinstance(final, dict) else None,
        "case_sheet_summary": final.get("case_sheet_summary") if isinstance(final, dict) else None,
        "doctor_review_summary": draft.get("_doctor_review_summary"),
        "quality": draft.get("_quality"),
        "raw_draft": draft,
    }

    return {
        "patient": patient_id,
        "doctor": doctor_id,
        "appointment": appointment_id,
        "lead": lead_id,
        "encounter_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "Draft",
        "case_type": _clean(erp.get("case_type")) or "Integrated",
        "consent_verified": 1 if (isinstance(encounter_ctx, dict) and encounter_ctx.get("consent_verified")) else 0,

        "chief_complaint": _clean(erp.get("chief_complaint")) or chief_fallback,
        "anamnesis": _clean(erp.get("anamnesis")) or anamn_fallback,

        "height_cm": _num_or_none(erp.get("height_cm")) or _num_or_none(vitals.get("height_cm") if isinstance(vitals, dict) else None),
        "weight_kg": _num_or_none(erp.get("weight_kg")) or _num_or_none(vitals.get("weight_kg") if isinstance(vitals, dict) else None),
        "wrist_cm": _num_or_none(erp.get("wrist_cm")) or _num_or_none(vitals.get("wrist_cm") if isinstance(vitals, dict) else None),
        "waist_cm": _num_or_none(erp.get("waist_cm")) or _num_or_none(vitals.get("waist_cm") if isinstance(vitals, dict) else None),
        "fore_arm_cm": _num_or_none(erp.get("fore_arm_cm")) or _num_or_none(vitals.get("fore_arm_cm") if isinstance(vitals, dict) else None),
        "hip_cm": _num_or_none(erp.get("hip_cm")) or _num_or_none(vitals.get("hip_cm") if isinstance(vitals, dict) else None),
        "temp": _clean(erp.get("temp")) or _clean(vitals.get("temperature") if isinstance(vitals, dict) else None),
        "bp": _clean(erp.get("bp")) or _clean(vitals.get("bp") if isinstance(vitals, dict) else None),
        "pr": _clean(erp.get("pr")) or _clean(vitals.get("pulse_rate") if isinstance(vitals, dict) else None),
        "rr": _clean(erp.get("rr")) or _clean(vitals.get("respiratory_rate") if isinstance(vitals, dict) else None),

        "vpk_dominance": _clean(erp.get("vpk_dominance")) or _clean(vpk.get("dominance") if isinstance(vpk, dict) else None) or _clean(ayu_ext.get("vpk_dominance") if isinstance(ayu_ext, dict) else None),
        "pulse_diagnosis": _clean(erp.get("pulse_diagnosis")) or _format_pulse(pulse),
        "ayurvedic_diagnosis": _clean(erp.get("ayurvedic_diagnosis")) or _clean(ap.get("ayurvedic_diagnosis") if isinstance(ap, dict) else None) or _clean(ayu_ext.get("ayurvedic_diagnosis") if isinstance(ayu_ext, dict) else None),
        "allopathic_diagnosis": _clean(erp.get("allopathic_diagnosis")) or _join(ap.get("allopathic_diagnosis") if isinstance(ap, dict) else []),
        "review_of_systems": _clean(erp.get("review_of_systems")) or ros_text,
        "systemic_examination": _clean(erp.get("systemic_examination")) or sysex_text,

        "sgp_rx": _clean(erp.get("sgp_rx")) or sgp_rx,
        "allopathic_medicines": _clean(erp.get("allopathic_medicines")) or allopathic_medicines,
        "panchakarma": _clean(erp.get("panchakarma")) or _format_panchakarma(panca),
        "home_remedies": _clean(erp.get("home_remedies")) or _join(plan.get("home_remedies") if isinstance(plan, dict) else []),

        "diet_include": _clean(erp.get("diet_include")) or _join(diet.get("include") if isinstance(diet, dict) else []),
        "diet_exclude": _clean(erp.get("diet_exclude")) or _join(diet.get("exclude") if isinstance(diet, dict) else []),
        "lifestyle_advice": _clean(erp.get("lifestyle_advice")) or _join(plan.get("lifestyle_advice") if isinstance(plan, dict) else []),
        "investigations_advised": _clean(erp.get("investigations_advised")) or investigations_text,

        "personal_history_diet": _clean(erp.get("personal_history_diet")) or _clean(pers.get("diet") if isinstance(pers, dict) else None),
        "personal_history_sleep": _clean(erp.get("personal_history_sleep")) or _clean(pers.get("sleep_hours") if isinstance(pers, dict) else None) or _clean(pers.get("sleep_quality") if isinstance(pers, dict) else None),
        "past_medical_history": _clean(erp.get("past_medical_history")) or pmh_text,
        "family_history": _clean(erp.get("family_history")) or family_text,
        "allergies": _clean(erp.get("allergies")) or _join(allergies),

        "follow_up": _clean(erp.get("follow_up")) or fup_text,
        "prognosis": _clean(erp.get("prognosis")) or _clean(ap.get("prognosis") if isinstance(ap, dict) else None),
        "notes": _clean(erp.get("notes")) or _json.dumps(full_notes_payload, ensure_ascii=False, indent=2, default=str),
    }

