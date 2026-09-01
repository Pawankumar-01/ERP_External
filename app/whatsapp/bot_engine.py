"""
WhatsApp Interactive Menu & Lead Generation Engine
Handles multi-turn dialogue, ERPNext Lead/Appointment creation, and Gemini AI Q&A.
"""
import logging
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
        """
        Main entry point for incoming Meta Cloud API webhook payloads.
        """
        try:
            entry = payload.get("entry", [])[0]
            changes = entry.get("changes", [])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [])
            
            if not messages:
                return  # Status updates (sent/delivered/read)

            msg = messages[0]
            sender_phone = msg.get("from")
            msg_type = msg.get("type")

            if not sender_phone:
                return

            # Extract WhatsApp profile name if sent by Meta
            contacts = value.get("contacts", [])
            wa_name = ""
            if contacts:
                wa_name = contacts[0].get("profile", {}).get("name", "").strip()

            session = state_store.get_session(sender_phone)
            if wa_name and "wa_profile_name" not in session.data:
                session.data["wa_profile_name"] = wa_name
            
            # Extract content based on type
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

            # Reset command check
            if text_body.lower() in ["hi", "hello", "menu", "start", "restart", "help", "main menu"]:
                session.reset()
                await self.send_main_menu(sender_phone)
                return

            # State machine router
            if session.state == "MAIN_MENU":
                await self._handle_main_menu_action(sender_phone, session, text_body, action_id)
            elif session.state == "AWAITING_NAME":
                await self._handle_name_input(sender_phone, session, text_body)
            elif session.state == "AWAITING_HEALTH_CONCERN":
                await self._handle_health_concern_input(sender_phone, session, text_body)
            elif session.state == "AWAITING_ADDRESS":
                await self._handle_address_input(sender_phone, session, text_body)
            elif session.state == "SELECTING_TREATMENT":
                await self._handle_treatment_selection(sender_phone, session, action_id, text_body)
            elif session.state == "SELECTING_SLOT":
                await self._handle_slot_selection(sender_phone, session, action_id, text_body)
            elif session.state == "ASKING_AI_QUESTION":
                await self._handle_ai_question(sender_phone, text_body)
            else:
                await self.send_main_menu(sender_phone)

        except Exception as e:
            logger.error(f"Error handling WhatsApp webhook payload: {e}", exc_info=True)

    async def send_main_menu(self, phone: str):
        # Check ERPNext for existing lead/patient name, or fallback to Meta WA Profile name
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
            {"id": "btn_book_appt", "title": "📅 Book Appointment"},
            {"id": "btn_faqs", "title": "❓ FAQs & Services"},
            {"id": "btn_support", "title": "🩺 Talk to Specialist"},
        ]
        await whatsapp_service.send_interactive_buttons(
            phone=phone,
            body_text=body_text,
            buttons=buttons,
            header_text="SGP Healthcare Assistant",
            footer_text="Select an option below to continue"
        )

    async def _handle_main_menu_action(self, phone: str, session, text_body: str, action_id: str):
        if action_id == "btn_book_appt" or "book" in text_body.lower():
            session.update_state("AWAITING_NAME")
            await whatsapp_service.send_text_message(
                phone,
                "Welcome! Let's get your consultation scheduled.\n\n"
                "Please reply with your *Full Name*:"
            )

        elif action_id == "btn_faqs" or "faq" in text_body.lower() or "services" in text_body.lower():
            await self._send_faq_list_menu(phone)

        elif action_id == "btn_support" or "talk" in text_body.lower() or "support" in text_body.lower():
            await whatsapp_service.send_text_message(
                phone,
                "🩺 *Care Team Callback Requested*\n\n"
                "Our clinic executive will contact you shortly on this number to assist you with your health query.\n\n"
                "You can also type 'menu' anytime to return to the main menu."
            )
            # Log as lead in ERPNext
            lead = await self._find_lead_by_phone(phone)
            if not lead:
                await erp_bridge_service.create_lead(
                    WhatsAppLeadData(
                        name=f"WA Contact ({phone[-4:]})",
                        phone=phone,
                        notes="Requested care team callback via WhatsApp Bot"
                    )
                )
        else:
            await self.send_main_menu(phone)

    async def _handle_name_input(self, phone: str, session, name: str):
        if len(name) < 2:
            await whatsapp_service.send_text_message(phone, "Please enter a valid name:")
            return

        session.data["patient_name"] = name
        session.update_state("AWAITING_HEALTH_CONCERN")

        await whatsapp_service.send_text_message(
            phone,
            f"Thank you, *{name}*!\n\n"
            "Please briefly tell us about your *primary health concern* or reason for consultation (e.g. Joint Pain, Digestion, Stress, Panchakarma, Wellness Check):"
        )

    async def _handle_health_concern_input(self, phone: str, session, concern: str):
        session.data["health_concern"] = concern
        session.update_state("AWAITING_ADDRESS")

        await whatsapp_service.send_text_message(
            phone,
            "📍 *Residential Address & Location*\n\n"
            "Please reply with your *Full Address, City, and Pincode*\n"
            "(e.g. 12-3 Main Road, Jubilee Hills, Hyderabad - 500033):"
        )

    async def _handle_address_input(self, phone: str, session, address_text: str):
        import re
        pincode_match = re.search(r'\b\d{6}\b', address_text)
        pincode = pincode_match.group(0) if pincode_match else ""

        session.data["patient_address"] = address_text
        session.data["patient_pincode"] = pincode

        # Create Lead in ERPNext
        try:
            lead_res = await erp_bridge_service.create_lead(
                WhatsAppLeadData(
                    name=session.data.get("patient_name", "Valued Patient"),
                    phone=phone,
                    address=address_text,
                    pincode=pincode,
                    interested_in="CONSULTATION",
                    notes=(
                        f"Created via WhatsApp Bot.\n"
                        f"Primary Concern: {session.data.get('health_concern', '')}\n"
                        f"Address: {address_text}\n"
                        f"Pincode: {pincode}"
                    )
                )
            )
            if lead_res:
                session.data["lead_id"] = lead_res.get("name")
                logger.info(f"Created ERPNext lead for WA user {phone}: {lead_res.get('name')}")
        except Exception as e:
            logger.error(f"Failed to create ERPNext lead for {phone}: {e}")

        session.update_state("SELECTING_TREATMENT")
        await self._prompt_treatment_selection(phone, session.data.get("patient_name", "Patient"))

    async def _prompt_treatment_selection(self, phone: str, patient_name: str):
        sections = [
            {
                "title": "Consultation Type",
                "rows": [
                    {
                        "id": "consult_new",
                        "title": "New Consultation",
                        "description": "First time holistic clinical assessment"
                    },
                    {
                        "id": "consult_followup",
                        "title": "Follow-up Review",
                        "description": "Review progress & current prescription"
                    },
                    {
                        "id": "consult_panchakarma",
                        "title": "Panchakarma & Therapy",
                        "description": "Detox procedures & therapy guidance"
                    },
                ]
            }
        ]
        await whatsapp_service.send_interactive_list(
            phone=phone,
            body_text=f"Hello *{patient_name}*! Please select the consultation category for your appointment:",
            button_label="Choose Category",
            sections=sections,
            header_text="Novadigm Consultation"
        )

    async def _handle_treatment_selection(self, phone: str, session, action_id: str, title: str):
        session.data["treatment"] = title or "Novadigm Consultation"
        session.update_state("SELECTING_SLOT")

        sections = [
            {
                "title": "Available Slots",
                "rows": [
                    {
                        "id": "slot_morning_1",
                        "title": "Morning (10:00 - 11:30)",
                        "description": "First available morning slot"
                    },
                    {
                        "id": "slot_morning_2",
                        "title": "Midday (11:30 - 1:00)",
                        "description": "Midday slot"
                    },
                    {
                        "id": "slot_evening_1",
                        "title": "Evening (4:30 - 6:00)",
                        "description": "Evening slot"
                    },
                ]
            }
        ]
        await whatsapp_service.send_interactive_list(
            phone=phone,
            body_text=f"Category Selected: *{session.data['treatment']}*\n\nPlease select your preferred appointment time slot:",
            button_label="Select Time Slot",
            sections=sections,
            header_text="Select Consultation Slot"
        )

    async def _handle_slot_selection(self, phone: str, session, action_id: str, slot_title: str):
        patient_name = session.data.get("patient_name", "Valued Patient")
        treatment = session.data.get("treatment", "Novadigm Consultation")
        slot = slot_title or "Preferred Time Slot"

        # Create Patient & Appointment in ERPNext
        try:
            lead_id = session.data.get("lead_id")
            if lead_id:
                patient_id = await erp_bridge_service.get_or_create_patient(lead_id)
                if patient_id:
                    appt_res = await erp_bridge_service.create_patient_appointment({
                        "patient": patient_id,
                        "department": treatment,
                        "notes": f"Booked via Novadigm WhatsApp Bot. Selected Slot: {slot}",
                        "appointment_date": "Today / Scheduled"
                    })
                    if appt_res:
                        logger.info(f"Scheduled ERPNext appointment for WA user {phone}: {appt_res.get('name')}")
        except Exception as e:
            logger.error(f"Error creating ERPNext appointment for {phone}: {e}")

        # Send interactive confirmation
        confirmation_msg = (
            f"🎉 *Appointment Confirmed!*\n\n"
            f"👤 *Patient Name:* {patient_name}\n"
            f"🩺 *Category:* {treatment}\n"
            f"⏰ *Slot:* {slot}\n"
            f"📍 *Location:* Novadigm Health Center\n\n"
            f"Our team will send a reminder link prior to your consultation.\n"
            f"Type 'menu' anytime to return to the main options."
        )
        await whatsapp_service.send_text_message(phone, confirmation_msg)
        session.reset()

    async def _send_faq_list_menu(self, phone: str):
        sections = [
            {
                "title": "FAQ Topics",
                "rows": [
                    {
                        "id": "faq_timings",
                        "title": "Clinic Hours & Location",
                        "description": "Opening hours and address details"
                    },
                    {
                        "id": "faq_fees",
                        "title": "Consultation Fees",
                        "description": "Fee details & treatment packages"
                    },
                    {
                        "id": "faq_panchakarma",
                        "title": "About Panchakarma",
                        "description": "Purification procedures & preparation"
                    },
                    {
                        "id": "faq_custom_ai",
                        "title": "Ask AI Care Assistant",
                        "description": "Ask any specific question about your health"
                    },
                ]
            }
        ]
        await whatsapp_service.send_interactive_list(
            phone=phone,
            body_text="Choose an FAQ topic below, or select 'Ask AI Care Assistant' to type your question freely:",
            button_label="View FAQ Topics",
            sections=sections,
            header_text="Novadigm Patient FAQs"
        )

    async def _handle_ai_question(self, phone: str, query: str):
        await whatsapp_service.send_text_message(phone, "⏳ *Consulting Novadigm Clinical Knowledge Base...*")

        system_prompt = (
            "You are a friendly, compassionate clinical AI assistant for Novadigm Health. "
            "You strictly follow Novadigm Health's official patient guidelines:\n"
            "1. Medicines: Morning (6-8 AM), Evening (6-8 PM) before food unless specified. D-Tox (2h after food), Lithozen (20m after food with ginger tea), Carcincure R (2h after food). Keep 15m gap after APD, 5m gap between others.\n"
            "2. Never alter prescription, medium (milk/water), or doses independently.\n"
            "3. Diet (CCRSTT to avoid): Cabbage, Cauliflower, Radish, Spinach, Tomato, Tamarind. Alternatives: Raw mango, Aamchur, Amla, Ginger, Ajwain, Cinnamon.\n"
            "4. Soups: Barley, Sabudana/Tapioca, Rice, Broccoli. Nuts: 5 Cashews, 5 Almonds, 2 tbsp Groundnuts soaked overnight.\n"
            "5. Breathing: DNB left-to-right (10m morning, 10m night). Suryanamaskar only after holding Naukasan for 40s without pain.\n"
            "6. Oils: Anutailam (2 drops nostril/ear daily x2 weeks), Steam inhalation (1x daily x2 weeks), Gandusham/Oil Pulling (sesame oil 1x daily x2 weeks).\n"
            "7. RED-FLAG SAFETY: Never diagnose, promise guaranteed cure/duration, or stop allopathic/BP/diabetes medicines without doctor review.\n"
            "Keep answers concise (max 3-4 sentences) and encourage consulting the Novadigm clinical team for personalized advice."
        )
        try:
            answer = await llm_service.generate_completion(
                system_prompt=system_prompt,
                user_content=query
            )
            reply = f"🤖 *Novadigm Assistant Response:*\n\n{answer}\n\n_Type 'menu' to return to options._"
        except Exception as e:
            logger.error(f"AI error for WA query: {e}")
            reply = (
                "Novadigm Health provides personalized consultations, 8-week diet & supplement plans, "
                "and therapy guidance. Please type 'menu' to schedule a consultation with our clinical team!"
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
            logger.error(f"Error searching ERPNext lead by phone: {e}")
        return None


bot_engine = WhatsAppBotEngine()
