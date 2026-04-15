"""
Appointment Module
─────────────────
Handles the full lead → patient → appointment flow.

Flow:
  1. Validate lead exists and status = ORIENTATION_ATTENDED
  2. Convert lead to Patient in ERPNext (if not already converted)
  3. Create Patient Appointment in ERPNext
  4. Update SGP Lead: status=APPOINTMENT_SCHEDULED, appointment=<name>
  5. Emit event log
  6. Return appointment details

Gating rule (from blueprint):
  Appointments are LOCKED until orientation + assessment are complete.
  FastAPI enforces this independently of ERPNext validation.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.erp_bridge.service import erp_bridge_service
from app.events.logger import event_logger, EventType

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Request / Response schemas ─────────────────────────────────────────────────

class ScheduleAppointmentRequest(BaseModel):
    """
    All fields needed to schedule a consultation appointment.
    lead_id is the primary key — patient conversion happens automatically.
    """
    lead_id:          str
    practitioner:     str            # ERPNext Healthcare Practitioner name
    appointment_date: str            # YYYY-MM-DD
    appointment_time: Optional[str] = None   # HH:MM:SS
    department:       Optional[str] = None
    notes:            Optional[str] = None
    duration:         int            = 30    # minutes


class AppointmentResponse(BaseModel):
    appointment_id:   str
    patient_id:       str
    patient_name:     str
    lead_id:          str
    practitioner:     str
    appointment_date: str
    appointment_time: Optional[str]
    status:           str
    notes:            Optional[str]


class AppointmentStatusUpdate(BaseModel):
    status: str   # Open, Closed, Cancelled, No Show
    notes: Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/", response_model=AppointmentResponse, status_code=201)
async def schedule_appointment(
    req: ScheduleAppointmentRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Full lead → patient → appointment flow.
    Enforces orientation completion gate.
    """
    # 1. Fetch lead and validate eligibility
    lead = await erp_bridge_service.get_lead(req.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead {req.lead_id} not found")

    lead_status = lead.get("status", "")
    if lead_status != "ORIENTATION_ATTENDED":
        raise HTTPException(
            status_code=403,
            detail=(
                f"Lead {req.lead_id} is not eligible for appointment scheduling. "
                f"Current status: {lead_status}. Required: ORIENTATION_ATTENDED"
            ),
        )

    orientation_done = lead.get("orientation_completed", 0)
    if not orientation_done:
        raise HTTPException(
            status_code=403,
            detail="Orientation must be completed before scheduling an appointment.",
        )

    lead_name    = lead.get("lead_name", "")
    lead_mobile  = lead.get("mobile_number", "")
    lead_email   = lead.get("email_id") or lead.get("email", "")

    # 2. Get or create Patient record
    patient_id = await _get_or_create_patient(req.lead_id, lead_name, lead_mobile, lead_email)

    # 3. Create Patient Appointment in ERPNext
    appointment = await erp_bridge_service.create_patient_appointment({
        "patient":          patient_id,
        "practitioner":     req.practitioner,
        "appointment_date": req.appointment_date,
        "appointment_time": req.appointment_time,
        "department":       req.department,
        "duration":         req.duration,
        "notes":            req.notes,
        "status":           "Open",
        # Link back to SGP Lead via reference fields
        "reference_doctype": "SGP Lead",
        "reference_docname": req.lead_id,
    })

    if not appointment:
        raise HTTPException(
            status_code=502,
            detail="Failed to create appointment in ERPNext. Check ERP logs."
        )

    appointment_name = appointment.get("name")

    # 4. Update SGP Lead status + link appointment
    await _update_lead_after_appointment(req.lead_id, appointment_name)

    # 5. Emit event
    await event_logger.log(
        entity_type="appointment",
        entity_id=appointment_name,
        event_type=EventType.APPOINTMENT_CREATED,
        payload={
            "lead_id":          req.lead_id,
            "patient_id":       patient_id,
            "practitioner":     req.practitioner,
            "appointment_date": req.appointment_date,
            "appointment_time": req.appointment_time,
        },
        triggered_by="api",
    )

    logger.info(
        f"Appointment {appointment_name} created for lead {req.lead_id} "
        f"| patient {patient_id} | date {req.appointment_date}"
    )

    return AppointmentResponse(
        appointment_id=appointment_name,
        patient_id=patient_id,
        patient_name=lead_name,
        lead_id=req.lead_id,
        practitioner=req.practitioner,
        appointment_date=req.appointment_date,
        appointment_time=req.appointment_time,
        status="Open",
        notes=req.notes,
    )


@router.get("/lead/{lead_id}")
async def get_lead_appointments(lead_id: str):
    """Get all appointments for a lead, looked up via reference_docname in ERPNext."""
    result = await erp_bridge_service._request(
        "GET",
        "/api/resource/Patient Appointment",
        params={
            "filters": f'[["reference_docname","=","{lead_id}"]]',
            "fields":  '["name","patient","practitioner","appointment_date","appointment_time","status","notes"]',
        },
    )
    return result or []


@router.get("/{appointment_id}")
async def get_appointment(appointment_id: str):
    """Fetch appointment details from ERPNext."""
    appt = await erp_bridge_service.get_appointment(appointment_id)
    if not appt:
        raise HTTPException(status_code=404, detail=f"Appointment {appointment_id} not found")
    return appt


@router.patch("/{appointment_id}/status")
async def update_appointment_status(
    appointment_id: str,
    req: AppointmentStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update appointment status.
    Valid statuses: Open, Closed, Cancelled, No Show

    No Show → triggers reorientation flag on the lead.
    """
    valid_statuses = {"Open", "Closed", "Cancelled", "No Show"}
    if req.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )

    result = await erp_bridge_service._request(
        "PUT",
        f"/api/resource/Patient Appointment/{appointment_id}",
        data={"status": req.status, "notes": req.notes},
    )
    if not result:
        raise HTTPException(status_code=502, detail="Failed to update appointment status")

    # No Show → trigger reorientation on the lead
    if req.status == "No Show":
        lead_id = result.get("reference_docname")
        if lead_id:
            await _trigger_reorientation(lead_id, appointment_id, db)

    await event_logger.log(
        entity_type="appointment",
        entity_id=appointment_id,
        event_type=EventType.APPOINTMENT_STATUS_UPDATED,
        payload={"status": req.status, "notes": req.notes},
        triggered_by="api",
    )

    return {"appointment_id": appointment_id, "status": req.status}


# ── Private helpers ────────────────────────────────────────────────────────────

async def _get_or_create_patient(
    lead_id: str, lead_name: str, mobile: str, email: str
) -> str:
    """
    Find existing Patient linked to this lead, or create one.
    Returns ERPNext Patient document name.
    """
    # Search for existing patient linked to this lead
    existing = await erp_bridge_service._request(
        "GET",
        "/api/resource/Patient",
        params={
            "filters": f'[["custom_sgp_lead","=","{lead_id}"]]',
            "fields":  '["name","patient_name"]',
            "limit":   "1",
        },
    )

    if existing and isinstance(existing, list) and len(existing) > 0:
        patient_name = existing[0].get("name")
        logger.info(f"Existing patient {patient_name} found for lead {lead_id}")
        return patient_name

    # Create new Patient from lead data
    new_patient = await erp_bridge_service._request(
        "POST",
        "/api/resource/Patient",
        data={
            "patient_name": lead_name,
            "mobile":       mobile,
            "email":        email,
            "custom_sgp_lead":     lead_id,
            "status":       "Active",
        },
    )

    if not new_patient:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to create Patient record for lead {lead_id}"
        )

    patient_name = new_patient.get("name")
    logger.info(f"Patient {patient_name} created from lead {lead_id}")
    return patient_name


async def _update_lead_after_appointment(lead_id: str, appointment_name: str) -> None:
    """Update SGP Lead status to APPOINTMENT_SCHEDULED and link the appointment."""
    try:
        await erp_bridge_service._request(
            "PUT",
            f"/api/resource/SGP Lead/{lead_id}",
            data={
                "status":      "APPOINTMENT_SCHEDULED",
                "appointment": appointment_name,
            },
        )
        logger.info(f"Lead {lead_id} updated → APPOINTMENT_SCHEDULED, appt={appointment_name}")
    except Exception as e:
        # Non-blocking — appointment was created, lead update failure is logged not raised
        logger.error(f"Failed to update lead {lead_id} after appointment creation: {e}")


async def _trigger_reorientation(lead_id: str, appointment_id: str, db) -> None:
    """
    No-show detected → set lead status to REORIENTATION_REQUIRED.
    This triggers the manager to re-schedule orientation for this patient.
    """
    try:
        await erp_bridge_service._request(
            "PUT",
            f"/api/resource/SGP Lead/{lead_id}",
            data={"status": "REORIENTATION_REQUIRED"},
        )
        await event_logger.log(
            entity_type="lead",
            entity_id=lead_id,
            event_type=EventType.REORIENTATION_TRIGGERED,
            payload={"reason": "no_show", "appointment_id": appointment_id},
            triggered_by="system",
        )
        logger.info(f"Reorientation triggered for lead {lead_id} after no-show on {appointment_id}")
    except Exception as e:
        logger.error(f"Failed to trigger reorientation for lead {lead_id}: {e}")
