"""
Lead Service
────────────
Thin orchestration layer between the FastAPI router and ERPBridgeService.

Rules:
  - This service NEVER touches the database directly.
  - It NEVER uses SQLAlchemy sessions.
  - Every read/write of lead data goes through erp_bridge_service.
  - This service is responsible for event emission after ERP calls succeed.

Architecture:
    Router → LeadService → ERPBridgeService → ERPNext REST API
"""

from typing import List, Optional
import logging

from app.leads.models import LeadCreate, LeadResponse, LeadStatus, LeadStatusUpdate
from app.events.logger import event_logger, EventType

logger = logging.getLogger(__name__)


class LeadService:
    """
    Orchestrates lead lifecycle via ERPNext.
    Emits domain events for every state change.
    Does not own any database session — all persistence is in ERPNext.
    """

    async def create_lead(self, data: LeadCreate) -> LeadResponse:
        """
        Create a new SGP Lead in ERPNext.
        Emits lead_created event on success.
        Raises ValueError if ERP call fails.
        """
        from app.erp_bridge.service import erp_bridge_service

        erp_data = await erp_bridge_service.create_lead(data)
        if not erp_data:
            raise ValueError("Failed to create lead in ERPNext — check ERP bridge logs.")

        lead = LeadResponse.from_erp(erp_data)

        await event_logger.log(
            entity_type="lead",
            entity_id=lead.id,
            event_type=EventType.LEAD_CREATED,
            payload={"name": lead.name, "phone": lead.phone, "status": lead.status},
            triggered_by="api",
        )
        logger.info(f"Lead created in ERPNext: {lead.id} ({lead.name})")
        return lead

    async def get_lead(self, lead_id: str) -> Optional[LeadResponse]:
        """Fetch a single SGP Lead from ERPNext by document ID."""
        from app.erp_bridge.service import erp_bridge_service

        erp_data = await erp_bridge_service.get_lead(lead_id)
        if not erp_data:
            return None
        return LeadResponse.from_erp(erp_data)

    async def list_leads(
        self,
        status: Optional[LeadStatus] = None,
        limit: int = 100,
    ) -> List[LeadResponse]:
        """
        List SGP Leads from ERPNext.
        Optionally filter by status field.
        """
        from app.erp_bridge.service import erp_bridge_service

        erp_list = await erp_bridge_service.list_leads(status=status, limit=limit)
        return [LeadResponse.from_erp(item) for item in erp_list]

    async def update_status(
        self, lead_id: str, update_data: LeadStatusUpdate
    ) -> LeadResponse:
        """
        Update SGP Lead status in ERPNext.
        Emits lead_status_updated event on success.
        Raises ValueError if lead not found or ERP call fails.
        """
        from app.erp_bridge.service import erp_bridge_service

        # Fetch current state for the event payload
        current = await erp_bridge_service.get_lead(lead_id)
        if not current:
            raise ValueError(f"Lead {lead_id} not found in ERPNext.")
        old_status = current.get("status", "UNKNOWN")

        erp_data = await erp_bridge_service.update_lead_status(lead_id, update_data)
        if not erp_data:
            raise ValueError(f"Failed to update lead {lead_id} status in ERPNext.")

        lead = LeadResponse.from_erp(erp_data)

        await event_logger.log(
            entity_type="lead",
            entity_id=lead_id,
            event_type=EventType.LEAD_STATUS_UPDATED,
            payload={"old_status": old_status, "new_status": update_data.status},
            triggered_by="api",
        )
        logger.info(f"Lead {lead_id} status: {old_status} → {update_data.status}")
        return lead

    async def mark_orientation_attended(
        self, lead_id: str, session_erp_name: str = None
    ) -> LeadResponse:
        """
        Called after orientation completion (70% watch time + quiz submitted).
        Updates ERPNext SGP Lead:
          - status → ORIENTATION_ATTENDED
          - orientation_completed → 1
          - orientation_session → session ERP name (if provided)
        """
        from app.erp_bridge.service import erp_bridge_service

        # Build the update payload
        # NOTE: orientation_session is a Link field — only set it if the session
        # exists in ERPNext (i.e. ERP mirror succeeded). Sending an unknown UUID
        # causes LinkValidationError. For now we update status + checkbox only.
        update_data = {
            "status":                "ORIENTATION_ATTENDED",
            "orientation_completed": 1,
        }
        # Uncomment once ERP session mirror is confirmed working:
        # if session_erp_name:
        #     update_data["orientation_session"] = session_erp_name

        try:
            result = await erp_bridge_service._request(
                "PUT",
                f"/api/resource/SGP Lead/{lead_id}",
                data=update_data,
            )
            if result:
                return LeadResponse.from_erp(result)
        except Exception as e:
            raise RuntimeError(f"Failed to update lead {lead_id} in ERPNext: {e}")

        raise RuntimeError(f"Failed to update lead {lead_id} status in ERPNext.")


lead_service = LeadService()