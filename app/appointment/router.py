
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


class ScheduleAppointmentRequest(BaseModel):
    lead_id:          str
    practitioner:     str
    appointment_date: str
    appointment_time: Optional[str] = None
    department:       Optional[str] = None
    notes:            Optional[str] = None
    duration:         int            = 30


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
    status: str
    notes: Optional[str] = None



@router.post("/", response_model=AppointmentResponse, status_code=201)
async def schedule_appointment(
    req: ScheduleAppointmentRequest,
    db: AsyncSession = Depends(get_db),
):
    lead = await erp_bridge_service.get_lead(req.lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead {req.lead_id} not found")

    lead_status = lead.get("status", "")
    if lead_status != "ORIENTATION_ATTENDED":
        raise HTTPException(
            status_code=403,
            detail=(
                f"Lead {req.lead_id} is not eligible for appointment scheduling. "
                f"Current status: {lead_status}. "
                "Patient must complete the orientation session (70% watch time) before scheduling."
            ),
        )

    lead_name    = lead.get("lead_name", "")
    lead_mobile  = lead.get("mobile_number", "")
    lead_email   = lead.get("email_id") or lead.get("email", "")

    patient_id = await _get_or_create_patient(req.lead_id, lead_name, lead_mobile, lead_email)

    appointment = await erp_bridge_service.create_patient_appointment({
        "patient":          patient_id,
        "practitioner":     req.practitioner,
        "appointment_date": req.appointment_date,
        "appointment_time": req.appointment_time,
        "department":       req.department,
        "duration":         req.duration,
        "notes":            req.notes,
        "billing_item": "Consultation Fee",
        "status":           "Open",
        "reference_doctype": "SGP Lead",
        "reference_docname": req.lead_id,
    })

    if not appointment:
        raise HTTPException(
            status_code=502,
            detail="Failed to create appointment in ERPNext. Check ERP logs."
        )

    appointment_name = appointment.get("name")

    await _update_lead_after_appointment(req.lead_id, appointment_name)

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
    result = await erp_bridge_service._request(
        "GET",
        "/api/resource/Patient Appointment",
        params={
            "filters": f'[["reference_docname","=","{lead_id}"]]',
            "fields":  '["name","patient","practitioner","appointment_date","appointment_time","status","notes"]',
        },
    )
    return result or []


@router.get("/by-patient/{patient_id}")
async def get_appointments_by_patient(patient_id: str):
    result = await erp_bridge_service._request(
        "GET",
        "/api/resource/Patient Appointment",
        params={
            "filters": f'[["patient","=","{patient_id}"]]',
            "fields":  (
                '["name","patient","patient_name","practitioner",'
                '"appointment_date","appointment_time","status","notes"]'
            ),
            "order_by": "appointment_date desc",
            "limit":    "50",
        },
    )
    return result or []


@router.get("/{appointment_id}")
async def get_appointment(appointment_id: str):
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



async def _get_or_create_patient(
    lead_id: str, lead_name: str, mobile: str, email: str
) -> str:
    patient_id = await erp_bridge_service.get_or_create_patient(lead_id)
    if patient_id:
        return patient_id

    name_parts = (lead_name or "Walk-in Patient").strip().split(maxsplit=1)
    first_name = name_parts[0]
    last_name  = name_parts[1] if len(name_parts) > 1 else ""

    new_patient = await erp_bridge_service._request(
        "POST",
        "/api/resource/Patient",
        data={
            "first_name":      first_name,
            "last_name":       last_name,
            "patient_name":    lead_name or "Walk-in Patient",
            "sex":             "Prefer not to say",
            "mobile":          mobile,
            "email":           email,
            "custom_sgp_lead": lead_id,
            "status":          "Active",
        },
    )

    if not new_patient:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to create Patient record for lead {lead_id}"
        )

    patient_name = new_patient.get("name")
    logger.info(f"Patient {patient_name} created from lead {lead_id} via explicit fallback")
    return patient_name


async def _update_lead_after_appointment(lead_id: str, appointment_name: str) -> None:
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
        logger.error(f"Failed to update lead {lead_id} after appointment creation: {e}")


async def _trigger_reorientation(lead_id: str, appointment_id: str, db) -> None:
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
