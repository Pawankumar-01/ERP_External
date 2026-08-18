"""
ERP Bridge Service
──────────────────
Single point of contact between FastAPI and ERPNext REST API.

Rules:
  - All HTTP calls to ERPNext go through this class only.
  - Services NEVER import aiohttp or call HTTP directly.
  - Includes retry logic, timeout, and structured error logging.
  - Runs in placeholder mode when ERP credentials are not configured.

ERPNext REST API format:
    GET    /api/resource/{DocType}           → list
    GET    /api/resource/{DocType}/{id}      → fetch
    POST   /api/resource/{DocType}           → create
    PUT    /api/resource/{DocType}/{id}      → update

Authentication header:
    Authorization: token API_KEY:API_SECRET
"""

import asyncio
import aiohttp
import logging
from typing import Any, Dict, List, Optional

from app.config.settings import settings
from app.events.logger import event_logger, EventType

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
TIMEOUT_SECONDS = 10
MAX_RETRIES     = 3
RETRY_DELAY     = 1.0   # seconds between retries

# ERPNext DocType names
DOCTYPE_LEAD                 = "SGP Lead"
DOCTYPE_ORIENTATION_SESSION  = "SGP Orientation Session"
DOCTYPE_ORIENTATION_ATTEND   = "SGP Orientation Attendance"
DOCTYPE_PATIENT              = "Patient"
DOCTYPE_APPOINTMENT          = "Patient Appointment"
DOCTYPE_ENCOUNTER            = "SGP Encounter"


