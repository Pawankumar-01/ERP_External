"""
Lead Router — HTTP API Endpoints
─────────────────────────────────
All lead operations proxy through LeadService → ERPBridgeService → ERPNext.
No database session is injected here — leads live in ERPNext only.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query

from app.leads.models import LeadCreate, LeadResponse, LeadStatus, LeadStatusUpdate
from app.leads.service import lead_service

router = APIRouter()


@router.post("/", response_model=LeadResponse, status_code=201)
async def create_lead(data: LeadCreate):
    """
    Create a new SGP Lead in ERPNext.
    Returns the created lead with appointment_eligible flag.
    """
    try:
        return await lead_service.create_lead(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ERP error: {str(e)}")


@router.get("/", response_model=List[LeadResponse])
async def list_leads(
    status: Optional[LeadStatus] = Query(None, description="Filter by lead status"),
    limit:  int                  = Query(100, le=500),
):
    """List SGP Leads from ERPNext, optionally filtered by status."""
    try:
        return await lead_service.list_leads(status=status, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ERP error: {str(e)}")


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: str):
    """Fetch a single SGP Lead from ERPNext by document ID."""
    lead = await lead_service.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found in ERPNext")
    return lead


@router.patch("/{lead_id}/status", response_model=LeadResponse)
async def update_lead_status(lead_id: str, data: LeadStatusUpdate):
    """
    Update SGP Lead status in ERPNext.
    Valid statuses: NEW → ORIENTATION_SCHEDULED → ORIENTATION_ATTENDED
                  → APPOINTMENT_SCHEDULED → CONVERTED
                  (or REORIENTATION_REQUIRED / DORMANT at any point)
    """
    try:
        return await lead_service.update_status(lead_id, data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ERP error: {str(e)}")


@router.get("/{lead_id}/eligibility")
async def check_appointment_eligibility(lead_id: str):
    """
    Check if a lead is eligible to book a doctor appointment.
    Eligibility requires status == ORIENTATION_ATTENDED.
    Data is fetched live from ERPNext.
    """
    lead = await lead_service.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found in ERPNext")

    eligible = lead.status == LeadStatus.ORIENTATION_ATTENDED
    return {
        "lead_id":             lead_id,
        "lead_name":           lead.name,
        "current_status":      lead.status,
        "appointment_eligible": eligible,
        "reason": (
            "Orientation completed"
            if eligible
            else f"Status is '{lead.status}' — orientation must be completed first"
        ),
    }