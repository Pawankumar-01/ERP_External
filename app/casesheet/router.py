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
    from app.casesheet.protocols import enrich_section_data
    for k, v in req.updates.items():
        if "." in k:
            parts = k.split(".")
            target = current
            for p in parts[:-1]:
                if isinstance(target, dict):
                    if p not in target or not isinstance(target.get(p), (dict, list)):
                        target[p] = {}
                    target = target[p]
                elif isinstance(target, list) and p.isdigit():
                    idx = int(p)
                    while len(target) <= idx:
                        target.append({})
                    target = target[idx]
                else:
                    break
            last_part = parts[-1]
            if isinstance(target, dict):
                target[last_part] = v
            elif isinstance(target, list) and last_part.isdigit():
                idx = int(last_part)
                while len(target) <= idx:
                    target.append(None)
                target[idx] = v
            top_sec = parts[0]
            if top_sec in current and isinstance(current[top_sec], dict):
                current[top_sec] = enrich_section_data(top_sec, current[top_sec])
        else:
            current[k] = enrich_section_data(k, v) if isinstance(v, dict) else v
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

def _synthesize_prescription_sheet(draft: Dict[str, Any]) -> Dict[str, Any]:
    """
    Synthesize prescription_sheet (quick_summary, daily_regimen, diet_plan_weeks, review_after)
    from existing consultation sections if not explicitly provided or to enrich partial entries.
    """
    existing_rx = draft.get("prescription_sheet") if isinstance(draft.get("prescription_sheet"), dict) else {}
    existing_qs = existing_rx.get("quick_summary") if isinstance(existing_rx.get("quick_summary"), dict) else {}
    existing_dreg = existing_rx.get("daily_regimen") if isinstance(existing_rx.get("daily_regimen"), dict) else {}
    existing_dietwk = existing_rx.get("diet_plan_weeks") if isinstance(existing_rx.get("diet_plan_weeks"), list) else []

    treat = draft.get("treatment_and_background") or {}
    medhist = draft.get("medication_history") or {}
    panca = draft.get("panchakarma") or {}
    inv = draft.get("investigation_reports") or {}
    ap = draft.get("assessment_and_plan") or {}
    plan = ap.get("plan") if isinstance(ap, dict) else {}
    plan = plan or {}
    detox = draft.get("detox_procedures") or {}
    ex = draft.get("exercises_yoga") or {}
    fup = draft.get("followup_details") or {}
    plan_fup = plan.get("follow_up") if isinstance(plan, dict) else {}
    plan_fup = plan_fup or {}

    # Quick summary synthesis
    allopathic_meds = existing_qs.get("allopathy_medicines")
    if not allopathic_meds:
        allopathic_from_treat = _format_medicines_from_current(treat.get("current_medications") if isinstance(treat, dict) else [])
        allopathic_from_medhist = _format_medicines_from_current(medhist.get("current_medicines") if isinstance(medhist, dict) else [], system_filter="allopathic")
        allopathic_meds = "\n".join(x for x in [allopathic_from_treat, allopathic_from_medhist] if x) or None

    pk_summary = existing_qs.get("panchakarma")
    if not pk_summary and isinstance(panca, dict):
        sessions = panca.get("sessions") or []
        if sessions:
            pk_summary = "\n".join([f"• {s.get('procedure') or ''} ({s.get('session_count') or ''} sessions)" for s in sessions if isinstance(s, dict)])
        else:
            pk_summary = _clean(panca.get("overall_remarks") or "") or None

    tests_summary = existing_qs.get("tests_to_be_done")
    if not tests_summary:
        inv_list = list(plan.get("investigations") or [])
        if isinstance(inv, dict) and inv.get("investigations_advised"):
            inv_list.extend(_as_list(inv.get("investigations_advised")))
        tests_summary = ", ".join([_clean(x) for x in inv_list if _clean(x)]) or None

    others_summary = existing_qs.get("others")
    if not others_summary and isinstance(fup, dict):
        others_summary = _clean(fup.get("followup_instructions") or "") or None

    # Daily regimen synthesis
    oils_list = []
    detox_list = []
    if isinstance(detox, dict) and isinstance(detox.get("detox_items"), list):
        for d in detox.get("detox_items", []):
            if isinstance(d, dict):
                name = _clean(d.get("name"))
                if not name:
                    continue
                q = _clean(d.get("quantity")) or ""
                f = _clean(d.get("frequency")) or ""
                q_f_parts = []
                for w in (f"{q} {f}").split():
                    if not q_f_parts or w.lower() != q_f_parts[-1].lower():
                        q_f_parts.append(w)
                q_f_str = " ".join(q_f_parts).strip()
                instr = _clean(d.get("instructions") or d.get("remarks")) or ""

                header = f"• {name} ({q_f_str})" if q_f_str and q_f_str != "()" else f"• {name}"
                if any(k in name.lower() for k in ["oil", "thailam", "tailam", "abhyanga", "anutail"]):
                    oils_list.append(f"{header}: {instr}" if instr else (f"{header}: As prescribed" if "anutail" not in name.lower() else header))
                else:
                    detox_list.append(f"{header}: {instr}" if instr else header)

    oil_app = existing_dreg.get("oil_applications") or ("\n".join(oils_list) if oils_list else None)
    detox_proc = existing_dreg.get("detox_procedures") or ("\n".join(detox_list) if detox_list else None)

    home_rem = existing_dreg.get("home_remedies")
    if not home_rem:
        hr_list = plan.get("home_remedies") or []
        if hr_list:
            home_rem = "\n".join([f"• {_clean(h)}" for h in _as_list(hr_list) if _clean(h)]) or None

    breathing_ex = existing_dreg.get("breathing_exercises")
    if not breathing_ex and isinstance(ex, dict) and isinstance(ex.get("exercises"), list):
        b_list = []
        for e in ex.get("exercises", []):
            if isinstance(e, dict) and (e.get("category") == "breathing" or any(k in (_clean(e.get("name")) or "").lower() for k in ["pranayama", "breathing", "anulom", "kapalabhati", "nostril"])):
                b_list.append(f"• {e.get('name')}: {e.get('frequency') or ''} {e.get('duration_minutes') or ''} mins")
        if b_list:
            breathing_ex = "\n".join(b_list)

    review_after = existing_rx.get("review_after")
    if not review_after:
        if isinstance(fup, dict):
            review_after = _clean(fup.get("next_visit_duration") or fup.get("next_visit_date"))
        if not review_after and isinstance(plan_fup, dict):
            review_after = _clean(plan_fup.get("next_visit"))

    return {
        "quick_summary": {
            "allopathy_medicines": allopathic_meds,
            "panchakarma": pk_summary,
            "tests_to_be_done": tests_summary,
            "others": others_summary,
        },
        "daily_regimen": {
            "oil_applications": oil_app,
            "detox_procedures": detox_proc,
            "home_remedies": home_rem,
            "breathing_exercises": breathing_ex,
        },
        "diet_plan_weeks": existing_dietwk,
        "review_after": review_after,
        "needs_doctor_confirmation": existing_rx.get("needs_doctor_confirmation", []),
    }


