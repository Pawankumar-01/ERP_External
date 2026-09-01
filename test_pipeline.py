import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_pipeline")

# Import actual modules
from app.whatsapp.bot_engine import WhatsAppBotEngine, WhatsAppLeadData

class MockWhatsAppService:
    def __init__(self):
        self.sent_messages = []

    async def send_text_message(self, phone: str, text: str):
        logger.info(f"[MOCK WA TXT] -> {phone}: {text[:60]}...")
        self.sent_messages.append({"type": "text", "phone": phone, "text": text})
        return True

    async def send_interactive_buttons(self, phone: str, body_text: str, buttons: list, header_text: str = None, footer_text: str = None):
        logger.info(f"[MOCK WA BTN] -> {phone}: {body_text[:60]}... Buttons: {[b['title'] for b in buttons]}")
        self.sent_messages.append({"type": "buttons", "phone": phone, "body": body_text, "buttons": buttons})
        return True

    async def send_interactive_list(self, phone: str, body_text: str, button_label: str, sections: list, header_text: str = None, footer_text: str = None):
        # Validate Meta Cloud API rules!
        if len(button_label) > 20:
            raise ValueError(f"Button label too long (>20 chars): '{button_label}'")
        for section in sections:
            sec_title = section.get("title", "")
            if len(sec_title) > 24:
                raise ValueError(f"Section title too long (>24 chars): '{sec_title}'")
            for row in section.get("rows", []):
                row_title = row.get("title", "")
                if len(row_title) > 24:
                    raise ValueError(f"Row title too long (>24 chars): '{row_title}'")
                row_desc = row.get("description", "")
                if len(row_desc) > 72:
                    raise ValueError(f"Row description too long (>72 chars): '{row_desc}'")

        logger.info(f"[MOCK WA LIST] -> {phone}: {header_text} - {button_label} (Meta Validation Passed!)")
        self.sent_messages.append({"type": "list", "phone": phone, "sections": sections})
        return True

class MockERPBridgeService:
    def __init__(self):
        self.leads = {}
        self.patients = {}
        self.appointments = {}
        self.counter = 1

    async def list_leads(self, limit: int = 50):
        return list(self.leads.values())

    async def create_lead(self, data: WhatsAppLeadData):
        lead_id = f"SGP-LEAD-2026-{self.counter:04d}"
        self.counter += 1
        lead_doc = {
            "name": lead_id,
            "lead_name": data.name,
            "mobile_number": data.phone,
            "email": data.email,
            "lead_source": data.lead_source,
            "interested_in": data.interested_in,
            "notes": data.notes,
            "status": "NEW"
        }
        self.leads[lead_id] = lead_doc
        logger.info(f"[MOCK ERP] Created SGP Lead {lead_id}: {data.name} ({data.phone})")
        return lead_doc

    async def get_lead(self, lead_id: str):
        return self.leads.get(lead_id)

    async def get_or_create_patient(self, lead_id: str):
        lead = self.leads.get(lead_id)
        if not lead:
            return None
        patient_id = f"PAT-2026-{self.counter:04d}"
        name_parts = lead["lead_name"].split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        
        # Verify email variable safety
        email = lead.get("email") or lead.get("email_id") or ""
        
        patient_doc = {
            "name": patient_id,
            "first_name": first_name,
            "last_name": last_name,
            "mobile": lead["mobile_number"],
            "email": email,
            "sex": "Prefer not to say",
            "custom_sgp_lead": lead_id,
            "status": "Active"
        }
        self.patients[patient_id] = patient_doc
        lead["status"] = "CONVERTED"
        logger.info(f"[MOCK ERP] Promoted SGP Lead {lead_id} -> Patient {patient_id} ({first_name} {last_name})")
        return patient_id

    async def create_patient_appointment(self, data: dict):
        appt_id = f"APPT-2026-{self.counter:04d}"
        self.appointments[appt_id] = data
        logger.info(f"[MOCK ERP] Created Patient Appointment {appt_id} for Patient {data.get('patient')}")
        return {"name": appt_id}

def make_text_payload(phone: str, text: str):
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"profile": {"name": "Bhanu Prakash"}}],
                    "messages": [{
                        "from": phone,
                        "type": "text",
                        "text": {"body": text}
                    }]
                }
            }]
        }]
    }

