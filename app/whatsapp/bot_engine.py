import logging
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any

from app.whatsapp.service import whatsapp_service
from app.whatsapp.state_store import state_store
from app.erp_bridge.service import erp_bridge_service
from app.casesheet.llm_service import llm_service

logger = logging.getLogger(__name__)


@dataclass
class WhatsAppLeadData:
    name: str
    phone: str
    email: Optional[str] = ""
    address: Optional[str] = ""
    city: Optional[str] = ""
    pincode: Optional[str] = ""
    lead_source: str = "WHATSAPP"
    interested_in: Optional[str] = "CONSULTATION"
    notes: Optional[str] = ""


class WhatsAppBotEngine:

    async def handle_webhook_payload(self, payload: Dict[str, Any]) -> None:
        try:
            entry = payload.get("entry", [])[0]
            changes = entry.get("changes", [])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [])

            if not messages:
                return

            msg = messages[0]
            sender_phone = msg.get("from")
            msg_type = msg.get("type")

            if not sender_phone:
                return

            contacts = value.get("contacts", [])
            wa_name = ""
            if contacts:
                wa_name = contacts[0].get("profile", {}).get("name", "").strip()

            session = state_store.get_session(sender_phone)
            if wa_name and "wa_profile_name" not in session.data:
                session.data["wa_profile_name"] = wa_name

            text_body = ""
            action_id = ""

            if msg_type == "text":
                text_body = msg.get("text", {}).get("body", "").strip()
            elif msg_type == "interactive":
                interactive = msg.get("interactive", {})
                int_type = interactive.get("type")
                if int_type == "button_reply":
                    action_id = interactive.get("button_reply", {}).get("id", "")
                    text_body = interactive.get("button_reply", {}).get("title", "")
                elif int_type == "list_reply":
                    action_id = interactive.get("list_reply", {}).get("id", "")
                    text_body = interactive.get("list_reply", {}).get("title", "")

            logger.info(f"Incoming WA msg from {sender_phone} [{session.state}]: text='{text_body}', action='{action_id}'")

            text_clean = text_body.lower().strip()

            if text_clean in ["hi", "hello", "menu", "start", "restart", "help", "main menu"]:
                session.reset()
                await self.send_main_menu(sender_phone)
                return

            if action_id in ["btn_book_consultation", "btn_book_appt"] or text_clean in ["book consultation", "book appointment"]:
                session.update_state("AWAITING_NAME")
                await whatsapp_service.send_text_message(
                    sender_phone,
                    "🌿 Welcome to SGP Healthcare!\n\n"
                    "Let's get your details for our Patient Manager.\n"
                    "Please reply with your *Full Name*:"
                )
                return

            if action_id in ["btn_talk_manager", "btn_support"] or "talk to manager" in text_clean or "talk to specialist" in text_clean or "support" in text_clean:
                await self._process_manager_callback_request(sender_phone, session)
                return

            if action_id == "btn_faqs" or text_clean in ["faqs", "faq", "services", "faqs & services"]:
                await self._send_faq_list_menu(sender_phone)
                return

            if action_id in ["faq_ai", "faq_custom_ai", "faq_ai_assistant"] or "ask ai" in text_clean or "ai care" in text_clean:
                session.update_state("ASKING_AI_QUESTION")
                msg = (
                    "🤖 *SGP AI Care Assistant*\n\n"
                    "Please type your health query or question below. "
                    "Our AI assistant will answer based on official SGP clinical knowledge guidelines!"
                )
                await whatsapp_service.send_text_message(sender_phone, msg)
                return

            if action_id == "faq_timings" or "clinic hours" in text_clean or "location" in text_clean:
                await self._handle_faq_selection(sender_phone, session, "faq_timings")
                return

            if action_id in ["faq_consultation", "faq_fees"] or "consultation process" in text_clean or "fees" in text_clean:
                await self._handle_faq_selection(sender_phone, session, "faq_consultation")
                return

            if action_id == "faq_panchakarma" or "panchakarma" in text_clean:
                await self._handle_faq_selection(sender_phone, session, "faq_panchakarma")
                return

            if action_id == "faq_diet_meds" or "diet" in text_clean or "medication" in text_clean:
                await self._handle_faq_selection(sender_phone, session, "faq_diet_meds")
                return

            if session.state == "AWAITING_NAME":
                await self._handle_name_input(sender_phone, session, text_body)
            elif session.state == "AWAITING_HEALTH_CONCERN":
                await self._handle_health_concern_input(sender_phone, session, text_body)
            elif session.state == "AWAITING_LOCATION":
                await self._handle_location_input(sender_phone, session, text_body)
            elif session.state == "SELECTING_PREFERRED_TIME":
                await self._handle_preferred_time_selection(sender_phone, session, action_id, text_body)
            elif session.state == "ASKING_AI_QUESTION":
                await self._handle_ai_question(sender_phone, session, text_body)
            else:
                session.reset()
                await self.send_main_menu(sender_phone)

        except Exception as e:
            logger.error(f"Error handling WhatsApp webhook payload: {e}", exc_info=True)

    async def send_main_menu(self, phone: str):
        session = state_store.get_session(phone)
        patient_name = session.data.get("patient_name")

        if not patient_name:
            lead = await self._find_lead_by_phone(phone)
            if lead:
                patient_name = lead.get("lead_name")
                session.data["patient_name"] = patient_name
            else:
                patient_name = session.data.get("wa_profile_name", "")

        greeting = f"Welcome *{patient_name}*" if patient_name else "Welcome"

        body_text = (
            f"🌿 *{greeting} to SGP Ayurvedic Healthcare Center!*\n\n"
            "I am your automated AI care assistant. How can we assist you today?"
        )
        buttons = [
            {"id": "btn_book_consultation", "title": "📅 Book Consultation"},
            {"id": "btn_faqs", "title": "❓ FAQs & Services"},
            {"id": "btn_talk_manager", "title": "📞 Talk to Manager"},
        ]
        await whatsapp_service.send_interactive_buttons(
            phone=phone,
            body_text=body_text,
            buttons=buttons,
            header_text="SGP Healthcare Assistant",
            footer_text="Select an option below to continue"
        )
        session.update_state("MAIN_MENU")

    async def _handle_name_input(self, phone: str, session, name: str):
        if len(name.strip()) < 2:
            await whatsapp_service.send_text_message(phone, "Please enter a valid name (at least 2 characters):")
            return

        session.data["patient_name"] = name.strip()
        session.update_state("AWAITING_HEALTH_CONCERN")

        await whatsapp_service.send_text_message(
            phone,
            f"Thank you, *{name.strip()}*!\n\n"
            "Please briefly describe your *Primary Health Concern* or reason for consultation "
            "(e.g. Joint Pain, Digestion, Skin Issues, Panchakarma, Wellness Check):"
        )

    async def _handle_health_concern_input(self, phone: str, session, concern: str):
        if len(concern.strip()) < 2:
            await whatsapp_service.send_text_message(phone, "Please briefly describe your health concern:")
            return

        session.data["health_concern"] = concern.strip()
        session.update_state("AWAITING_LOCATION")

        await whatsapp_service.send_text_message(
            phone,
            "📍 *Location & Address*\n\n"
            "Please reply with your *City, Area, and Pincode*\n"
            "(e.g. Jubilee Hills, Hyderabad - 500033):"
        )

    async def _handle_location_input(self, phone: str, session, location_text: str):
        pincode_match = re.search(r'\b\d{6}\b', location_text)
        pincode = pincode_match.group(0) if pincode_match else ""

        session.data["patient_address"] = location_text.strip()
        session.data["patient_pincode"] = pincode
        session.update_state("SELECTING_PREFERRED_TIME")

        sections = [
            {
                "title": "Preferred Call Window",
                "rows": [
                    {
                        "id": "time_morning",
                        "title": "Morning (10 AM - 1 PM)",
                        "description": "Call me during morning hours"
                    },
                    {
                        "id": "time_afternoon",
                        "title": "Afternoon (1 PM - 4 PM)",
                        "description": "Call me during afternoon hours"
                    },
                    {
                        "id": "time_evening",
                        "title": "Evening (4 PM - 7 PM)",
                        "description": "Call me during evening hours"
                    },
                    {
                        "id": "time_anytime",
                        "title": "Anytime",
                        "description": "Call me as soon as available"
                    },
                ]
            }
        ]
        await whatsapp_service.send_interactive_list(
            phone=phone,
            body_text=(
                f"Thank you! Please select your preferred time window for our *Patient Manager* to call you:"
            ),
            button_label="Select Call Window",
            sections=sections,
            header_text="SGP Consultation Request"
        )

    async def _handle_preferred_time_selection(self, phone: str, session, action_id: str, title: str):
        preferred_time = title if title else "Morning (10 AM - 1 PM)"
        session.data["preferred_time"] = preferred_time

        patient_name = session.data.get("patient_name", "Valued Patient")
        health_concern = session.data.get("health_concern", "General Consultation")
        address = session.data.get("patient_address", "")
        pincode = session.data.get("patient_pincode", "")

        lead_notes = (
            f"Created via SGP WhatsApp Bot.\n"
            f"Primary Health Concern: {health_concern}\n"
            f"Location/Address: {address}\n"
            f"Pincode: {pincode}\n"
            f"Preferred Call Window: {preferred_time}\n"
            f"Action Required: Patient Manager to contact patient and confirm consultation & orientation slot."
        )

        lead_created = None
        try:
            lead_created = await erp_bridge_service.create_lead(
                WhatsAppLeadData(
                    name=patient_name,
                    phone=phone,
                    address=address,
                    pincode=pincode,
                    interested_in="CONSULTATION",
                    notes=lead_notes
                )
            )
            if lead_created:
                logger.info(f"Successfully registered SGP Lead in ERPNext for {phone}: {lead_created.get('name')}")
        except Exception as e:
            logger.error(f"Error registering SGP Lead in ERPNext for {phone}: {e}")

        confirmation_msg = (
            f"✅ *Consultation Request Received!*\n\n"
            f"👤 *Patient Name:* {patient_name}\n"
            f"🩺 *Health Concern:* {health_concern}\n"
            f"📍 *Location:* {address}\n"
            f"🕒 *Preferred Call Window:* {preferred_time}\n\n"
            f"📞 *What happens next?*\n"
            f"Our *Patient Manager* will call you shortly on *{phone}* to answer your questions, "
            f"confirm your consultation schedule, and allocate your orientation slot.\n\n"
            f"_Type 'menu' anytime to return to options._"
        )
        await whatsapp_service.send_text_message(phone, confirmation_msg)
        session.reset()

    async def _process_manager_callback_request(self, phone: str, session):
        patient_name = session.data.get("patient_name") or session.data.get("wa_profile_name") or "Valued Patient"

        lead_notes = (
            f"Requested direct callback from Patient Manager via WhatsApp Bot.\n"
            f"Action Required: Patient Manager to call back patient on priority."
        )

        try:
            await erp_bridge_service.create_lead(
                WhatsAppLeadData(
                    name=patient_name,
                    phone=phone,
                    interested_in="CONSULTATION",
                    notes=lead_notes
                )
            )
            logger.info(f"Registered SGP Lead callback request for {phone}")
        except Exception as e:
            logger.error(f"Error registering SGP Lead callback for {phone}: {e}")

        msg = (
            f"📞 *Patient Manager Callback Requested*\n\n"
            f"Thank you, *{patient_name}*!\n"
            f"Our Patient Manager has been notified and will call you shortly on *{phone}* to assist you.\n\n"
            f"_Type 'menu' anytime to return to main options._"
        )
        await whatsapp_service.send_text_message(phone, msg)
        session.reset()

    async def _send_faq_list_menu(self, phone: str):
        sections = [
            {
                "title": "FAQ Topics & Guidance",
                "rows": [
                    {
                        "id": "faq_timings",
                        "title": "Clinic Hours & Location",
                        "description": "Operating hours, location & patient care contact"
                    },
                    {
                        "id": "faq_consultation",
                        "title": "Consultation Process",
                        "description": "How SGP holistic assessment & 8-week plan works"
                    },
                    {
                        "id": "faq_panchakarma",
                        "title": "Panchakarma & Therapies",
                        "description": "Detox procedures & therapy guidance"
                    },
                    {
                        "id": "faq_diet_meds",
                        "title": "Diet & Medication Rules",
                        "description": "Ayurvedic dosage timing & CCRSTT diet rules"
                    },
                    {
                        "id": "faq_custom_ai",
                        "title": "Ask AI Care Assistant",
                        "description": "Type any health question for instant clinical answers"
                    },
                ]
            }
        ]
        await whatsapp_service.send_interactive_list(
            phone=phone,
            body_text="Choose an FAQ topic below, or select 'Ask AI Care Assistant' to type your question freely:",
            button_label="View FAQ Topics",
            sections=sections,
            header_text="SGP Patient Information"
        )

    async def _handle_faq_selection(self, phone: str, session, action_id: str):
        if action_id == "faq_timings":
            msg = (
                "🏥 *SGP Ayurvedic Healthcare Center*\n\n"
                "⏰ *Clinic Hours:* Monday – Saturday (9:00 AM – 7:00 PM)\n"
                "📍 *Location:* SGP Regional Centers & Online Tele-Consultations\n"
                "📞 *Patient Care Desk:* Managed directly by our dedicated Patient Managers.\n\n"
                "_Type 'book' to request a consultation, or 'menu' for main options._"
            )
            await whatsapp_service.send_text_message(phone, msg)

        elif action_id in ["faq_consultation", "faq_fees"]:
            msg = (
                "🩺 *SGP 3-Step Clinical Process*\n\n"
                "1. *Request Consultation:* Share your health details via this bot.\n"
                "2. *Manager Call:* Our Patient Manager contacts you to confirm your orientation & consultation slot.\n"
                "3. *Holistic Assessment:* Complete Nadi Pariksha, VPK diagnosis, and receive your personalized 8-week regimen.\n\n"
                "_Type 'book' to get started!_"
            )
            await whatsapp_service.send_text_message(phone, msg)

        elif action_id == "faq_panchakarma":
            msg = (
                "🌿 *Panchakarma & Therapy Procedures*\n\n"
                "SGP specializes in authentic Panchakarma & detoxification therapies:\n"
                "• *Basti & Vasthi:* Januvasthi, Kati Vasthi, Greeva Vasthi\n"
                "• *Detox Cleanses:* Nithya & Prathivaara Virechana\n"
                "• *Home Protocols:* Anutailam nasal drops, Steam Inhalation & Gandusham\n\n"
                "_Type 'book' to request a consultation with our Patient Manager._"
            )
            await whatsapp_service.send_text_message(phone, msg)

        elif action_id == "faq_diet_meds":
            msg = (
                "💊 *SGP Diet & Medicine Intake Rules*\n\n"
                "• *Medicine Timings:* Morning (6-8 AM), Evening (6-8 PM) before food unless prescribed.\n"
                "• *Special Intake:* D-Tox (2h after food), Lithozen (20m after food with ginger tea).\n"
                "• *Diet Rule (CCRSTT to avoid):* Avoid Cabbage, Cauliflower, Radish, Spinach, Tomato, Tamarind.\n"
                "• *Recommended Soups:* Barley, Tapioca (Sabu Dana), Rice, and Finger Millet (Ragi).\n\n"
                "_Type 'menu' to return to options._"
            )
            await whatsapp_service.send_text_message(phone, msg)

        elif action_id in ["faq_ai", "faq_custom_ai"]:
            session.update_state("ASKING_AI_QUESTION")
            msg = (
                "🤖 *SGP AI Care Assistant*\n\n"
                "Please type your health query or question below. "
                "Our AI assistant will answer based on official SGP clinical knowledge guidelines!"
            )
            await whatsapp_service.send_text_message(phone, msg)
        else:
            await self.send_main_menu(phone)

    async def _handle_ai_question(self, phone: str, session, query: str):
        if query.lower().strip() in ["menu", "back", "exit", "book"]:
            session.reset()
            if "book" in query.lower():
                session.update_state("AWAITING_NAME")
                await whatsapp_service.send_text_message(phone, "Please reply with your *Full Name*:")
            else:
                await self.send_main_menu(phone)
            return

        await whatsapp_service.send_text_message(phone, "⏳ *Consulting SGP Clinical Knowledge Base...*")

        system_prompt = (
            "You are a friendly, compassionate clinical AI assistant for SGP Ayurvedic Healthcare. "
            "You strictly follow SGP's official patient clinical guidelines:\n"
            "1. Medicines: Morning (6-8 AM), Evening (6-8 PM) before food unless specified. D-Tox (2h after food), Lithozen (20m after food with ginger tea), Carcincure R (2h after food). Keep 15m gap after APD, 5m gap between others.\n"
            "2. Never alter prescription, medium (milk/water), or doses independently.\n"
            "3. Diet (CCRSTT to avoid): Cabbage, Cauliflower, Radish, Spinach, Tomato, Tamarind. Alternatives: Raw mango, Aamchur, Amla, Ginger, Ajwain, Cinnamon.\n"
            "4. Soups: Barley, Sabudana/Tapioca, Rice, Broccoli. Nuts: 5 Cashews, 5 Almonds, 2 tbsp Groundnuts soaked overnight.\n"
            "5. Breathing: DNB left-to-right (10m morning, 10m night). Suryanamaskar only after holding Naukasan for 40s without pain.\n"
            "6. Oils: Anutailam (2 drops nostril/ear daily x2 weeks), Steam inhalation (1x daily x2 weeks), Gandusham/Oil Pulling (sesame oil 1x daily x2 weeks).\n"
            "7. RED-FLAG SAFETY: Never diagnose, promise guaranteed cure/duration, or stop allopathic/BP/diabetes medicines without doctor review.\n"
            "Keep answers concise (max 3-4 sentences) and encourage consulting the SGP Patient Manager for personalized appointment scheduling."
        )
        try:
            answer = await llm_service.generate_completion(
                system_prompt=system_prompt,
                user_content=query
            )
            reply = (
                f"🤖 *SGP Assistant Response:*\n\n{answer}\n\n"
                f"📞 *Would you like our Patient Manager to contact you?*\n"
                f"Type 'book' to request a consultation, or 'menu' for options."
            )
        except Exception as e:
            logger.error(f"AI error for WA query: {e}")
            reply = (
                "SGP Healthcare provides personalized consultations, 8-week diet & supplement plans, "
                "and therapy guidance. Please type 'book' or 'menu' to request a callback from our Patient Manager!"
            )

        await whatsapp_service.send_text_message(phone, reply)

    async def _find_lead_by_phone(self, phone: str) -> Optional[Dict]:
        try:
            leads = await erp_bridge_service.list_leads(limit=50)
            clean_input = phone.replace("+", "").replace(" ", "").replace("-", "")
            for lead in leads:
                mobile = str(lead.get("mobile_number") or "").replace("+", "").replace(" ", "").replace("-", "")
                if mobile and (clean_input in mobile or mobile in clean_input):
                    return lead
        except Exception as e:
            logger.error(f"Error searching SGP Lead by phone: {e}")
        return None


bot_engine = WhatsAppBotEngine()