def _normalize_supplements_weeks(supplements: Any) -> list:
    """
    Ensure each Ayurvedic supplement object has quantity_mg and an 8-week dosage array
    for seamless rendering in the ERPNext Jinja print template's 8-week grid.
    """
    if not isinstance(supplements, list):
        return []
    norm = []
    for item in supplements:
        if not isinstance(item, dict):
            continue
        new_item = dict(item)
        if not new_item.get("quantity_mg"):
            new_item["quantity_mg"] = str(new_item.get("quantity") or new_item.get("strength") or "").strip()
        weeks = new_item.get("weeks")
        if not weeks or not isinstance(weeks, list):
            dose_val = _clean(new_item.get("dose") or new_item.get("dose_morning") or "")
            if "->" in dose_val or "-" in dose_val:
                parts = [p.strip() for p in dose_val.replace("->", "-").split("-") if p.strip()]
                if len(parts) >= 3:
                    new_item["weeks"] = [parts[0], parts[0], parts[1], parts[1], parts[2], parts[2], parts[-1], parts[-1]]
                elif len(parts) == 2:
                    new_item["weeks"] = [parts[0], parts[0], parts[0], parts[0], parts[1], parts[1], parts[1], parts[1]]
                else:
                    new_item["weeks"] = [parts[0] if parts else "1"] * 8
            else:
                base_dose = dose_val if (dose_val and any(c.isdigit() for c in dose_val)) else "1"
                new_item["weeks"] = [base_dose] * 8
        else:
            existing = list(weeks)
            last = existing[-1] if existing else "1"
            while len(existing) < 8:
                existing.append(last)
            existing = existing[:8]
            if any(isinstance(v, str) and ("->" in v or ("-" in v and not v.startswith("-") and not v.endswith("-"))) for v in existing):
                cleaned_weeks = []
                i = 0
                while i < len(existing):
                    val = str(existing[i]).strip()
                    if "->" in val or ("-" in val and len(val.split("-")) >= 2):
                        sep = "->" if "->" in val else "-"
                        parts = [p.strip() for p in val.split(sep) if p.strip()]
                        j = i
                        while j < len(existing) and str(existing[j]).strip() == val:
                            j += 1
                        span = j - i
                        if span > 1 and len(parts) == 2:
                            half = (span + 1) // 2
                            for k in range(span):
                                cleaned_weeks.append(parts[0] if k < half else parts[1])
                        elif span > 1 and len(parts) > 2:
                            for k in range(span):
                                idx = min(k * len(parts) // span, len(parts) - 1)
                                cleaned_weeks.append(parts[idx])
                        else:
                            cleaned_weeks.append(parts[-1] if parts else "1")
                        i = j
                    else:
                        cleaned_weeks.append(val)
                        i += 1
                new_item["weeks"] = cleaned_weeks
            else:
                new_item["weeks"] = existing
        norm.append(new_item)
    return norm


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
    from app.casesheet.protocols import enrich_section_data

    # Ensure protocols and procedures are enriched before mapping to ERP fields
    for sec_key in ["detox_procedures", "panchakarma", "exercises_yoga", "assessment_and_plan"]:
        if isinstance(draft.get(sec_key), dict):
            draft[sec_key] = enrich_section_data(sec_key, draft[sec_key])

    final = draft.get("_final_case_sheet") or draft.get("final_case_sheet") or {}
    erp = final.get("erp_field_summaries") if isinstance(final, dict) else {}
    erp = erp or {}

    pat_identity = draft.get("patient_identity") or {}
    pat_name = _clean(erp.get("patient_name")) or ( _clean(pat_identity.get("patient_name")) if isinstance(pat_identity, dict) else None )
    pat_gender = _clean(erp.get("gender")) or ( _clean(pat_identity.get("gender")) if isinstance(pat_identity, dict) else None )
    pat_mobile = _clean(erp.get("mobile")) or ( _clean(pat_identity.get("mobile")) if isinstance(pat_identity, dict) else None )
    pat_age = _clean(erp.get("age"))
    if not pat_age and isinstance(pat_identity, dict) and pat_identity.get("age"):
        raw_age = str(pat_identity.get("age")).strip()
        unit = str(pat_identity.get("age_unit") or "").strip()
        if unit and unit.lower() not in raw_age.lower():
            pat_age = f"{raw_age} {unit}"
        else:
            pat_age = raw_age

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
    obgyn = draft.get("menstrual_obstetric_history") or {}
    allergy = draft.get("allergy_history") or {}
    family = draft.get("family_history_detailed") or {}
    genex = draft.get("general_examination") or {}
    local = draft.get("local_examination") or {}
    inv = draft.get("investigation_reports") or {}
    ap = draft.get("assessment_and_plan") or {}
    ros = draft.get("review_of_systems") or {}
    sysex = draft.get("systemic_examination") or {}
    encounter_ctx = draft.get("encounter_context") or {}
    detox = draft.get("detox_procedures") or {}
    ex_yoga = draft.get("exercises_yoga") or {}
    fup_details = draft.get("followup_details") or {}

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

    surg_text = _clean(erp.get("surgical_history"))
    if not surg_text and isinstance(surg, dict):
        surg_items = []
        for s in _as_list(surg.get("surgeries") or []):
            if isinstance(s, dict):
                parts = [p for p in [_clean(s.get("procedure")), _clean(s.get("year_or_date")), _clean(s.get("indication")), _clean(s.get("hospital"))] if p]
                surg_items.append(" — ".join(parts))
            elif s:
                surg_items.append(_clean(s))
        surg_items.extend([_clean(h) for h in _as_list(surg.get("hospitalizations") or []) if _clean(h)])
        surg_text = "\n".join(surg_items)
    elif not surg_text and surg:
        surg_text = _clean(surg)

    medhist_text = _clean(erp.get("medication_history"))
    if not medhist_text and isinstance(medhist, dict):
        meds_list = _format_medicines_from_current(medhist.get("current_medicines") or medhist.get("medicines") or [])
        medhist_text = meds_list or _clean(medhist.get("summary") or "")
    elif not medhist_text and medhist:
        medhist_text = _clean(medhist)

    obgyn_text = _clean(erp.get("menstrual_obstetric_history"))
    if not obgyn_text and isinstance(obgyn, dict):
        if not obgyn.get("not_applicable_or_not_mentioned"):
            ob_parts = []
            for k in ["lmp", "cycle_regularity", "cycle_length", "flow", "dysmenorrhea", "pregnancy_status", "menopause_status"]:
                val = _clean(obgyn.get(k))
                if val and val.lower() != "unknown":
                    ob_parts.append(f"{k.replace('_', ' ').title()}: {val}")
            if obgyn.get("obstetric_history"):
                ob_parts.append(f"Obstetric History: {_clean(obgyn.get('obstetric_history'))}")
            if ob_parts:
                obgyn_text = "\n".join(ob_parts)
    elif not obgyn_text and obgyn:
        obgyn_text = _clean(obgyn)

    allopathic_from_treat = _format_medicines_from_current(treat.get("current_medications") if isinstance(treat, dict) else [])
    allopathic_from_plan = _join(plan.get("medications") if isinstance(plan, dict) else [], sep="\n")
    allopathic_medicines = "\n".join(x for x in [allopathic_from_treat, allopathic_from_plan] if x)

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

    gen_text = _clean(erp.get("general_examination"))
    if not gen_text and isinstance(genex, dict) and _clean(genex):
        gen_text = _format_exam_dict(genex)
    elif not gen_text and genex:
        gen_text = _clean(genex)

    sysex_text = _clean(sysex.get("summary")) if isinstance(sysex, dict) else _clean(sysex)
    if not sysex_text and isinstance(sysex, dict):
        sysex_text = _format_exam_dict(sysex)
    if isinstance(local, dict) and _clean(local):
        loc_str = _format_exam_dict(local, "Local Exam")
        if loc_str:
            sysex_text = (sysex_text + "\n\n" if sysex_text else "") + loc_str

    inv_reports_text = _clean(erp.get("investigation_reports"))
    if not inv_reports_text and isinstance(inv, dict):
        inv_items = []
        for lab in _as_list(inv.get("lab_results") or []):
            if isinstance(lab, dict):
                inv_items.append(f"Lab - {lab.get('test_name')}: {lab.get('value')} {lab.get('unit') or ''} ({lab.get('date') or ''})".strip())
        for img in _as_list(inv.get("imaging_reports") or []):
            if isinstance(img, dict):
                inv_items.append(f"Imaging - {img.get('modality') or 'Report'} ({img.get('body_region') or ''}): {img.get('impression') or _join(img.get('findings'))}".strip())
        inv_reports_text = "\n".join(inv_items)
    elif not inv_reports_text and inv:
        inv_reports_text = _clean(inv)

    detox_text = _clean(erp.get("detox_procedures"))
    if not detox_text and isinstance(detox, dict):
        d_items = []
        for d in _as_list(detox.get("detox_items") or []):
            if isinstance(d, dict):
                parts = [p for p in [_clean(d.get("name")), _clean(d.get("quantity")), _clean(d.get("frequency")), _clean(d.get("timing")), _clean(d.get("instructions"))] if p]
                d_items.append(" — ".join(parts))
        detox_text = "\n".join(d_items)
    elif not detox_text and detox:
        detox_text = _clean(detox)

    exercises_text = _clean(erp.get("exercises_yoga"))
    if not exercises_text and isinstance(ex_yoga, dict):
        e_items = []
        for e in _as_list(ex_yoga.get("exercises") or []):
            if isinstance(e, dict):
                parts = [p for p in [_clean(e.get("name")), _clean(e.get("frequency")), f"{e.get('duration_minutes')} mins" if e.get("duration_minutes") else "", _clean(e.get("remarks"))] if p]
                e_items.append(" — ".join(parts))
        exercises_text = "\n".join(e_items)
    elif not exercises_text and ex_yoga:
        exercises_text = _clean(ex_yoga)

    fup_text = _join([v for v in [fup.get("daily"), fup.get("weekly"), fup.get("monthly"), fup.get("next_visit")] if v], sep=" | ") if isinstance(fup, dict) else ""
    if not fup_text and isinstance(fup_details, dict):
        fup_parts = [
            f"Next Visit: {_clean(fup_details.get('next_visit_duration') or fup_details.get('next_visit_date'))}" if (fup_details.get("next_visit_duration") or fup_details.get("next_visit_date")) else "",
            _clean(fup_details.get("followup_instructions"))
        ]
        fup_text = " | ".join(p for p in fup_parts if p)

    fup_doc_text = _clean(erp.get("followup_doc"))
    if not fup_doc_text and isinstance(fup_details, dict):
        doc_name = _clean(fup_details.get("followup_doc_name") or fup_details.get("assigned_doc"))
        doc_contact = _clean(fup_details.get("followup_doc_contact"))
        fup_doc_text = f"{doc_name} ({doc_contact})" if (doc_name and doc_contact) else (doc_name or doc_contact)

    investigations_text = _join(plan.get("investigations") if isinstance(plan, dict) else [])
    if isinstance(inv, dict):
        inv_advised = _join(inv.get("investigations_advised"))
        if inv_advised:
            investigations_text = (investigations_text + ", " if investigations_text else "") + inv_advised

    synth_rx = _synthesize_prescription_sheet(draft)
    norm_supp = _normalize_supplements_weeks(draft.get("ayurvedic_supplements"))

    rx_quick_str = _clean(erp.get("rx_quick_summary"))
    if not rx_quick_str and isinstance(synth_rx.get("quick_summary"), dict):
        qs_parts = []
        for k, label in [("allopathy_medicines", "Medicines"), ("panchakarma", "Panchakarma"), ("tests_to_be_done", "Tests Advised"), ("others", "Other Instructions")]:
            val = _clean(synth_rx["quick_summary"].get(k))
            if val:
                qs_parts.append(f"[{label}]\n{val}")
        rx_quick_str = "\n\n".join(qs_parts)

    rx_regimen_str = _clean(erp.get("rx_daily_regimen"))
    if not rx_regimen_str and isinstance(synth_rx.get("daily_regimen"), dict):
        dr_parts = []
        for k, label in [("oil_applications", "Oil Applications"), ("detox_procedures", "Detox Procedures"), ("home_remedies", "Home Remedies"), ("breathing_exercises", "Breathing & Pranayama")]:
            val = _clean(synth_rx["daily_regimen"].get(k))
            if val:
                dr_parts.append(f"[{label}]\n{val}")
        rx_regimen_str = "\n\n".join(dr_parts)

    full_notes_payload = {
        **draft,
        "ayurvedic_supplements": norm_supp,
        "prescription_sheet": synth_rx,
        "case_sheet_markdown": final.get("case_sheet_markdown") if isinstance(final, dict) else None,
        "case_sheet_summary": final.get("case_sheet_summary") if isinstance(final, dict) else None,
        "doctor_review_summary": draft.get("_doctor_review_summary"),
        "quality": draft.get("_quality"),
        "raw_draft": draft,
    }

    return {
        "patient": patient_id,
        "patient_name": pat_name,
        "age": pat_age,
        "gender": pat_gender,
        "mobile": pat_mobile,
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
        "general_examination": _clean(erp.get("general_examination")) or gen_text,
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

        "medication_history": _clean(erp.get("medication_history")) or medhist_text,
        "surgical_history": _clean(erp.get("surgical_history")) or surg_text,
        "menstrual_obstetric_history": _clean(erp.get("menstrual_obstetric_history")) or obgyn_text,
        "general_examination": _clean(erp.get("general_examination")) or gen_text,
        "investigation_reports": _clean(erp.get("investigation_reports")) or inv_reports_text,
        "detox_procedures": _clean(erp.get("detox_procedures")) or detox_text,
        "exercises_yoga": _clean(erp.get("exercises_yoga")) or exercises_text,
        "rx_quick_summary": rx_quick_str,
        "rx_daily_regimen": rx_regimen_str,
        "followup_doc": _clean(erp.get("followup_doc")) or fup_doc_text,
    }

