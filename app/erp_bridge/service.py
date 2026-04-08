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
                            # Non-retryable client errors
                            await self._emit_failure(method, path, resp.status, err_msg)
                            return None

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
        return None

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
        """PUT /api/resource/SGP Orientation Session/{session_id}"""
        return await self._request(
            "PUT",
            f"/api/resource/{DOCTYPE_ORIENTATION_SESSION}/{session_id}",
            data={"status": status},
        )

    # ── Orientation Attendance operations ─────────────────────────────────────

    async def create_orientation_attendance(
        self,
        lead_id: str,
        session_id: str,
        attendance_status: str,
        watch_time: int,
    ) -> bool:
        """
        POST /api/resource/SGP Orientation Attendance
        Creates the attendance record in ERPNext after 70% threshold is met.
        """
        payload = {
            "doctype":            DOCTYPE_ORIENTATION_ATTEND,
            "lead_id":            lead_id,
            "orientation_session": session_id,
            "attendance_status":  attendance_status,
            "watch_time_seconds": watch_time,
        }
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

    async def create_patient(self, lead_id: str, patient_data: Dict) -> Optional[Dict]:
        """
        POST /api/resource/Patient
        Converts a qualified lead into an ERPNext Patient record.
        """
        payload = {
            "doctype":  DOCTYPE_PATIENT,
            "sgp_lead": lead_id,
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

    # ── Encounter operations ──────────────────────────────────────────────────

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

    async def get_appointment(self, appointment_id: str) -> Optional[Dict]:
        """GET /api/resource/Patient Appointment/{id}"""
        return await self._request(
            "GET", f"/api/resource/{DOCTYPE_APPOINTMENT}/{appointment_id}"
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