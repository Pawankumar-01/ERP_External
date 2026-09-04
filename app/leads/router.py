
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query

from app.leads.models import LeadCreate, LeadResponse, LeadStatus, LeadStatusUpdate
from app.leads.service import lead_service

router = APIRouter()


@router.post("/", response_model=LeadResponse, status_code=201)
async def create_lead(data: LeadCreate):
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
    try:
        return await lead_service.list_leads(status=status, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ERP error: {str(e)}")


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(lead_id: str):
    lead = await lead_service.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead {lead_id} not found in ERPNext")
    return lead


@router.patch("/{lead_id}/status", response_model=LeadResponse)
async def update_lead_status(lead_id: str, data: LeadStatusUpdate):
    try:
        return await lead_service.update_status(lead_id, data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ERP error: {str(e)}")


@router.get("/{lead_id}/eligibility")
async def check_appointment_eligibility(lead_id: str):
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