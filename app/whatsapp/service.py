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
        clean_phone = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
        if len(clean_phone) == 10:
            clean_phone = "91" + clean_phone

        date_time = scheduled_at or "To be confirmed"

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
                            {"type": "text", "text": lead_name},
                            {"type": "text", "text": patient_id},
                            {"type": "text", "text": session_id},
                            {"type": "text", "text": date_time},
                            {"type": "text", "text": join_url},
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

    def _normalize_phone(self, phone: str) -> str:
        clean = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
        if len(clean) == 10:
            clean = "91" + clean
        return clean

    async def send_text_message(self, phone: str, text: str) -> bool:
        clean_phone = self._normalize_phone(phone)
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_phone,
            "type": "text",
            "text": {"body": text},
        }
        return await self._send_api_payload(clean_phone, payload)

    async def send_interactive_buttons(
        self,
        phone: str,
        body_text: str,
        buttons: list,
        header_text: Optional[str] = None,
        footer_text: Optional[str] = None,
    ) -> bool:
        clean_phone = self._normalize_phone(phone)
        formatted_buttons = []
        for btn in buttons[:3]:
            formatted_buttons.append({
                "type": "reply",
                "reply": {
                    "id": str(btn.get("id")),
                    "title": str(btn.get("title"))[:20],
                }
            })

        interactive = {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": formatted_buttons},
        }
        if header_text:
            interactive["header"] = {"type": "text", "text": header_text}
        if footer_text:
            interactive["footer"] = {"text": footer_text}

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_phone,
            "type": "interactive",
            "interactive": interactive,
        }
        return await self._send_api_payload(clean_phone, payload)

    async def send_interactive_list(
        self,
        phone: str,
        body_text: str,
        button_label: str,
        sections: list,
        header_text: Optional[str] = None,
        footer_text: Optional[str] = None,
    ) -> bool:
        clean_phone = self._normalize_phone(phone)
        interactive = {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": button_label[:20],
                "sections": sections,
            },
        }
        if header_text:
            interactive["header"] = {"type": "text", "text": header_text}
        if footer_text:
            interactive["footer"] = {"text": footer_text}

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_phone,
            "type": "interactive",
            "interactive": interactive,
        }
        return await self._send_api_payload(clean_phone, payload)

    async def _send_api_payload(self, clean_phone: str, payload: dict) -> bool:
        headers = {
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }
        url = f"{GRAPH_API_URL}/{settings.WHATSAPP_PHONE_ID}/messages"

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code == 200:
                    logger.info(f"WhatsApp message sent to {clean_phone}")
                    return True
                else:
                    logger.error(f"WhatsApp API failed for {clean_phone}: {res.status_code} - {res.text}")
                    return False
            except Exception as e:
                logger.error(f"WhatsApp send error for {clean_phone}: {e}")
                return False


whatsapp_service = WhatsAppService()