def make_interactive_payload(phone: str, action_id: str, title: str, int_type: str = "button_reply"):
    reply_key = "button_reply" if int_type == "button_reply" else "list_reply"
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"profile": {"name": "Bhanu Prakash"}}],
                    "messages": [{
                        "from": phone,
                        "type": "interactive",
                        "interactive": {
                            "type": int_type,
                            reply_key: {"id": action_id, "title": title}
                        }
                    }]
                }
            }]
        }]
    }

async def run_full_pipeline_test():
    logger.info("=== STARTING FULL WHATSAPP INTAKE PIPELINE TEST ===")
    
    mock_wa = MockWhatsAppService()
    mock_erp = MockERPBridgeService()
    
    # Patch global services in bot_engine
    import app.whatsapp.bot_engine as bot_mod
    bot_mod.whatsapp_service = mock_wa
    bot_mod.erp_bridge_service = mock_erp

    engine = WhatsAppBotEngine()
    test_phone = "919618080752"

    # Step 1: User sends "hi"
    logger.info("\n--- STEP 1: Main Menu Request ---")
    await engine.handle_webhook_payload(make_text_payload(test_phone, "hi"))
    
    # Step 2: User clicks "📅 Book Appointment"
    logger.info("\n--- STEP 2: Click Book Appointment ---")
    await engine.handle_webhook_payload(make_interactive_payload(test_phone, "btn_book_appt", "📅 Book Appointment"))
    
    # Step 3: User inputs Name "Bhanu Prakash"
    logger.info("\n--- STEP 3: Enter Full Name ---")
    await engine.handle_webhook_payload(make_text_payload(test_phone, "Bhanu Prakash"))
    
    # Step 4: User inputs Health Concern "Abdominal pain"
    logger.info("\n--- STEP 4: Enter Health Concern ---")
    await engine.handle_webhook_payload(make_text_payload(test_phone, "Abdominal pain"))

    # Step 5: User inputs Address & Pincode "H.No 12-3, Jubilee Hills, Hyderabad - 500033"
    logger.info("\n--- STEP 5: Enter Address & Pincode ---")
    await engine.handle_webhook_payload(make_text_payload(test_phone, "H.No 12-3, Jubilee Hills, Hyderabad - 500033"))

    # Step 6: User selects Category "New Consultation"
    logger.info("\n--- STEP 6: Select Category ---")
    await engine.handle_webhook_payload(make_interactive_payload(test_phone, "consult_new", "New Consultation", int_type="list_reply"))

    # Step 7: User selects Slot "Morning (10:00 - 11:30)"
    logger.info("\n--- STEP 7: Select Time Slot ---")
    await engine.handle_webhook_payload(make_interactive_payload(test_phone, "slot_morning_1", "Morning (10:00 - 11:30)", int_type="list_reply"))

    logger.info("\n=== VERIFYING ERPNEXT RECORDS CREATED ===")
    assert len(mock_erp.leads) == 1, "Lead creation failed"
    lead = list(mock_erp.leads.values())[0]
    logger.info(f"Verified Lead Doc: {lead}")
    assert lead["lead_name"] == "Bhanu Prakash"
    assert lead["lead_source"] == "WHATSAPP"
    assert lead["interested_in"] == "CONSULTATION"
    assert lead["status"] == "CONVERTED"
    assert "Abdominal pain" in lead["notes"]
    assert "500033" in lead["notes"]

    assert len(mock_erp.patients) == 1, "Patient creation failed"
    patient = list(mock_erp.patients.values())[0]
    logger.info(f"Verified Patient Doc: {patient}")
    assert patient["first_name"] == "Bhanu"
    assert patient["last_name"] == "Prakash"
    assert patient["mobile"] == test_phone

    assert len(mock_erp.appointments) == 1, "Appointment creation failed"
    appt = list(mock_erp.appointments.values())[0]
    logger.info(f"Verified Appointment Doc: {appt}")
    assert appt["patient"] == patient["name"]

    logger.info("\n🎉 ALL PIPELINE TESTS PASSED SUCCESSFULLY WITH ZERO ERRORS!")

if __name__ == "__main__":
    asyncio.run(run_full_pipeline_test())
