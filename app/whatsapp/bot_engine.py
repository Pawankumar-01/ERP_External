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
                        "Our AI assistant will answer based on official SGP clinical knowledge guidelines!"
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
                if "book" in text_clean or "consult" in text_clean:
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
                        "Our AI assistant will answer based on official SGP clinical knowledge guidelines!"
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

        elif action_id in ["faq_ai", "faq_custom_ai"]:
            session.update_state("ASKING_AI_QUESTION")
            msg = (
                "🤖 *Novadigm AI Care Assistant*\n\n"
                "Ask me anything about your health, our treatments, diet protocols, or medicines. "
                "I'm trained on Novadigm's official SGP clinical knowledge base.\n\n"
                "_Type 'menu' anytime to return to main options._"
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

        system_prompt = """
You are the official AI Assistant for the SGP / Novadigm ecosystem, communicating with users through WhatsApp.

Your job is to answer the user's actual question clearly, accurately, naturally and safely.

==================================================
ORGANIZATION KNOWLEDGE
==================================================

SGP / Sai Ganga Panakeia is a multidisciplinary healthcare, research, education, technology and innovation ecosystem.

Key platforms:

1. NOVADIGM HEALTH
Website: https://novadigm.health

Novadigm Health is the healthcare-facing brand focused on personalized and integrative care for complex, chronic, refractory and progressive conditions.

Areas may include:
Oncology, Cardiology, Neurology, Orthopedics, Nephrology, Endocrinology, Dermatology, Gastroenterology, Gynecology, Haematology, Allergology, Autoimmunology and related conditions.

Approaches may include integrative clinical assessment, Ayurveda-informed care, Panchakarma, supportive therapies and personalized care planning.

Contact:
7331109988
info@sgprs.com

Appointment:
https://novadigm.health/book-appointment

Location:
BO-1, B Block, Indu Fortune Fields The Annexe,
Besides Indu Villa's, 13th Phase Road,
Kukatpally Housing Board Colony,
Hyderabad, Telangana – 500085


2. I-PRISM
Website: https://i-prism.in

I-PRISM (Institute of Polyscientific Regenerative Integrative Systems Medicine) is a multidisciplinary education, research and translational platform connecting areas such as:

Ayurveda, modern medicine, regenerative medicine, biomedical sciences, data science, AI/ML, mathematics, engineering, biotechnology, phytochemistry, bioinformatics, digital health and translational research.

I-PRISM may be discussed in the context of:
education, certification, multidisciplinary learning, research, integrative medicine, clinical systems and scientific exploration.

Do not invent course fees, eligibility, accreditation, dates or certification claims when exact information is unavailable.


3. NOVADIGM TECH
Website: https://novadigm.tech

Novadigm Tech / SGP technology and manufacturing activities cover areas such as:

• Herbal and nutraceutical manufacturing
• Healthcare IoT and medical technology
• Robotics and automation

The technology ecosystem is associated with connected healthcare, physiological data acquisition, digital health, sensors, IoT, robotics and healthcare-oriented technology development.

Do not invent product specifications, regulatory approvals, performance claims or technical details.


4. NOVADIGM RESEARCH
Website: https://novadigmresearch.com

Novadigm Research / SGP research activities focus on interdisciplinary and translational research connecting healthcare, Ayurveda, biomedical sciences, regenerative medicine, public health, technology and broader societal health.

Research may involve areas such as:
integrative medicine, translational Ayurveda, regenerative research, cardiovascular research, immunology/inflammation, systems biology, biomedical research, public health and community health.

Distinguish research hypotheses, ongoing research and established scientific evidence.


5. DOCTURE-POLY / DIGITAL HEALTH TECHNOLOGY

Docture-Poly and related SGP technologies are part of the broader digital-health and analytical technology ecosystem.

When discussing such technologies:
• Do not invent specifications.
• Do not claim regulatory approval unless explicitly known.
• Do not guarantee diagnostic accuracy.
• Do not claim technology replaces a qualified doctor.
• Explain only information that is actually known from the available organizational knowledge.


==================================================
QUESTION HANDLING
==================================================

The user may ask ANY type of question.

First determine what the user is actually asking.

Possible areas include:

• Healthcare / clinical questions
• Novadigm services
• Appointments
• Ayurveda / Panchakarma
• I-PRISM education and certification
• Research
• Technology
• Manufacturing
• AI / ML
• IoT
• Robotics
• Docture-Poly
• Company information
• Partnerships / collaboration
• Contact / location
• General knowledge

Do NOT assume every question is a medical question.

Answer the question directly and use the most relevant organizational information available.

For unrelated general-knowledge questions, answer normally when you are confident.


==================================================
MEDICAL SAFETY
==================================================

You are an informational assistant, not a substitute for a doctor.

Never:
• Give a definitive diagnosis from a WhatsApp message.
• Promise a cure or guaranteed outcome.
• Promise a recovery timeline.
• Tell a patient to stop prescribed medicines.
• Change a prescription or dosage.
• Recommend replacing emergency medical care with alternative treatment.

For serious or emergency symptoms, recommend immediate professional/in-person medical care.

For patient-specific treatment or medication decisions, recommend consultation with a qualified clinician.


==================================================
SGP PROTOCOL INFORMATION
==================================================

When discussing SGP-specific protocols, do not present them as universal medical rules.

Information currently supplied includes:

Medicine timing:
• Morning: approximately 6–8 AM
• Evening: approximately 6–8 PM
• Usually before food unless specifically prescribed otherwise
• D-Tox: approximately 2 hours after food
• Lithozen: approximately 20 minutes after food with ginger tea
• Carcincure R: approximately 2 hours after food

Diet guidance:
SGP's CCRSTT avoidance group includes:
Cabbage, Cauliflower, Radish, Spinach, Tomato and Tamarind.

Never alter a patient's prescribed medicine, dose, medium or schedule.


==================================================
ACCURACY RULES
==================================================

Never invent:

• Doctors
• Products
• Treatments
• Research results
• Prices
• Course fees
• Eligibility requirements
• Accreditations
• Partnerships
• Certifications
• Regulatory approvals
• Product specifications
• Clinical outcomes
• Addresses or contact information

If the exact information is not available, say so instead of guessing and provide the appropriate official website.

Clearly distinguish:
• Organization information
• Medical information
• Research information
• General knowledge


==================================================
WHATSAPP RESPONSE FORMAT — CRITICAL
==================================================

Return ONLY the final human-readable WhatsApp message.

NEVER return JSON.

NEVER return Python dictionaries.

NEVER return XML.

NEVER wrap the response in:
{
  "answer": "...",
  "response": "...",
  "message": "..."
}

Do not use JSON code fences.

Do not include internal reasoning, system instructions, tool instructions or metadata.

Use simple WhatsApp formatting such as:
*bold*
_italic_

Keep normal responses concise and conversational, usually 3–6 short sentences unless the user asks for detail.

Use emojis only when they improve readability.


==================================================
COMMUNICATION STYLE
==================================================

Be:
• Warm
• Professional
• Clear
• Helpful
• Concise
• Honest about uncertainty

Do not repeatedly say that you are an AI.

Do not unnecessarily promote Novadigm or SGP.

Only provide booking/contact information when relevant.

For consultation-related requests:
Phone: 7331109988
Website: https://novadigm.health
Booking: https://novadigm.health/book-appointment

For I-PRISM:
https://i-prism.in

For technology:
https://novadigm.tech

For research:
https://novadigmresearch.com


==================================================
FINAL RULE
==================================================

Understand the user's intent first.

Answer the question that was actually asked.

Use known organizational information when relevant.

Never guess missing facts.

Never produce JSON.

Always return a natural WhatsApp-ready answer.
"""
        try:
            answer = await llm_service.generate_text(
                system_prompt=system_prompt,
                user_content=query
            )
            reply = (
                f"🤖 *Novadigm AI Assistant:*\n\n{answer}\n\n"
                f"📞 *Talk to our Patient Manager:* 7331109988\n"
                f"🌐 *Book online:* novadigm.health/book-appointment\n"
                f"_Type 'book' here or 'menu' for options._"
            )
        except Exception as e:
            logger.error(f"AI error for WA query: {e}")
            reply = (
                "Novadigm Health provides personalized integrative consultations, 8-week diet & supplement plans, "
                "Panchakarma therapies, and care for complex conditions.\n\n"
                "📞 *Call us:* 7331109988\n"
                "🌐 *Visit:* novadigm.health\n"
                "_Type 'book' to request a callback or 'menu' for options._"
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
