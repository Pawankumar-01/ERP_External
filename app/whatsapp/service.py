"""
WhatsApp Service — Meta Cloud API
Uses approved template: sgp_orientation_invite
"""
import logging
import httpx
from typing import Optional
from app.config.settings import settings

logger = logging.getLogger(__name__)
GRAPH_API_URL = "https://graph.facebook.com/v19.0"

class WhatsAppService:
    async def send_orientation_invite(
        self,
        phone: str,
        lead_name: str,
        patient_id: str,
        session_id: str,
        session_title: str,
        scheduled_at: Optional[str] = None,
    ) -> bool:
        # Normalize phone
        clean_phone = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
        if len(clean_phone) == 10:
            clean_phone = "91" + clean_phone

        date_time = scheduled_at or "To be confirmed"

        # Join URL — /meet/ frontend page.
        # Strip any accidental trailing slash from FRONTEND_BASE_URL so we never
        # produce URLs like http://host/:8001/meet/... when the env var has a slash.
        base = settings.FRONTEND_BASE_URL.rstrip("/")
        join_url = f"{base}/meet/index.html?session={session_id}&lead={patient_id}"

        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "template",
            "template": {
                "name": "orientation_details",
                "language": {"code": "en"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": lead_name},    # {{1}} name
                            {"type": "text", "text": patient_id},   # {{2}} patient id
                            {"type": "text", "text": session_id},   # {{3}} session id
                            {"type": "text", "text": date_time},    # {{4}} date time
                            {"type": "text", "text": join_url},     # {{5}} join link
                        ],
                    },
                ],
            },
        }

        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        url = f"{GRAPH_API_URL}/{settings.WHATSAPP_PHONE_ID}/messages"

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code == 200:
                    logger.info(f"WhatsApp sent to {clean_phone}")
                    return True
                else:
                    logger.error(f"WhatsApp failed for {clean_phone}: {res.text}")
                    return False
            except Exception as e:
                logger.error(f"WhatsApp error for {clean_phone}: {e}")
                return False

whatsapp_service = WhatsAppService()