class ERPBridgeService:
    """
    Async HTTP client for all ERPNext operations.
    Implements retry, timeout, placeholder mode, and structured event logging.
    """

    def __init__(self):
        self._configured: Optional[bool] = None   # lazily determined

    # ── Configuration check ───────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """True only when real ERP credentials are present in settings."""
        if self._configured is None:
            self._configured = bool(
                settings.ERPNEXT_BASE_URL
                and settings.ERPNEXT_API_KEY
                and settings.ERPNEXT_API_KEY not in ("your-erpnext-api-key", "")
                and settings.ERPNEXT_API_SECRET not in ("your-erpnext-api-secret", "")
            )
        return self._configured

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": (
                f"token {settings.ERPNEXT_API_KEY}:{settings.ERPNEXT_API_SECRET}"
            ),
            "Content-Type": "application/json",
            "Accept":        "application/json",
        }

    def _url(self, path: str) -> str:
        base = settings.ERPNEXT_BASE_URL.rstrip("/")
        return f"{base}{path}"

    # ── Core HTTP helpers ─────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        Execute an HTTP request against ERPNext with retry logic.
        Returns parsed JSON dict or None on failure.
        Logs every attempt, success, and failure as structured events.
        """
        if not self.is_configured:
            logger.warning(
                f"[ERP BRIDGE] Placeholder mode — skipping {method} {path} "
                f"(ERPNext not configured)"
            )
            # Return a minimal stub so callers don't crash in dev
            return {"name": "PLACEHOLDER", "_placeholder": True, **(data or {})}

        url = self._url(path)
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        last_exc: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession(headers=self._headers, timeout=timeout) as session:
                    request_kwargs: Dict[str, Any] = {}
                    if data is not None:
                        request_kwargs["json"] = data
                    if params is not None:
                        request_kwargs["params"] = params

                    async with session.request(method, url, **request_kwargs) as resp:
                        body = await resp.json(content_type=None)

                        if resp.status in (200, 201):
                            logger.info(f"[ERP] {method} {path} → {resp.status}")
                            await self._emit_success(method, path, resp.status)
                            return body.get("data", body)

                        # ERPNext encodes errors in the response body
                        err_msg = body.get("exception") or body.get("message") or str(body)
                        logger.error(
                            f"[ERP] {method} {path} → {resp.status} | "
                            f"attempt {attempt}/{MAX_RETRIES} | {err_msg}"
                        )
                        if resp.status in (400, 403, 404, 409, 417, 422):
                            # Non-retryable client errors — raise so caller gets the real reason
                            await self._emit_failure(method, path, resp.status, err_msg)
                            raise RuntimeError(
                                f"ERPNext {resp.status} error: {err_msg}"
                            )

            except RuntimeError:
                # Re-raise explicit ERPNext errors directly to caller
                raise
            except asyncio.TimeoutError:
                last_exc = asyncio.TimeoutError(f"Timeout on {method} {path}")
                logger.warning(
                    f"[ERP] Timeout on attempt {attempt}/{MAX_RETRIES}: {method} {path}"
                )
            except aiohttp.ClientError as exc:
                last_exc = exc
                logger.warning(
                    f"[ERP] ClientError on attempt {attempt}/{MAX_RETRIES}: {exc}"
                )
            except Exception as exc:
                last_exc = exc
                logger.error(f"[ERP] Unexpected error on attempt {attempt}: {exc}")
                break   # Don't retry unexpected errors

            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)

        await self._emit_failure(method, path, 0, str(last_exc))
        raise RuntimeError(f"ERPNext request failed on {method} {path}: {last_exc}")

    async def _emit_success(self, method: str, path: str, status: int) -> None:
        await event_logger.log(
            entity_type="erp_bridge",
            entity_id=path,
            event_type=EventType.ERP_CALL_SUCCESS,
            payload={"method": method, "path": path, "status": status},
            triggered_by="erp_bridge",
        )

    async def _emit_failure(
        self, method: str, path: str, status: int, error: str
    ) -> None:
        await event_logger.log(
            entity_type="erp_bridge",
            entity_id=path,
            event_type=EventType.ERP_CALL_FAILED,
            payload={"method": method, "path": path, "status": status, "error": error},
            triggered_by="erp_bridge",
        )

    # ── Lead operations ───────────────────────────────────────────────────────

    async def create_lead(self, data) -> Optional[Dict]:
        """
        POST /api/resource/SGP Lead
        Maps LeadCreate fields to SGP Lead DocType fields.

        Note: Frappe POST response sometimes echoes back input rather than
        the created doc. We use the returned 'name' to do a follow-up GET
        to guarantee we return the real created document with correct ID.
        """
        payload = {
            "lead_name":     data.name,
            "mobile_number": data.phone,
            "email_id":      data.email,
            "lead_source":   data.lead_source,
            "interested_in": data.interested_in,
            "notes":         data.notes,
            "status":        "NEW",
        }
        result = await self._request("POST", f"/api/resource/{DOCTYPE_LEAD}", data=payload)
        if not result:
            return None
        # Fetch the actual created document to get the real auto-generated name/ID
        created_name = result.get("name")
        if created_name and created_name != "PLACEHOLDER":
            fetched = await self._request("GET", f"/api/resource/{DOCTYPE_LEAD}/{created_name}")
            return fetched if fetched else result
        return result

    async def get_lead(self, lead_id: str) -> Optional[Dict]:
        """GET /api/resource/SGP Lead/{id}"""
        return await self._request("GET", f"/api/resource/{DOCTYPE_LEAD}/{lead_id}")

    async def list_leads(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """
        GET /api/resource/SGP Lead
        Uses ERPNext list API with optional filters.
        """
        params: Dict[str, Any] = {
            "limit_page_length": limit,
            "fields": '["name","lead_name","mobile_number","email_id","status","lead_source","interested_in","notes","creation","modified"]',
        }
        if status:
            params["filters"] = f'[["SGP Lead","status","=","{status}"]]'

        result = await self._request("GET", f"/api/resource/{DOCTYPE_LEAD}", params=params)
        if result is None:
            return []
        # ERPNext list response: {"data": [...]} — already unwrapped by _request
        if isinstance(result, list):
            return result
        return result.get("data", [])

    async def update_lead_status(self, lead_id: str, update_data) -> Optional[Dict]:
        """
        PUT /api/resource/SGP Lead/{id}
        Updates only status and notes fields.
        """
        payload: Dict[str, Any] = {"status": update_data.status}
        if update_data.notes:
            payload["notes"] = update_data.notes
        return await self._request(
            "PUT", f"/api/resource/{DOCTYPE_LEAD}/{lead_id}", data=payload
        )

    # ── Orientation Session operations ────────────────────────────────────────

    async def create_orientation_session(
        self,
        session_id: str,
        title: str,
        scheduled_at: Optional[str],
        status: str = "Scheduled",
    ) -> Optional[Dict]:
        """
        POST /api/resource/SGP Orientation Session
        Mirrors the local session record in ERPNext for reporting.

        Field mapping (FastAPI → ERPNext DocType):
            title        → session_title  (Data, reqd)
            scheduled_at → orientation_date + start_time
            session_id   → room_name (used as external reference)
            status       → status
        """
        from datetime import datetime

        # Parse scheduled_at into date and time components
        orientation_date = None
        start_time = None
        if scheduled_at:
            try:
                dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                orientation_date = dt.strftime("%Y-%m-%d")
                start_time = dt.strftime("%H:%M:%S")
            except Exception:
                orientation_date = scheduled_at[:10] if len(scheduled_at) >= 10 else None

        payload = {
            "session_title":    title,
            "room_name":        session_id,   # store automation ID here for lookup
            "orientation_date": orientation_date,
            "start_time":       start_time,
            "status":           status,
        }
        return await self._request(
            "POST", f"/api/resource/{DOCTYPE_ORIENTATION_SESSION}", data=payload
        )

    async def update_orientation_session_status(
        self, session_id: str, status: str
    ) -> Optional[Dict]:
        """
        PUT /api/resource/SGP Orientation Session/{session_id}
        Valid ERPNext status values: Scheduled, On Going, Completed, Cancelled
        """
        return await self._request(
            "PUT",
            f"/api/resource/{DOCTYPE_ORIENTATION_SESSION}/{session_id}",
            data={"status": status},
        )

    async def update_lead_orientation_scheduled(
        self, lead_id: str, session_title: str
    ) -> Optional[Dict]:
        """
        Mark an SGP Lead as scheduled for orientation.
        Called when patient manager registers leads into a batch session.
        Sets status → ORIENTATION_SCHEDULED so it appears in the orientation funnel.
        """
        result = await self._request(
            "PUT",
            f"/api/resource/{DOCTYPE_LEAD}/{lead_id}",
            data={"status": "ORIENTATION_SCHEDULED"},
        )
        if result:
            logger.info(
                f"[ERP] Lead {lead_id} marked ORIENTATION_SCHEDULED "
                f"for session '{session_title}'"
            )
        return result

    # ── Orientation Attendance operations ─────────────────────────────────────

    async def create_orientation_attendance(
        self,
        lead_id: str,
        session_id: str,
        attendance_status: str,
        watch_time: int,
        joined_at=None,
        left_at=None,
    ) -> bool:
        """
        POST /api/resource/SGP Orientation Attendance
        Field mapping (FastAPI → ERPNext DocType):
            lead_id      → lead        (Link: SGP Lead)
            session_id   → orientation_session (Link: SGP Orientation Session)
            watch_time   → total_watch_minutes (converted from seconds)
            completed    → orientation_completed (Check)
            eligible     → appointment_eligible (Check)
        Note: orientation_session Link requires the ERPNext doc name (e.g. 40ue4r6gsa),
        not the PostgreSQL UUID. Until ERP mirror stores the ERPNext name,
        this field is omitted to avoid LinkValidationError.
        """
        from app.config.settings import settings
        watch_minutes = int(watch_time / 60)
        # min_required_minutes: 70% of a standard 60-min session = 42 mins
        # This is stored on the attendance record for audit purposes
        min_required = int(settings.ORIENTATION_COMPLETION_THRESHOLD * 60)
        payload = {
            "lead":                  lead_id,
            "orientation_completed": 1,
            "appointment_eligible":  1,
            "watch_minutes":         watch_minutes,
            "min_required_minutes":  min_required,
        }
        if joined_at:
            payload["joined_at"] = joined_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(joined_at, "strftime") else str(joined_at)
        if left_at:
            payload["left_at"] = left_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(left_at, "strftime") else str(left_at)
        result = await self._request(
            "POST", f"/api/resource/{DOCTYPE_ORIENTATION_ATTEND}", data=payload
        )
        success = result is not None
        if success:
            await event_logger.log(
                entity_type="erp_bridge",
                entity_id=lead_id,
                event_type=EventType.ERP_ATTENDANCE_CREATED,
                payload=payload,
                triggered_by="erp_bridge",
            )
        return success

    # ── Patient operations ────────────────────────────────────────────────────

    async def get_or_create_patient(self, lead_id: str) -> Optional[str]:
        """
        Idempotent: find the ERPNext Patient linked to this lead, or create one.
        Returns the ERPNext Patient document name (e.g. 'PAT-0001'), or None on failure.

        Field used for the lead → patient link: custom_sgp_lead
        This must exist as a custom field on the ERPNext Patient DocType.

        Safe to call from orientation completion AND appointment scheduling —
        calling it twice will always return the same patient name.
        """
        # ① Search for existing Patient already linked to this lead
        existing = await self._request(
            "GET",
            f"/api/resource/{DOCTYPE_PATIENT}",
            params={
                "filters": f'[["custom_sgp_lead","=","{lead_id}"]]',
                "fields":  '["name","patient_name"]',
                "limit":   "1",
            },
        )
        if existing and isinstance(existing, list) and len(existing) > 0:
            patient_name = existing[0].get("name")
            logger.info(f"[ERP] Existing patient '{patient_name}' found for lead {lead_id}")
            return patient_name

        # ② Fetch lead data so we can populate the Patient record
        lead = await self.get_lead(lead_id)
        if not lead:
            logger.error(f"[ERP] Cannot create Patient — lead {lead_id} not found")
            return None

        lead_name   = lead.get("lead_name") or lead.get("name", "")
        mobile      = lead.get("mobile_number") or lead.get("mobile_no", "")
        email       = lead.get("email_id") or ""

        # Split full name into first_name + last_name
        # ERPNext Patient DocType requires first_name (mandatory)
        # patient_name is auto-calculated from first + last name in ERPNext
        name_parts = lead_name.strip().split(" ", 1)
        first_name = name_parts[0]
        last_name  = name_parts[1] if len(name_parts) > 1 else ""

        # ③ Create new Patient record
        # sex is mandatory in ERPNext Patient DocType.
        # We default to "Prefer not to say" since gender is not collected
        # at lead registration time — staff can update it in ERPNext.
        new_patient = await self._request(
            "POST",
            f"/api/resource/{DOCTYPE_PATIENT}",
            data={
                "first_name":      first_name,
                "last_name":       last_name,
                "sex":             "Prefer not to say",
                "mobile":          mobile,
                "email":           email,
                "custom_sgp_lead": lead_id,    # link back to SGP Lead
                "status":          "Active",
            },
        )

        if not new_patient or new_patient.get("_placeholder"):
            logger.warning(
                f"[ERP] Patient creation returned placeholder/None for lead {lead_id} "
                "(ERP may not be configured)"
            )
            return new_patient.get("name") if new_patient else None

        patient_name = new_patient.get("name")
        logger.info(f"[ERP] Patient '{patient_name}' created from lead {lead_id}")
        if patient_name and mobile:
            try:
                patient_doc = await self._request("GET", f"/api/resource/{DOCTYPE_PATIENT}/{patient_name}")
                if patient_doc and patient_doc.get("customer"):
                    await self._request(
                        "PUT",
                        f"/api/resource/Customer/{patient_doc['customer']}",
                        data={"mobile_no": mobile},
                    )
                    logger.info(f"[ERP] Customer mobile updated for patient '{patient_name}'")
            except Exception as e:
                logger.warning(f"[ERP] Could not sync mobile to customer: {e}")

        return patient_name

    async def create_patient(self, lead_id: str, patient_data: Dict) -> Optional[Dict]:
        """
        POST /api/resource/Patient — low-level create with arbitrary patient_data.
        Prefer get_or_create_patient() for the standard lead → patient promotion flow.
        """
        payload = {
            "doctype":         DOCTYPE_PATIENT,
            "custom_sgp_lead": lead_id,
            **patient_data,
        }
        return await self._request("POST", f"/api/resource/{DOCTYPE_PATIENT}", data=payload)

    async def get_patient(self, erp_patient_id: str) -> Optional[Dict]:
        """GET /api/resource/Patient/{id}"""
        return await self._request("GET", f"/api/resource/{DOCTYPE_PATIENT}/{erp_patient_id}")

    # ── Appointment operations ────────────────────────────────────────────────

    async def create_patient_appointment(
        self, appointment_data: Dict
    ) -> Optional[Dict]:
        """
        POST /api/resource/Patient Appointment
        Creates a doctor appointment for an orientation-eligible lead.
        """
        payload = {"doctype": DOCTYPE_APPOINTMENT, **appointment_data}
        return await self._request(
            "POST", f"/api/resource/{DOCTYPE_APPOINTMENT}", data=payload
        )

    # ── Encounter & Practitioner operations ───────────────────────────────────

    async def get_practitioners(self) -> List[Dict[str, Any]]:
        """
        GET /api/resource/Healthcare Practitioner
        Fetch registered healthcare practitioners from ERPNext.
        """
        params = {
            "fields": '["name", "practitioner_name", "department", "designation", "mobile"]',
            "limit_page_length": 200,
        }
        try:
            res = await self._request("GET", "/api/resource/Healthcare Practitioner", params=params)
            return res if isinstance(res, list) else []
        except Exception as err:
            logger.warning(f"Failed to fetch Healthcare Practitioners from ERPNext: {err}")
            return []

    async def create_encounter(self, encounter_data: Dict) -> Optional[Dict]:
        """
        POST /api/resource/SGP Encounter
        Creates a clinical encounter record for a doctor visit.
        """
        payload = {"doctype": DOCTYPE_ENCOUNTER, **encounter_data}
        return await self._request(
            "POST", f"/api/resource/{DOCTYPE_ENCOUNTER}", data=payload
        )

    async def get_encounter(self, encounter_id: str) -> Optional[Dict]:
        """GET /api/resource/SGP Encounter/{id}"""
        return await self._request(
            "GET", f"/api/resource/{DOCTYPE_ENCOUNTER}/{encounter_id}"
        )

    async def update_encounter_status(
        self, encounter_id: str, status: str
    ) -> Optional[Dict]:
        """PUT /api/resource/SGP Encounter/{id} — advance workflow status."""
        return await self._request(
            "PUT",
            f"/api/resource/{DOCTYPE_ENCOUNTER}/{encounter_id}",
            data={"status": status},
        )

    async def upload_file_to_encounter(
        self,
        encounter_id: str,
        filename: str,
        file_bytes: bytes,
        is_private: int = 0,
    ) -> Optional[Dict]:
        """
        Upload a file attachment to an SGP Encounter doc in Frappe/ERPNext via /api/method/upload_file.
        """
        if not self.is_configured:
            logger.warning(
                f"[ERP BRIDGE] Placeholder mode — skipping file upload '{filename}' to encounter '{encounter_id}'"
            )
            return {"file_url": f"/files/{filename}", "_placeholder": True}

        url = self._url("/api/method/upload_file")
        headers = {
            "Authorization": f"token {settings.ERPNEXT_API_KEY}:{settings.ERPNEXT_API_SECRET}"
        }
        data = aiohttp.FormData()
        data.add_field("file", file_bytes, filename=filename, content_type="image/jpeg")
        data.add_field("doctype", DOCTYPE_ENCOUNTER)
        data.add_field("docname", encounter_id)
        data.add_field("is_private", str(is_private))

        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.post(url, data=data) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status in (200, 201):
                        logger.info(f"[ERP] Successfully attached file '{filename}' to SGP Encounter '{encounter_id}'")
                        return body.get("message", body)
                    else:
                        logger.error(f"[ERP] File upload failed ({resp.status}): {body}")
                        return None
        except Exception as e:
            logger.error(f"[ERP] Exception during file upload to encounter: {e}")
            return None

    async def get_appointment(self, appointment_id: str) -> Optional[Dict]:
        """GET /api/resource/Patient Appointment/{id}"""
        return await self._request(
            "GET", f"/api/resource/{DOCTYPE_APPOINTMENT}/{appointment_id}"
        )

    async def close_appointment(self, appointment_id: str) -> Optional[Dict]:
        """
        Mark a Patient Appointment as Closed after the encounter is finalized.
        Called automatically from casesheet finalize flow.
        """
        result = await self._request(
            "PUT",
            f"/api/resource/{DOCTYPE_APPOINTMENT}/{appointment_id}",
            data={"status": "Closed"},
        )
        if result:
            logger.info(f"[ERP] Appointment {appointment_id} closed after encounter finalize")
        return result

    async def get_payment_for_appointment(
        self, appointment_id: str
    ) -> Optional[Dict]:
        """
        Look up a posted Payment Entry linked to this appointment.
        ERPNext Payment Entry stores reference in 'reference_name'.
        Returns the payment entry dict if found, None if not yet paid.
        """
        result = await self._request(
            "GET",
            "/api/resource/Payment Entry",
            params={
                "filters": (
                    f'[["Payment Entry Reference","reference_name","=","{appointment_id}"]'
                    f',["Payment Entry","docstatus","=","1"]]'
                ),
                "fields": '["name","paid_amount","mode_of_payment","posting_date","party"]',
                "limit": "1",
            },
        )
        if result and isinstance(result, list) and len(result) > 0:
            return result[0]
        return None

    async def get_payment_for_patient(
        self, patient_id: str
    ) -> Optional[Dict]:
        """Look up most recent posted Payment Entry for a patient (walk-in flow)."""
        result = await self._request(
            "GET",
            "/api/resource/Payment Entry",
            params={
                "filters": (
                    f'[["Payment Entry","party","=","{patient_id}"]'
                    f',["Payment Entry","docstatus","=","1"]]'
                ),
                "fields": '["name","paid_amount","mode_of_payment","posting_date","party"]',
                "order_by": "posting_date desc",
                "limit": "1",
            },
        )
        if result and isinstance(result, list) and len(result) > 0:
            return result[0]
        return None


    async def get_patient_by_mobile(self, mobile: str) -> Optional[Dict]:
        """Find patient by mobile number."""
        clean = mobile.strip().replace("+91", "").replace(" ", "").replace("-", "")
        if len(clean) == 10:
            search_nums = [clean, "91" + clean, "+91" + clean]
        else:
            search_nums = [clean]

        for num in search_nums:
            result = await self._request(
                "GET",
                "/api/resource/Patient",
                params={
                    "filters": f'[["Patient","mobile","=","{num}"]]',
                    "fields": '["name","patient_name","mobile","customer","custom_sgp_lead"]',
                    "limit": "1",
                },
            )
            if result and isinstance(result, list) and len(result) > 0:
                return result[0]
        return None

    async def update_encounter_payment(
        self, encounter_id: str, payment_entry_id: str, paid_amount: float
    ) -> Optional[Dict]:
        """
        Link a verified payment entry to an SGP Encounter.
        Stores payment reference and amount on the encounter record.
        Requires custom fields on SGP Encounter: custom_payment_entry, custom_paid_amount.
        """
        return await self._request(
            "PUT",
            f"/api/resource/{DOCTYPE_ENCOUNTER}/{encounter_id}",
            data={
                "custom_payment_entry": payment_entry_id,
                "custom_paid_amount": paid_amount,
            },
        )

    # ── Webhook verification (inbound: ERPNext → FastAPI) ─────────────────────

    def verify_erp_webhook(self, body: bytes, signature_header: str) -> bool:
        """
        Verify that an inbound webhook was sent by our ERPNext instance.
        ERPNext signs with HMAC-SHA256 using ERP_WEBHOOK_SECRET.
        Header: X-Frappe-Webhook-Signature
        Returns True if valid, False otherwise.
        """
        import hashlib
        import hmac as _hmac

        if not settings.ERP_WEBHOOK_SECRET or \
           settings.ERP_WEBHOOK_SECRET == "your-erp-webhook-secret":
            logger.warning(
                "ERP_WEBHOOK_SECRET not configured — skipping signature check (dev mode)"
            )
            return True

        expected = _hmac.new(
            settings.ERP_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        return _hmac.compare_digest(expected, signature_header)


erp_bridge_service = ERPBridgeService()

    
    
