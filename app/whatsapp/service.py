"""
WhatsApp Service — Meta Cloud API
Uses approved template: orientation_invite
"""

import base64
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

        # Encode both IDs into a single URL-safe token
        token = base64.urlsafe_b64encode(
            f"{session_id}:{patient_id}".encode()
        ).decode().rstrip("=")

        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "template",
            "template": {
                "name": "orientation_invite",
                "language": {"code": "en"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": lead_name},      # {{1}}
                            {"type": "text", "text": session_title},  # {{2}}
                            {"type": "text", "text": date_time},      # {{3}}
                        ],
                    },
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [
                            {"type": "text", "text": token}           # {{1}} in URL
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
                    logger.info(f"WhatsApp template sent to {clean_phone}")
                    return True
                else:
                    logger.error(f"WhatsApp failed for {clean_phone}: {res.text}")
                    return False
            except Exception as e:
                logger.error(f"WhatsApp request error for {clean_phone}: {e}")
                return False


whatsapp_service = WhatsAppService()