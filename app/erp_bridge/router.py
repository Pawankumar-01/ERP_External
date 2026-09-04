
import hashlib
import hmac
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.config.settings import settings
from app.erp_bridge.service import erp_bridge_service
from app.events.logger import EventType, event_logger

router  = APIRouter()
logger  = logging.getLogger(__name__)



class ERPWebhookPayload(BaseModel):
    doctype: str
    name:    str
    event:   Optional[str] = None
    doc:     Optional[Dict] = None



class AttendanceSyncRequest(BaseModel):
    lead_id:            str
    session_id:         str
    attendance_status:  str = "Present"
    watch_time_seconds: int = 0
    completion_pct:     float = 0.0


class AppointmentRequest(BaseModel):
    patient_id:       str
    practitioner:     str
    appointment_date: str
    appointment_time: Optional[str] = None
    department:       Optional[str] = None
    notes:            Optional[str] = None


class EncounterRequest(BaseModel):
    patient_id:          str
    practitioner:        str
    encounter_date:      str
    lead_id:             Optional[str] = None
    appointment_id:      Optional[str] = None
    orientation_verified: bool         = False
    consent_verified:    bool          = False
    notes:               Optional[str] = None



@router.post("/webhook")
async def receive_erp_webhook(
    request: Request,
    x_frappe_webhook_signature: Optional[str] = Header(None),
):
    body = await request.body()

    if x_frappe_webhook_signature:
        valid = erp_bridge_service.verify_erp_webhook(
            body, x_frappe_webhook_signature
        )
        if not valid:
            logger.warning("[ERP WEBHOOK] Invalid signature — rejecting")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    else:
        logger.warning("[ERP WEBHOOK] No signature header — processing anyway (dev mode)")

    try:
        payload = ERPWebhookPayload.model_validate_json(body)
    except Exception as e:
        logger.error(f"[ERP WEBHOOK] Failed to parse payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    logger.info(
        f"[ERP WEBHOOK] Received: doctype={payload.doctype} "
        f"name={payload.name} event={payload.event}"
    )

    await _dispatch_webhook(payload)

    return {"status": "received", "doctype": payload.doctype, "name": payload.name}


async def _dispatch_webhook(payload: ERPWebhookPayload) -> None:
    doc     = payload.doc or {}
    dtype   = payload.doctype
    event   = payload.event or "unknown"

    if dtype == "SGP Lead":
        await _handle_lead_webhook(payload.name, event, doc)

    elif dtype == "Patient Appointment":
        await _handle_appointment_webhook(payload.name, event, doc)

    elif dtype == "SGP Encounter":
        await _handle_encounter_webhook(payload.name, event, doc)

    else:
        await event_logger.log(
            entity_type="erp_webhook",
            entity_id=payload.name,
            event_type=EventType.ERP_CALL_SUCCESS,
            payload={"doctype": dtype, "event": event, "note": "unhandled doctype"},
            triggered_by="erp_webhook",
        )


async def _handle_lead_webhook(name: str, event: str, doc: Dict) -> None:
    new_status = doc.get("status", "UNKNOWN")
    await event_logger.log(
        entity_type="lead",
        entity_id=name,
        event_type=EventType.LEAD_STATUS_UPDATED,
        payload={
            "source":     "erp_webhook",
            "event":      event,
            "new_status": new_status,
            "lead_name":  doc.get("lead_name"),
        },
        triggered_by="erp_webhook",
    )
    logger.info(f"[ERP WEBHOOK] Lead {name} updated via ERPNext: status={new_status}")


async def _handle_appointment_webhook(name: str, event: str, doc: Dict) -> None:
    await event_logger.log(
        entity_type="appointment",
        entity_id=name,
        event_type=EventType.APPOINTMENT_CREATED,
        payload={
            "source":       "erp_webhook",
            "event":        event,
            "patient":      doc.get("patient"),
            "practitioner": doc.get("practitioner"),
            "date":         doc.get("appointment_date"),
        },
        triggered_by="erp_webhook",
    )
    logger.info(f"[ERP WEBHOOK] Appointment {name} event={event}")


async def _handle_encounter_webhook(name: str, event: str, doc: Dict) -> None:
    await event_logger.log(
        entity_type="encounter",
        entity_id=name,
        event_type=EventType.ENCOUNTER_CREATED,
        payload={
            "source":   "erp_webhook",
            "event":    event,
            "status":   doc.get("status"),
            "patient":  doc.get("patient"),
        },
        triggered_by="erp_webhook",
    )
    logger.info(f"[ERP WEBHOOK] Encounter {name} status={doc.get('status')}")



@router.post("/sync-attendance")
async def sync_attendance(req: AttendanceSyncRequest):
    success = await erp_bridge_service.create_orientation_attendance(
        lead_id=req.lead_id,
        session_id=req.session_id,
        attendance_status=req.attendance_status,
        watch_time=req.watch_time_seconds,
    )
    if not success:
        raise HTTPException(status_code=502, detail="ERP sync failed — check server logs")
    return {"status": "synced", "lead_id": req.lead_id, "session_id": req.session_id}


@router.get("/lead/{lead_id}")
async def get_erp_lead(lead_id: str):
    lead = await erp_bridge_service.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found in ERPNext")
    return lead


@router.get("/patient/{erp_patient_id}")
async def get_erp_patient(erp_patient_id: str):
    patient = await erp_bridge_service.get_patient(erp_patient_id)
    if not patient:
        raise HTTPException(
            status_code=404, detail=f"Patient {erp_patient_id} not found"
        )
    return patient


@router.post("/appointment")
async def create_appointment(req: AppointmentRequest):
    result = await erp_bridge_service.create_patient_appointment({
        "patient":           req.patient_id,
        "practitioner":      req.practitioner,
        "appointment_date":  req.appointment_date,
        "appointment_time":  req.appointment_time,
        "department":        req.department,
        "notes":             req.notes,
    })
    if not result:
        raise HTTPException(status_code=502, detail="Failed to create appointment in ERPNext")
    return result


@router.post("/encounter")
async def create_encounter(req: EncounterRequest):
    result = await erp_bridge_service.create_encounter({
        "patient":               req.patient_id,
        "practitioner":          req.practitioner,
        "encounter_date":        req.encounter_date,
        "lead":                  req.lead_id,
        "appointment":           req.appointment_id,
        "orientation_verified":  req.orientation_verified,
        "consent_verified":      req.consent_verified,
        "notes":                 req.notes,
        "status":                "Draft",
    })
    if not result:
        raise HTTPException(status_code=502, detail="Failed to create encounter in ERPNext")
    return result


@router.get("/encounter/{encounter_id}")
async def get_encounter(encounter_id: str):
    enc = await erp_bridge_service.get_encounter(encounter_id)
    if not enc:
        raise HTTPException(status_code=404, detail=f"Encounter {encounter_id} not found")
    return enc


@router.patch("/encounter/{encounter_id}/status")
async def update_encounter_status(encounter_id: str, status: str):
    valid_statuses = {"Draft", "Under Review", "Approved", "Closed"}
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {valid_statuses}",
        )
    result = await erp_bridge_service.update_encounter_status(encounter_id, status)
    if not result:
        raise HTTPException(status_code=502, detail="Failed to update encounter status")
    return result


@router.get("/status")
async def bridge_status():
    return {
        "configured": erp_bridge_service.is_configured,
        "mode":       "live" if erp_bridge_service.is_configured else "placeholder",
        "base_url":   (
            settings.ERPNEXT_BASE_URL
            if erp_bridge_service.is_configured
            else "not configured"
        ),
    }
