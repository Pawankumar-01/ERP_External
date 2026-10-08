import logging
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any

from app.whatsapp.service import whatsapp_service
from app.whatsapp.state_store import state_store
from app.whatsapp.knowledge import (
    EMERGENCY_MESSAGE,
    ESCALATION_MESSAGE,
    format_knowledge_context,
    is_emergency_query,
    search_knowledge,
)
from app.whatsapp.llm_service import whatsapp_llm_service
from app.whatsapp.docture_poly import (
    DEVICE_ARTICLES,
    device_answer_is_generic_overview,
    device_fallback_answer,
    device_fallback_article_ids,
    device_knowledge_context,
)
from app.erp_bridge.service import erp_bridge_service

logger = logging.getLogger(__name__)

_DOCTURE_POLY_QUERY_TERMS = (
    "docture poly",
    "docture-poly",
    "doctor poly",
    "doctor-poly",
    "vpk42",
    "vpk 42",
    "docturepoly",
)

_DOCTURE_POLY_GENERIC_QUERY_TERMS = (
    "your device",
    "the device",
    "device info",
    "device enquiry",
    "device inquiry",
    "device demo",
    "device price",
    "device cost",
    "device scan",
    "device work",
    "buy device",
    "health device",
    "this machine",
)


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

            # 1. Global Reset / Main Menu commands (Always active)
            if text_clean in ["hi", "hello", "menu", "start", "restart", "help", "main menu", "exit", "cancel"]:
                session.reset()
                await self.send_main_menu(sender_phone)
                return

            # 2. Explicit Interactive Button or List Clicks (Triggered by button action_id)
            if action_id:
                if action_id in ["btn_book_consultation", "btn_book_appt", "consult_new", "consult_followup"]:
                    session.update_state("AWAITING_NAME")
                    await whatsapp_service.send_text_message(
                        sender_phone,
                        "🌿 *Welcome to SGP Healthcare!*\n\n"
                        "Let's get your details for our Patient Manager.\n"
                        "Please reply with your *Full Name*:"
                    )
                    return

                if action_id in ["btn_talk_manager", "btn_support"]:
                    await self._process_manager_callback_request(sender_phone, session)
                    return

                if action_id == "btn_faqs":
                    await self._send_faq_list_menu(sender_phone)
                    return

                if action_id in ["faq_ai", "faq_custom_ai", "faq_ai_assistant"]:
                    session.update_state("ASKING_AI_QUESTION")
                    msg = (
                        "🤖 *SGP AI Care Assistant*\n\n"
                        "Please type your health query or question below. "
                        "Our assistant will answer using approved general Novadigm guidance."
                    )
                    await whatsapp_service.send_text_message(sender_phone, msg)
                    return

                if action_id == "faq_timings":
                    await self._handle_faq_selection(sender_phone, session, "faq_timings")
                    return

                if action_id in ["faq_consultation", "faq_fees"]:
                    await self._handle_faq_selection(sender_phone, session, "faq_consultation")
                    return

                if action_id == "faq_panchakarma":
                    await self._handle_faq_selection(sender_phone, session, "faq_panchakarma")
                    return

                if action_id == "faq_diet_meds":
                    await self._handle_faq_selection(sender_phone, session, "faq_diet_meds")
                    return

                if action_id == "faq_docture_poly":
                    await self._handle_faq_selection(sender_phone, session, "faq_docture_poly")
                    return

                if action_id in ["time_morning", "time_afternoon", "time_evening", "time_anytime", "slot_morning_1", "slot_morning_2", "slot_evening_1"]:
                    await self._handle_preferred_time_selection(sender_phone, session, action_id, text_body)
                    return

            # 3. State-Driven Free-Form Text Router (No action_id or typed text)
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
                # Default MAIN_MENU state text matching
                if (
                    any(term in text_clean for term in _DOCTURE_POLY_QUERY_TERMS)
                    or any(term in text_clean for term in _DOCTURE_POLY_GENERIC_QUERY_TERMS)
                    or text_clean == "device"
                ):
                    session.update_state("ASKING_AI_QUESTION")
                    session.data["knowledge_topic"] = "docture_poly"
                    await self._handle_ai_question(sender_phone, session, text_body)
                elif "book" in text_clean or "consult" in text_clean:
                    session.update_state("AWAITING_NAME")
                    await whatsapp_service.send_text_message(
                        sender_phone,
                        "🌿 *Welcome to SGP Healthcare!*\n\n"
                        "Let's get your details for our Patient Manager.\n"
                        "Please reply with your *Full Name*:"
                    )
                elif "faq" in text_clean or "service" in text_clean:
                    await self._send_faq_list_menu(sender_phone)
                elif "manager" in text_clean or "talk" in text_clean or "specialist" in text_clean or "support" in text_clean or "call" in text_clean:
                    await self._process_manager_callback_request(sender_phone, session)
                elif "ask ai" in text_clean or "ai care" in text_clean:
                    session.update_state("ASKING_AI_QUESTION")
                    msg = (
                        "🤖 *SGP AI Care Assistant*\n\n"
                        "Please type your health query or question below. "
                        "Our assistant will answer using approved general Novadigm guidance."
                    )
                    await whatsapp_service.send_text_message(sender_phone, msg)
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
            f"🌿 *{greeting} to Novadigm Health — by SGP Hospitals*\n\n"
            "Integrative & personalized clinical care for complex and progressive conditions.\n"
            "I am your automated AI care assistant. How can we help you today?"
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
            header_text="Novadigm Health | SGP Hospitals",
            footer_text="🌐 novadigm.health | 📞 7331109988"
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

        patient_name = session.data.get("patient_name", "Valued Patient")
        health_concern = session.data.get("health_concern", "General Consultation")

        lead_notes = (
            f"Created via SGP WhatsApp Bot.\n"
            f"Primary Health Concern: {health_concern}\n"
            f"Location/Address: {location_text.strip()}\n"
            f"Pincode: {pincode}\n"
            f"Action Required: Patient Manager to contact patient and confirm consultation & orientation slot."
        )

        lead_created = None
        try:
            lead_created = await erp_bridge_service.create_lead(
                WhatsAppLeadData(
                    name=patient_name,
                    phone=phone,
                    address=location_text.strip(),
                    pincode=pincode,
                    interested_in="CONSULTATION",
                    notes=lead_notes
                )
            )
            if lead_created:
                lead_id = lead_created.get("name")
                session.data["lead_id"] = lead_id
                logger.info(f"SGP Lead created for WA user {phone}: {lead_id}")
        except Exception as e:
            logger.error(f"Error creating SGP Lead for {phone}: {e}")

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
            body_text="One last step! Select your preferred time for our *Patient Manager* to call you:",
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
        lead_id = session.data.get("lead_id")

        if lead_id:
            updated_notes = (
                f"Created via SGP WhatsApp Bot.\n"
                f"Primary Health Concern: {health_concern}\n"
                f"Location/Address: {address}\n"
                f"Preferred Call Window: {preferred_time}\n"
                f"Action Required: Patient Manager to contact patient and confirm consultation & orientation slot."
            )
            try:
                await erp_bridge_service._request(
                    "PUT",
                    f"/api/resource/SGP Lead/{lead_id}",
                    data={"notes": updated_notes}
                )
                logger.info(f"SGP Lead {lead_id} updated with preferred call window: {preferred_time}")
            except Exception as e:
                logger.warning(f"Could not update lead {lead_id} with preferred time: {e}")

        confirmation_msg = (
            f"✅ *Consultation Request Received!*\n\n"
            f"👤 *Patient Name:* {patient_name}\n"
            f"🩺 *Health Concern:* {health_concern}\n"
            f"📍 *Location:* {address}\n"
            f"🕒 *Preferred Call Window:* {preferred_time}\n\n"
            f"📞 *What happens next?*\n"
            f"Our *Patient Manager* will call you shortly on this number to answer your questions, "
            f"confirm your consultation schedule, and allocate your orientation slot.\n\n"
            f"_Type 'menu' anytime to return to options._"
        )
        await whatsapp_service.send_text_message(phone, confirmation_msg)
        session.reset()

    async def _process_manager_callback_request(self, phone: str, session):
        patient_name = session.data.get("patient_name") or session.data.get("wa_profile_name") or "Valued Patient"

        is_device_enquiry = session.data.get("knowledge_topic") == "docture_poly"
        interested_in = "DEVICE" if is_device_enquiry else "CONSULTATION"
        enquiry_label = "Docture-Poly device enquiry" if is_device_enquiry else "consultation enquiry"

        lead_notes = (
            f"Requested direct callback from Patient Manager via WhatsApp Bot for a {enquiry_label}.\n"
            f"Action Required: Patient Manager to call back patient on priority."
        )

        try:
            await erp_bridge_service.create_lead(
                WhatsAppLeadData(
                    name=patient_name,
                    phone=phone,
                    interested_in=interested_in,
                    notes=lead_notes
                )
            )
            logger.info(f"Registered SGP Lead callback request for {phone}")
        except Exception as e:
            logger.error(f"Error registering SGP Lead callback for {phone}: {e}")

        msg = (
            f"📞 *Patient Manager Callback Requested*\n\n"
            f"Thank you, *{patient_name}*!\n"
            f"Our Patient Manager has been notified about your *{enquiry_label}* and will call "
            f"you shortly on *{phone}* to assist you.\n\n"
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
                        "id": "faq_docture_poly",
                        "title": "Docture-Poly VPK42",
                        "description": "How the device works, outputs, limits & demos"
                    },
                    {
                        "id": "faq_custom_ai",
                        "title": "Ask AI Care Assistant",
                        "description": "Ask for approved general health and service guidance"
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
                "🏥 *Novadigm Health — SGP Hospitals*\n\n"
                "⏰ *Clinic Hours:* Monday – Saturday (9:00 AM – 7:00 PM)\n"
                "📍 *Address:* BO-1, B Block, Indu Fortune Fields The Annexe, Besides Indu Villa's, "
                "13th Phase Rd, Kukatpally Housing Board Colony, Hyderabad, Telangana – 500085\n"
                "📞 *Contact:* 7331109988\n"
                "📧 *Email:* info@sgprs.com\n\n"
                "🌐 *Website:* novadigm.health\n"
                "🔗 *Group:* saigangapanakeia.in\n\n"
                "_Type 'book' to request a consultation, or 'menu' for main options._"
            )
            await whatsapp_service.send_text_message(phone, msg)

        elif action_id in ["faq_consultation", "faq_fees"]:
            msg = (
                "🩺 *Novadigm 3-Step Clinical Journey*\n\n"
                "1️⃣ *Request Consultation:* Share your health details via this bot.\n"
                "2️⃣ *Manager Call:* Our Patient Manager calls you on *7331109988* to confirm your orientation & consultation slot.\n"
                "3️⃣ *Holistic Assessment:* Led by *Dr. Ravishankar Polisetty* — Nadi Pariksha, VPK diagnosis, and your personalized 8-week integrative regimen.\n\n"
                "🌐 *Learn more:* novadigm.health\n"
                "🔖 *Book online:* novadigm.health/book-appointment\n\n"
                "_Type 'book' to get started!_"
            )
            await whatsapp_service.send_text_message(phone, msg)

        elif action_id == "faq_panchakarma":
            msg = (
                "🌿 *Panchakarma & Integrative Therapies*\n\n"
                "Novadigm specializes in authentic Panchakarma & detoxification therapies:\n"
                "• *Basti & Vasthi:* Januvasthi, Kati Vasthi, Greeva Vasthi\n"
                "• *Detox Cleanses:* Nithya & Prathivaara Virechana\n"
                "• *Home Protocols:* Anutailam nasal drops, Steam Inhalation & Gandusham\n"
                "• *Specializations:* Oncology, Cardiology, Neurology, Orthopedics, Nephrology, Endocrinology, Dermatology & more\n\n"
                "🌐 novadigm.health/disease-condition\n\n"
                "_Type 'book' to request a consultation with our Patient Manager._"
            )
            await whatsapp_service.send_text_message(phone, msg)

        elif action_id == "faq_diet_meds":
            msg = (
                "💊 *SGP Diet & Medicine Intake Rules*\n\n"
                "• *Medicine Timings:* Morning (6-8 AM), Evening (6-8 PM) before food unless prescribed.\n"
                "• *Special Intake:* D-Tox (2h after food), Lithozen (20m after food with ginger tea).\n"
                "• *Diet Rule (CCRSTT to avoid):* Avoid Cabbage, Cauliflower, Radish, Spinach, Tomato, Tamarind.\n"
                "• *Recommended Soups:* Barley, Tapioca (Sabu Dana), Rice, and Finger Millet (Ragi).\n"
                "• *Nuts:* 5 Cashews, 5 Almonds, 2 tbsp Groundnuts soaked overnight.\n\n"
                "📖 *More on our protocols:* saigangapanakeia.in/blogs\n\n"
                "_Type 'menu' to return to options._"
            )
            await whatsapp_service.send_text_message(phone, msg)

        elif action_id == "faq_docture_poly":
            session.update_state("ASKING_AI_QUESTION")
            session.data["knowledge_topic"] = "docture_poly"
            msg = (
                "🔵 *Docture-Poly VPK42*\n\n"
                "Docture-Poly is a non-invasive physiological-signal platform "
                "that uses pulse-wave/PPG and HRV-related analysis to create a "
                "VPK42 homeostasis fingerprint for physician-reviewed preventive "
                "and wellness insights. It does *not* independently diagnose or "
                "treat disease and does not replace doctors or standard tests.\n\n"
                "You can ask the AI assistant questions such as:\n"
                "• How does the scan work?\n"
                "• What do Variability, Processing and Kinetics mean?\n"
                "• What does the report contain?\n"
                "• Can it replace a blood test or diagnose disease?\n"
                "• Is the scan non-invasive?\n"
                "• How can I book a demo or ask about price?\n\n"
                "🌐 *Official website:* docture-poly.com\n"
                "📞 *Product enquiries:* 7331109988\n\n"
                "_Reply with any Docture-Poly question, or type 'manager' for "
                "a device-enquiry callback._"
            )
            await whatsapp_service.send_text_message(phone, msg)

        elif action_id in ["faq_ai", "faq_custom_ai"]:
            session.update_state("ASKING_AI_QUESTION")
            msg = (
                "🤖 *Novadigm AI Care Assistant*\n\n"
                "Ask me anything about your health, our treatments, diet protocols, or medicines. "
                "I'll answer using approved general Novadigm guidance and route questions "
                "that require individualized clinical advice.\n\n"
                "_Type 'menu' anytime to return to main options._"
            )
            await whatsapp_service.send_text_message(phone, msg)
        else:
            await self.send_main_menu(phone)

    async def _handle_ai_question(self, phone: str, session, query: str):
        normalized_query = query.lower().strip()
        if normalized_query in {"menu", "back", "exit", "book"}:
            session.reset()
            if normalized_query == "book":
                session.update_state("AWAITING_NAME")
                await whatsapp_service.send_text_message(
                    phone, "Please reply with your *Full Name*:"
                )
            else:
                await self.send_main_menu(phone)
            return

        if normalized_query in {
            "manager", "talk to manager", "call me", "callback", "call back"
        }:
            await self._process_manager_callback_request(phone, session)
            return

        # Emergency routing must remain deterministic and must not wait for a
        # free model or consume an LLM request.
        if is_emergency_query(query):
            await whatsapp_service.send_text_message(phone, EMERGENCY_MESSAGE)
            session.reset()
            return

        if (
            session.data.get("knowledge_topic") == "docture_poly"
            or any(term in normalized_query for term in _DOCTURE_POLY_QUERY_TERMS)
            or any(term in normalized_query for term in _DOCTURE_POLY_GENERIC_QUERY_TERMS)
            or normalized_query == "device"
        ):
            session.data["knowledge_topic"] = "docture_poly"
            await self._handle_docture_poly_question(phone, session, query)
            return

        matches = search_knowledge(query)
        best_match = matches[0] if matches else None

        # High-confidence questions use the approved answer verbatim. This
        # preserves clinical wording and avoids spending scarce free requests.
        confidence_threshold = 9
        if best_match and best_match.score >= confidence_threshold:
            answer = best_match.article.answer
        else:
            await whatsapp_service.send_text_message(
                phone, "⏳ *Checking approved Novadigm guidance...*"
            )
            context = format_knowledge_context(matches)
            try:
                answer = await whatsapp_llm_service.generate_answer(
                    query=query,
                    knowledge_context=context,
                )
            except Exception as exc:
                logger.warning(
                    "WhatsApp answer model unavailable; using approved fallback: %s",
                    exc,
                )
                answer = best_match.article.answer if best_match else ESCALATION_MESSAGE

        reply = (
            f"🤖 *Novadigm Information Assistant:*\n\n{answer}\n\n"
            "_Type 'menu' for options or 'book' to request a consultation._"
        )
        await whatsapp_service.send_text_message(phone, reply)

    async def _handle_docture_poly_question(self, phone: str, session, query: str):
        # Use one model call with all device facts. Product-name keyword scores
        # must never bypass interpretation of a free-text device enquiry.
        history = session.data.get("docture_poly_history", [])
        route = "model"
        article_ids = [article.article_id for article in DEVICE_ARTICLES]
        try:
            answer = await whatsapp_llm_service.generate_answer(
                query=query,
                knowledge_context=device_knowledge_context(),
                topic="docture_poly",
                conversation_history=history,
            )
            if device_answer_is_generic_overview(query, answer):
                raise RuntimeError("Docture-Poly response did not address the requested topic")
        except Exception as exc:
            route = "knowledge_fallback"
            article_ids = device_fallback_article_ids(query)
            logger.warning(
                "Docture-Poly answer attempt failed; using knowledge fallback: %s", exc
            )
            answer = device_fallback_answer(query)

        logger.info(
            "Docture-Poly answer route=%s articles=%s history_messages=%d",
            route,
            article_ids,
            len(history),
        )
        session.data["docture_poly_history"] = (
            history + [
                {"role": "user", "content": query[:1200]},
                {"role": "assistant", "content": answer[:1200]},
            ]
        )[-4:]
        session.update_state("ASKING_AI_QUESTION")
        reply = (
            f"🤖 *Novadigm Information Assistant:*\n\n{answer}\n\n"
            "_Type 'manager' for device enquiries or 'menu' for options._"
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
