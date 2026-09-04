
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app.config.settings import settings
from app.whatsapp.service import whatsapp_service
from app.whatsapp.bot_engine import bot_engine
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


@router.get("/webhook")
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("[WHATSAPP WEBHOOK] Domain & token verified successfully!")
        return Response(content=hub_challenge, media_type="text/plain")
    else:
        logger.warning(f"[WHATSAPP WEBHOOK] Verification failed. Token mismatch: {hub_verify_token}")
        raise HTTPException(status_code=403, detail="Verification token mismatch")


@router.post("/webhook")
async def receive_webhook(request: Request):
    try:
        payload = await request.json()
        await bot_engine.handle_webhook_payload(payload)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"[WHATSAPP WEBHOOK] Failed to process payload: {e}")
        return {"status": "error", "message": str(e)}
