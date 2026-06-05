"""
WhatsApp Router
POST /api/v1/whatsapp/notify-orientation → send join links to leads
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.whatsapp.service import whatsapp_service
from app.erp_bridge.service import erp_bridge_service

router = APIRouter()
logger = logging.getLogger(__name__)


class OrientationNotifyRequest(BaseModel):
    session_id:    str
    session_title: str
    lead_ids:      List[str]
    scheduled_at:  Optional[str] = None


@router.post("/notify-orientation")
async def notify_orientation(req: OrientationNotifyRequest):
    """
    Fetch each lead's phone from ERPNext and send WhatsApp join link.
    Returns count of successful sends.
    """
    sent = 0
    failed = []

    for lead_id in req.lead_ids:
        try:
            lead = await erp_bridge_service.get_lead(lead_id)
            if not lead:
                logger.warning(f"WhatsApp notify: lead {lead_id} not found in ERPNext")
                failed.append(lead_id)
                continue

            phone = lead.get("mobile_number") or lead.get("phone") or ""
            if not phone:
                logger.warning(f"WhatsApp notify: lead {lead_id} has no phone number")
                failed.append(lead_id)
                continue

            name = lead.get("lead_name") or "Patient"
            patient_id = lead.get("name") or lead_id

            success = await whatsapp_service.send_orientation_invite(
                phone=phone,
                lead_name=name,
                patient_id = patient_id,
                session_id=req.session_id,
                session_title=req.session_title,
                scheduled_at=req.scheduled_at,
            )

            if success:
                sent += 1
            else:
                failed.append(lead_id)

        except Exception as e:
            logger.error(f"WhatsApp notify error for lead {lead_id}: {e}")
            failed.append(lead_id)

    return {
        "sent":   sent,
        "failed": len(failed),
        "failed_lead_ids": failed,
        "total":  len(req.lead_ids),
    }
