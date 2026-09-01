"""
Novadigm Health — WhatsApp Interactive Menu & Lead Generation Engine
Handles multi-turn dialogue, FAQ navigation, ERPNext Lead/Appointment creation,
emergency detection, escalation, and Gemini AI Q&A.
"""
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

from app.whatsapp.service import whatsapp_service
from app.whatsapp.state_store import state_store
from app.whatsapp.faq_data import (
    FAQ_DATA, MAIN_MENU_ROWS, MENU_TO_FAQ_KEY,
    EMERGENCY_KEYWORDS, EMERGENCY_RESPONSE,
    ESCALATION_RESPONSE, IMPORTANT_NOTICE,
)
from app.erp_bridge.service import erp_bridge_service

logger = logging.getLogger(__name__)


@dataclass
class WhatsAppLeadData:
    name: str
    phone: str
    email: Optional[str] = ""
    lead_source: str = "WhatsApp Bot"
    interested_in: Optional[str] = "Ayurvedic Consultation"
    notes: Optional[str] = ""


def _is_emergency(text: str) -> bool:
    """Check if a patient message contains emergency red-flag keywords."""
    lower = text.lower()
    return any(kw in lower for kw in EMERGENCY_KEYWORDS)


class WhatsAppBotEngine:

    # ── Webhook Entry Point ──────────────────────────────────────────────────

    async def handle_webhook_payload(self, payload: Dict[str, Any]) -> None:
        try:
            entry   = payload.get("entry", [])[0]
            changes = entry.get("changes", [])[0]
            value   = changes.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                return  # Delivery/read receipts — ignore

            msg = messages[0]
            sender_phone = msg.get("from")
            msg_type     = msg.get("type")
            if not sender_phone:
                return

            session = state_store.get_session(sender_phone)

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
            elif msg_type == "document":
                await self._handle_report_upload(sender_phone, session)
                return
            elif msg_type == "image":
                await self._handle_report_upload(sender_phone, session)
                return

            logger.info(
                "WA msg from %s [%s]: text='%s' action='%s'",
                sender_phone, session.state, text_body[:60], action_id
            )

            # ── Emergency check (highest priority) ──────────────────────────
            if _is_emergency(text_body):
                await whatsapp_service.send_text_message(sender_phone, EMERGENCY_RESPONSE)
                return

            # ── Global reset commands ────────────────────────────────────────
            if text_body.lower() in {"hi", "hello", "menu", "start", "restart", "help", "main menu", "0"}:
                session.reset()
                await self.send_main_menu(sender_phone)
                return

            # ── State machine router ─────────────────────────────────────────
            state = session.state
            if state == "MAIN_MENU":
                await self._handle_main_menu_action(sender_phone, session, action_id, text_body)
            elif state == "FAQ_CATEGORY":
                await self._handle_faq_question_selection(sender_phone, session, action_id)
            elif state == "AWAITING_NAME":
                await self._handle_name_input(sender_phone, session, text_body)
            elif state == "AWAITING_HEALTH_CONCERN":
                await self._handle_health_concern_input(sender_phone, session, text_body)
            elif state == "SELECTING_TREATMENT":
                await self._handle_treatment_selection(sender_phone, session, action_id, text_body)
            elif state == "SELECTING_SLOT":
                await self._handle_slot_selection(sender_phone, session, action_id, text_body)
            elif state == "COLLECTING_APPT_INFO":
                await self._handle_appointment_info(sender_phone, session, text_body)
            elif state == "ASKING_AI_QUESTION":
                await self._handle_ai_question(sender_phone, text_body)
            else:
                await self.send_main_menu(sender_phone)

        except Exception as e:
            logger.error("Error handling WhatsApp webhook payload: %s", e, exc_info=True)

    # ── Main Menu ────────────────────────────────────────────────────────────

    async def send_main_menu(self, phone: str):
        await whatsapp_service.send_text_message(
            phone, IMPORTANT_NOTICE
        )
        await whatsapp_service.send_interactive_list(
            phone=phone,
            body_text=(
                "🌿 *Welcome to Novadigm Health Patient Support*\n\n"
                "How may we assist you today? Please select a category below."
            ),
            button_label="View Options",
            sections=[{"title": "Support Categories", "rows": MAIN_MENU_ROWS}],
            header_text="Novadigm Health",
            footer_text="Type 'menu' anytime to return here",
        )

    # ── Main Menu Selection Handler ──────────────────────────────────────────

    async def _handle_main_menu_action(
        self, phone: str, session, action_id: str, text_body: str
    ):
        # Map menu selection → FAQ category display
        faq_key = MENU_TO_FAQ_KEY.get(action_id)
        if faq_key:
            await self._show_faq_category(phone, session, faq_key)
            return

        # Appointment booking
        if action_id == "menu_appointments" or "appoint" in text_body.lower() or "book" in text_body.lower():
            lead = await self._find_lead_by_phone(phone)
            if lead:
                session.data["patient_name"] = lead.get("lead_name") or "Valued Patient"
                session.data["lead_id"] = lead.get("name")
                session.update_state("SELECTING_TREATMENT")
                await self._prompt_treatment_selection(phone, session.data["patient_name"])
            else:
                session.update_state("AWAITING_NAME")
                await whatsapp_service.send_text_message(
                    phone,
                    "📅 *Appointment Booking*\n\nWelcome! Let's get your consultation scheduled.\n\n"
                    "Please reply with your *Full Name*:"
                )
            return

        # Request care team connection
        if action_id == "menu_team" or "speak" in text_body.lower() or "team" in text_body.lower():
            await self._connect_to_team(phone, session)
            return

        # Fallback
        await self.send_main_menu(phone)

    # ── FAQ Category → Question List ─────────────────────────────────────────

    async def _show_faq_category(self, phone: str, session, faq_key: str):
        category = FAQ_DATA.get(faq_key)
        if not category:
            await self.send_main_menu(phone)
            return

        rows = [
            {
                "id": q["id"],
                "title": q["short_title"][:24],
                "description": "",
            }
            for q in category["questions"]
        ]
        rows.append({"id": "back_to_menu", "title": "⬅️ Back to Main Menu", "description": ""})

        session.update_state("FAQ_CATEGORY", faq_key=faq_key)

        await whatsapp_service.send_interactive_list(
            phone=phone,
            body_text=f"{category['emoji']} *{category['title']}*\n\nSelect a question below:",
            button_label="Select Question",
            sections=[{"title": category["title"], "rows": rows}],
            header_text="Novadigm Health FAQs",
            footer_text="Type 'menu' to return to the main menu",
        )

    # ── FAQ Question Answer Delivery ─────────────────────────────────────────

    async def _handle_faq_question_selection(self, phone: str, session, action_id: str):
        if action_id == "back_to_menu":
            session.reset()
            await self.send_main_menu(phone)
            return

        # Search all FAQ categories for the matching question ID
        answer = None
        for cat_data in FAQ_DATA.values():
            for q in cat_data.get("questions", []):
                if q["id"] == action_id:
                    answer = q["answer"]
                    break
            if answer:
                break

        if not answer:
            await self.send_main_menu(phone)
            return

        await whatsapp_service.send_text_message(phone, answer)

        # Offer next-step buttons
        await whatsapp_service.send_interactive_buttons(
            phone=phone,
            body_text="Was this helpful? What would you like to do next?",
            buttons=[
                {"id": "btn_more_faqs",  "title": "❓ More Questions"},
                {"id": "btn_book_appt",  "title": "📅 Book Appointment"},
                {"id": "btn_team",       "title": "🩺 Speak to Team"},
            ],
            footer_text="Type 'menu' to return to the main menu",
        )
        session.update_state("MAIN_MENU")

    # ── Next-Step Button Handler ─────────────────────────────────────────────

    async def _handle_main_menu_action_from_buttons(
        self, phone: str, session, action_id: str
    ):
        if action_id == "btn_more_faqs":
            session.reset()
            await self.send_main_menu(phone)
        elif action_id == "btn_book_appt":
            await self._handle_main_menu_action(phone, session, "menu_appointments", "book")
        elif action_id == "btn_team":
            await self._connect_to_team(phone, session)
        else:
            await self.send_main_menu(phone)

    # ── Connect to Care Team ─────────────────────────────────────────────────

    async def _connect_to_team(self, phone: str, session):
        await whatsapp_service.send_text_message(phone, ESCALATION_RESPONSE)
        await whatsapp_service.send_text_message(
            phone,
            "🩺 *Care Team Callback Requested*\n\n"
            "Our clinic executive will contact you shortly on this number.\n\n"
            "Type 'menu' anytime to return to the main menu."
        )
        # Log as lead in ERPNext if not already known
        lead = await self._find_lead_by_phone(phone)
        if not lead:
            try:
                await erp_bridge_service.create_lead(
                    WhatsAppLeadData(
                        name=f"WA Patient ({phone[-4:]})",
                        phone=phone,
                        notes="Requested care team callback via WhatsApp Bot",
                    )
                )
            except Exception as e:
                logger.error("Failed to create ERPNext lead for callback request %s: %s", phone, e)
        session.reset()

    # ── Report Upload Handler ────────────────────────────────────────────────

    async def _handle_report_upload(self, phone: str, session):
        await whatsapp_service.send_text_message(
            phone,
            "📋 *Report Received*\n\n"
            "Thank you for sending your report.\n\n"
            "Please ensure:\n"
            "• The report belongs to the correct patient\n"
            "• Your registered name/ID is provided\n"
            "• Images/documents are clear and complete\n\n"
            "Your report will be reviewed by the appropriate clinician. "
            "The chatbot cannot provide an autonomous diagnosis from uploaded reports.\n\n"
            "Type 'menu' to return to the main menu."
        )
        session.reset()

    # ── Appointment Booking Flow ─────────────────────────────────────────────

    async def _handle_name_input(self, phone: str, session, name: str):
        if len(name.strip()) < 2:
            await whatsapp_service.send_text_message(phone, "Please enter a valid full name:")
            return
        session.data["patient_name"] = name.strip()
        session.update_state("AWAITING_HEALTH_CONCERN")
        await whatsapp_service.send_text_message(
            phone,
            f"Thank you, *{name.strip()}*!\n\n"
            "Please briefly tell us your *primary health concern* or reason for consultation\n"
            "(e.g., Joint Pain, Digestion, Stress, Panchakarma, General Wellness):"
        )

    async def _handle_health_concern_input(self, phone: str, session, concern: str):
        session.data["health_concern"] = concern
        try:
            lead_res = await erp_bridge_service.create_lead(
                WhatsAppLeadData(
                    name=session.data.get("patient_name", "Valued Patient"),
                    phone=phone,
                    interested_in=concern[:50],
                    notes=f"Created via WhatsApp Bot. Primary Concern: {concern}",
                )
            )
            if lead_res:
                session.data["lead_id"] = lead_res.get("name")
                logger.info("ERPNext lead created for WA user %s: %s", phone, lead_res.get("name"))
        except Exception as e:
            logger.error("Failed to create ERPNext lead for %s: %s", phone, e)

        session.update_state("SELECTING_TREATMENT")
        await self._prompt_treatment_selection(phone, session.data.get("patient_name", "Patient"))

    async def _prompt_treatment_selection(self, phone: str, patient_name: str):
        sections = [
            {
                "title": "Select Specialised Clinic",
                "rows": [
                    {"id": "treat_general",      "title": "Kaya Chikitsa (General)",     "description": "Chronic conditions & consultations"},
                    {"id": "treat_panchakarma",  "title": "Panchakarma & Detox",          "description": "8-week purification therapies"},
                    {"id": "treat_lifestyle",    "title": "Diet & Lifestyle Plan",        "description": "Ayurvedic nutrition & Yoga"},
                ],
            }
        ]
        await whatsapp_service.send_interactive_list(
            phone=phone,
            body_text=f"Hello *{patient_name}*! Please select your consultation specialty:",
            button_label="Choose Specialty",
            sections=sections,
            header_text="Novadigm Health — Appointment",
        )

    async def _handle_treatment_selection(
        self, phone: str, session, action_id: str, title: str
    ):
        session.data["treatment"] = title or "Ayurvedic Consultation"
        session.update_state("SELECTING_SLOT")

        sections = [
            {
                "title": "Available Slots",
                "rows": [
                    {"id": "slot_morning",  "title": "Morning  10:00 AM – 11:30 AM", "description": ""},
                    {"id": "slot_midday",   "title": "Midday   11:30 AM – 01:00 PM", "description": ""},
                    {"id": "slot_evening",  "title": "Evening  04:30 PM – 06:00 PM", "description": ""},
                ],
            }
        ]
        await whatsapp_service.send_interactive_list(
            phone=phone,
            body_text=(
                f"*Specialty:* {session.data['treatment']}\n\n"
                "Please select your preferred appointment time slot:"
            ),
            button_label="Select Slot",
            sections=sections,
            header_text="Novadigm Health — Select Slot",
        )

    async def _handle_slot_selection(
        self, phone: str, session, action_id: str, slot_title: str
    ):
        patient_name = session.data.get("patient_name", "Valued Patient")
        treatment    = session.data.get("treatment",    "Ayurvedic Consultation")
        slot         = slot_title or "Preferred Time Slot"

        try:
            lead_id = session.data.get("lead_id")
            if lead_id:
                patient_id = await erp_bridge_service.get_or_create_patient(lead_id)
                if patient_id:
                    appt_res = await erp_bridge_service.create_patient_appointment({
                        "patient":          patient_id,
                        "department":       treatment,
                        "notes":            f"Booked via WhatsApp Bot. Slot: {slot}",
                        "appointment_date": "Pending Confirmation",
                    })
                    if appt_res:
                        logger.info("ERPNext appointment created for %s: %s", phone, appt_res.get("name"))
        except Exception as e:
            logger.error("Error creating ERPNext appointment for %s: %s", phone, e)

        await whatsapp_service.send_text_message(
            phone,
            f"🎉 *Appointment Request Received!*\n\n"
            f"👤 *Patient Name:* {patient_name}\n"
            f"🩺 *Specialty:* {treatment}\n"
            f"⏰ *Preferred Slot:* {slot}\n\n"
            f"Our team will confirm your appointment shortly.\n\n"
            f"📋 _Please keep ready your latest prescription, current medicine list, "
            f"and recent investigation reports for your consultation._\n\n"
            f"Type 'menu' to return to the main options."
        )
        session.reset()

    # ── Gemini AI Q&A Fallback ───────────────────────────────────────────────

    async def _handle_ai_question(self, phone: str, query: str):
        await whatsapp_service.send_text_message(
            phone, "⏳ *Consulting Novadigm Knowledge Base...*"
        )
        try:
            from app.casesheet.llm_service import llm_service
            system_prompt = (
                "You are a patient-care assistant for Novadigm Health Ayurvedic Healthcare. "
                "Provide helpful, concise (max 3–4 sentences) answers to patient queries about "
                "Ayurvedic consultations, Panchakarma therapies, clinic procedures, and general health guidance. "
                "Never diagnose diseases, prescribe medicines, or modify doses. "
                "Always encourage the patient to consult their treating doctor for individual medical decisions. "
                "If the query sounds like an emergency, immediately direct the patient to seek emergency medical care."
            )
            answer = await llm_service.generate_completion(
                system_prompt=system_prompt,
                user_content=query,
            )
            reply = f"🤖 *Novadigm Assistant:*\n\n{answer}\n\n_Type 'menu' to return to options._"
        except Exception as e:
            logger.error("Gemini AI error for WA query: %s", e)
            reply = ESCALATION_RESPONSE

        await whatsapp_service.send_text_message(phone, reply)

    # ── Appointment Info Collection (fallback text flow) ─────────────────────

    async def _handle_appointment_info(self, phone: str, session, text_body: str):
        step = session.data.get("appt_step", "name")
        if step == "name":
            await self._handle_name_input(phone, session, text_body)
        elif step == "concern":
            await self._handle_health_concern_input(phone, session, text_body)

    # ── Utility: Find Existing Lead by Phone ─────────────────────────────────

    async def _find_lead_by_phone(self, phone: str) -> Optional[Dict]:
        try:
            leads = await erp_bridge_service.list_leads(limit=50)
            clean_input = phone.replace("+", "").replace(" ", "").replace("-", "")
            for lead in leads:
                mobile = str(lead.get("mobile_number") or "").replace("+", "").replace(" ", "").replace("-", "")
                if mobile and (clean_input in mobile or mobile in clean_input):
                    return lead
        except Exception as e:
            logger.error("Error searching ERPNext lead by phone %s: %s", phone, e)
        return None


bot_engine = WhatsAppBotEngine()
