"""
Orientation Service — Business Logic
──────────────────────────────────────
Manages session lifecycle, LiveKit rooms, participant tokens,
and attendance calculation.

Architecture notes:
  - OrientationSession + OrientationParticipant are stored locally in PostgreSQL.
    These are analytics/operational tables — not CRM data.
  - When a session is created, it is ALSO mirrored to ERPNext via ERP Bridge.
  - When attendance threshold is met, ERP Bridge creates the attendance record
    and LeadService updates the SGP Lead status in ERPNext.
  - LeadService no longer accepts a db session — it talks to ERP directly.
"""

from datetime import datetime, timezone
from typing import List, Optional
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.orientation.models import (
    OrientationSession,
    OrientationParticipant,
    SessionCreate,
    SessionStatus,
    AttendanceStatus,
    AddParticipantRequest,
)
from app.livekit.client import livekit_client
from app.events.logger import event_logger, EventType
from app.config.settings import settings

logger = logging.getLogger(__name__)


class OrientationService:

    # ── Session CRUD ──────────────────────────────────────────────────────────

    async def create_session(
        self, db: AsyncSession, data: "SessionCreate"
    ) -> OrientationSession:
        """
        1. Generate session ID + LiveKit room name.
        2. Provision room on LiveKit Cloud.
        3. Persist session record to local PostgreSQL.
        4. Mirror session to ERPNext (non-blocking).
        5. If lead_ids provided: auto-register all leads as participants and mark
           each lead ORIENTATION_SCHEDULED in ERPNext (patient manager batch flow).
        """
        import uuid
        from app.erp_bridge.service import erp_bridge_service

        session_id = str(uuid.uuid4())
        room_name  = f"orientation-{session_id[:8]}"

        # Provision LiveKit room
        await livekit_client.create_room(room_name)
        scheduled_at_dt = None
        if data.scheduled_at:
            if isinstance(data.scheduled_at, str):
                scheduled_at_dt = datetime.fromisoformat(data.scheduled_at)
            else:
                scheduled_at_dt = data.scheduled_at

        # Persist locally (analytics/operational data)
        session = OrientationSession(
            id=session_id,
            title=data.title,
            livekit_room_name=room_name,
            scheduled_at=scheduled_at_dt,
        )
        db.add(session)
        await db.flush()
        await db.refresh(session, ["participants"])

        # Mirror to ERPNext (non-blocking)
        try:
            await erp_bridge_service.create_orientation_session(
                session_id=session.id,
                title=session.title,
                scheduled_at=(
                    session.scheduled_at.isoformat() if session.scheduled_at else None
                ),
                status="Scheduled",
            )
        except Exception as e:
            logger.warning(f"ERP mirror for session {session_id} failed (non-critical): {e}")

        await event_logger.log(
            entity_type="orientation_session",
            entity_id=session.id,
            event_type=EventType.ORIENTATION_SESSION_CREATED,
            payload={"title": session.title, "room": room_name},
            triggered_by="api",
        )
        logger.info(f"Orientation session created: {session.id} | room: {room_name}")

        # ── Pre-register leads if provided (patient manager batch scheduling) ──
        lead_ids = getattr(data, "lead_ids", []) or []
        for lead_id in lead_ids:
            try:
                identity = f"{lead_id}:Patient"
                participant = OrientationParticipant(
                    session_id=session_id,
                    lead_id=lead_id,
                    livekit_identity=identity,
                )
                db.add(participant)
                await db.flush()

                # Mark lead as ORIENTATION_SCHEDULED in ERPNext
                await erp_bridge_service.update_lead_orientation_scheduled(
                    lead_id=lead_id,
                    session_title=session.title,
                )

                await event_logger.log(
                    entity_type="lead",
                    entity_id=lead_id,
                    event_type=EventType.ORIENTATION_SCHEDULED,
                    payload={"session_id": session_id, "session_title": session.title},
                    triggered_by="api",
                )
                logger.info(f"Lead {lead_id} pre-registered + marked ORIENTATION_SCHEDULED")
            except Exception as e:
                logger.error(
                    f"Failed to pre-register lead {lead_id} in session {session_id}: {e}"
                )

        return session

    async def get_session(
        self, db: AsyncSession, session_id: str
    ) -> Optional[OrientationSession]:
        result = await db.execute(
            select(OrientationSession)
            .where(OrientationSession.id == session_id)
            .options(selectinload(OrientationSession.participants))
        )
        return result.scalar_one_or_none()

    async def list_sessions(self, db: AsyncSession) -> List[OrientationSession]:
        result = await db.execute(
            select(OrientationSession)
            .order_by(OrientationSession.created_at.desc())
            .options(selectinload(OrientationSession.participants))
        )
        return list(result.scalars().all())

    # ── Participant Management ────────────────────────────────────────────────

    async def add_participant(
        self, db: AsyncSession, session_id: str, req: AddParticipantRequest
    ) -> OrientationParticipant:
        """Register a lead as a participant. Lead ID comes from ERPNext."""
        session = await self.get_session(db, session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        identity = f"{req.lead_id}:{req.lead_name.replace(' ', '_')}"
        participant = OrientationParticipant(
            session_id=session_id,
            lead_id=req.lead_id,
            livekit_identity=identity,
        )
        db.add(participant)
        await db.flush()

        await event_logger.log(
            entity_type="orientation_participant",
            entity_id=participant.id,
            event_type=EventType.ORIENTATION_PARTICIPANT_REGISTERED,
            payload={"lead_id": req.lead_id, "session_id": session_id},
            triggered_by="api",
        )
        return participant

    async def generate_token(
        self, db: AsyncSession, session_id: str, lead_id: str, lead_name: str,
        mobile: str = None,
    ) -> dict:
        """
        Generate a LiveKit JWT for a patient participant.

        Security checks (in order):
          1. Session exists
          2. lead_id exists in ERPNext as a real SGP Lead
          3. If mobile provided → must match ERPNext mobile_number (identity proof)
          4. Lead status must allow orientation (not already CONVERTED etc.)
          5. Auto-register as participant if not already registered
        """
        from app.erp_bridge.service import erp_bridge_service

        session = await self.get_session(db, session_id)
        if not session:
            raise ValueError("Session not found. Please check your link.")

        # 1. Validate lead exists in ERPNext
        erp_lead = await erp_bridge_service.get_lead(lead_id)
        if not erp_lead:
            raise ValueError(
                "Patient ID not found. Please contact the clinic to verify your registration."
            )

        # 2. Verify mobile number if provided (primary identity proof)
        if mobile:
            erp_mobile = (erp_lead.get("mobile_number") or "").strip()
            submitted_mobile = mobile.strip().replace(" ", "").replace("-", "")
            erp_mobile_clean = erp_mobile.replace(" ", "").replace("-", "")
            # Check last 10 digits to handle country code variations
            if erp_mobile_clean[-10:] != submitted_mobile[-10:]:
                raise ValueError(
                    "Mobile number does not match our records. "
                    "Please contact the clinic for assistance."
                )

        # 3. Check lead status allows orientation
        lead_status = erp_lead.get("status", "")
        blocked_statuses = ["CONVERTED", "DORMANT"]
        if lead_status in blocked_statuses:
            raise ValueError(
                f"Your account status ({lead_status}) does not allow joining an orientation. "
                "Please contact the clinic."
            )

        # 4. Use ERPNext lead_name as authoritative display name
        verified_name = erp_lead.get("lead_name") or lead_name

        # 5. Auto-register as participant if not already registered
        existing = await self._get_participant(db, session.id, lead_id)
        if not existing:
            from app.orientation.models import AddParticipantRequest
            req = AddParticipantRequest(lead_id=lead_id, lead_name=verified_name)
            await self.add_participant(db, session_id, req)
            await db.commit()

        identity = f"{lead_id}:{verified_name.replace(' ', '_')}"
        token = livekit_client.generate_token(
            room_name=session.livekit_room_name,
            identity=identity,
            display_name=verified_name,
            is_host=False,
        )
        return {
            "token":       token,
            "livekit_url": settings.LIVEKIT_URL,
            "room_name":   session.livekit_room_name,
            "identity":    identity,
            "session_id":  session_id,
            "lead_name":   verified_name,
        }

    async def generate_host_token(
        self, db: AsyncSession, session_id: str, host_name: str
    ) -> dict:
        """Generate a privileged host JWT for the doctor/presenter."""
        session = await self.get_session(db, session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        identity = f"host:{host_name.replace(' ', '_')}"
        token = livekit_client.generate_token(
            room_name=session.livekit_room_name,
            identity=identity,
            display_name=f"🩺 {host_name}",
            is_host=True,
        )
        return {
            "token":      token,
            "livekit_url": settings.LIVEKIT_URL,
            "room_name":  session.livekit_room_name,
            "identity":   identity,
            "session_id": session_id,
        }

    # ── Session Lifecycle ─────────────────────────────────────────────────────

    async def start_session(
        self, db: AsyncSession, session_id: str
    ) -> OrientationSession:
        session = await self.get_session(db, session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.status != SessionStatus.SCHEDULED:
            raise ValueError(f"Session is already {session.status}")

        session.status     = SessionStatus.LIVE
        session.started_at = datetime.now(timezone.utc)
        await db.flush()

        # Mirror status update to ERP — use ERPNext-valid status value
        try:
            from app.erp_bridge.service import erp_bridge_service
            await erp_bridge_service.update_orientation_session_status(
                session_id, "On Going"
            )
        except Exception as e:
            logger.warning(f"ERP session status update failed (non-critical): {e}")

        await event_logger.log(
            entity_type="orientation_session",
            entity_id=session_id,
            event_type=EventType.ORIENTATION_SESSION_STARTED,
            payload={"started_at": session.started_at.isoformat()},
            triggered_by="api",
        )
        logger.info(f"Session {session_id} started")
        return session

    async def end_session(
        self, db: AsyncSession, session_id: str
    ) -> OrientationSession:
        """
        End session, compute final attendance for all participants,
        and trigger completion flow for anyone who crossed the threshold.
        """
        session = await self.get_session(db, session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.status != SessionStatus.LIVE:
            raise ValueError(f"Session is not live (current: {session.status})")

        now = datetime.now(timezone.utc)
        session.status   = SessionStatus.ENDED
        session.ended_at = now

        if session.started_at:
            session.duration_seconds = int(
                (now - session.started_at).total_seconds()
            )
        await db.flush()

        await self._finalize_attendance(db, session)

        # Mirror to ERP (non-blocking) — use ERPNext-valid status value
        try:
            from app.erp_bridge.service import erp_bridge_service
            await erp_bridge_service.update_orientation_session_status(
                session_id, "Completed"
            )
        except Exception as e:
            logger.warning(f"ERP session end mirror failed (non-critical): {e}")

        await event_logger.log(
            entity_type="orientation_session",
            entity_id=session_id,
            event_type=EventType.ORIENTATION_SESSION_ENDED,
            payload={
                "ended_at":        now.isoformat(),
                "duration_seconds": session.duration_seconds,
            },
            triggered_by="api",
        )
        logger.info(
            f"Session {session_id} ended. Duration: {session.duration_seconds}s"
        )
        return session

    async def end_session_by_room(self, db: AsyncSession, room_name: str) -> None:
        """Called by LiveKit room_finished webhook to finalize attendance."""
        session = await self._get_session_by_room(db, room_name)
        if not session:
            logger.warning(f"room_finished: no session found for room {room_name}")
            return
        try:
            await self.end_session(db, session.id)
        except Exception as e:
            logger.warning(f"end_session_by_room: {e} (may already be ended)")

    # ── Attendance Processing (called by LiveKit webhooks) ────────────────────

    async def record_join(
        self, db: AsyncSession, room_name: str, identity: str
    ) -> None:
        """Called by LiveKit webhook on participant_joined."""
        session = await self._get_session_by_room(db, room_name)
        if not session:
            logger.warning(f"Webhook: no session for room {room_name}")
            return

        lead_id = identity.split(":")[0] if ":" in identity else identity
        if lead_id.startswith("host"):
            return  # Hosts are not tracked for attendance

        participant = await self._get_participant(db, session.id, lead_id)
        if not participant:
            logger.warning(
                f"Webhook: lead {lead_id} not registered in session {session.id}"
            )
            return

        participant.join_time        = datetime.now(timezone.utc)
        participant.attendance_status = AttendanceStatus.JOINED
        await db.flush()

        await event_logger.log(
            entity_type="orientation_participant",
            entity_id=participant.id,
            event_type=EventType.ORIENTATION_PARTICIPANT_JOINED,
            payload={"lead_id": lead_id, "session_id": session.id, "room": room_name},
            triggered_by="livekit_webhook",
        )

    async def record_leave(
        self, db: AsyncSession, room_name: str, identity: str
    ) -> None:
        """
        Called by LiveKit webhook on participant_left.
        Calculates watch time and applies the 70% completion rule.
        """
        session = await self._get_session_by_room(db, room_name)
        if not session:
            return

        lead_id = identity.split(":")[0] if ":" in identity else identity
        if lead_id.startswith("host"):
            return

        participant = await self._get_participant(db, session.id, lead_id)
        if not participant or not participant.join_time:
            return

        now           = datetime.now(timezone.utc)
        participant.leave_time = now
        watch_seconds = int((now - participant.join_time).total_seconds())
        participant.watch_seconds += watch_seconds

        # Percentage of session attended
        if session.duration_seconds and session.duration_seconds > 0:
            participant.watch_percentage = min(
                participant.watch_seconds / session.duration_seconds, 1.0
            )
        elif session.started_at:
            elapsed = int((now - session.started_at).total_seconds())
            if elapsed > 0:
                participant.watch_percentage = min(
                    participant.watch_seconds / elapsed, 1.0
                )

        threshold = settings.ORIENTATION_COMPLETION_THRESHOLD
        completed = participant.watch_percentage >= threshold
        participant.attendance_status = (
            AttendanceStatus.COMPLETED if completed else AttendanceStatus.JOINED
        )
        await db.flush()

        await event_logger.log(
            entity_type="orientation_participant",
            entity_id=participant.id,
            event_type=EventType.ORIENTATION_PARTICIPANT_LEFT,
            payload={
                "lead_id":          lead_id,
                "watch_seconds":    participant.watch_seconds,
                "watch_percentage": round(participant.watch_percentage * 100, 1),
                "completed":        completed,
            },
            triggered_by="livekit_webhook",
        )

        if completed:
            await self._trigger_completion(db, participant, session)

    # ── Private: Finalize at session end ─────────────────────────────────────

    async def _finalize_attendance(
        self, db: AsyncSession, session: OrientationSession
    ) -> None:
        """
        At session end, handle participants still marked JOINED —
        they stayed until the room closed without a leave event.
        """
        result = await db.execute(
            select(OrientationParticipant)
            .where(OrientationParticipant.session_id == session.id)
            .where(
                OrientationParticipant.attendance_status == AttendanceStatus.JOINED
            )
        )
        for participant in result.scalars().all():
            if participant.join_time and session.duration_seconds:
                now   = datetime.now(timezone.utc)
                extra = int((now - participant.join_time).total_seconds())
                participant.watch_seconds    += extra
                participant.watch_percentage  = min(
                    participant.watch_seconds / session.duration_seconds, 1.0
                )
                if (
                    participant.watch_percentage
                    >= settings.ORIENTATION_COMPLETION_THRESHOLD
                ):
                    participant.attendance_status = AttendanceStatus.COMPLETED
                    await self._trigger_completion(db, participant, session)
                else:
                    participant.attendance_status = AttendanceStatus.PARTIAL

        await db.flush()

    # ── Private: Completion trigger ───────────────────────────────────────────

    async def _trigger_completion(
        self,
        db: AsyncSession,
        participant: OrientationParticipant,
        session: OrientationSession,
    ) -> None:
        """
        Called when a participant crosses the attendance threshold.

        Flow:
          1. Create SGP Orientation Attendance in ERPNext.
          2. Update SGP Lead status → ORIENTATION_ATTENDED in ERPNext.
          3. Auto-promote Lead → Patient in ERPNext (get_or_create_patient).
          4. Emit orientation_completed event.

        NOTE:
          - lead_service.mark_orientation_attended() does NOT take a db session.
            It talks to ERPNext directly via ERP Bridge.
          - get_or_create_patient() is idempotent — calling it again from the
            assessment submission or appointment scheduling is always safe.
          - All ERP calls are wrapped in try/except — a failing ERP call must
            never prevent local attendance data from being saved.
        """
        from app.leads.service import lead_service
        from app.erp_bridge.service import erp_bridge_service

        # 1. Create attendance record in ERPNext
        try:
            await erp_bridge_service.create_orientation_attendance(
                lead_id=participant.lead_id,
                session_id=session.id,
                attendance_status="Present",
                watch_time=participant.watch_seconds,
                joined_at=participant.join_time,
                left_at=participant.leave_time,
            )
        except Exception as e:
            logger.error(
                f"ERP attendance creation failed for lead {participant.lead_id}: {e}"
            )

        # 2. Update lead status + orientation fields in ERPNext
        try:
            # Pass session ERP name for orientation_session field linkage
            await lead_service.mark_orientation_attended(
                participant.lead_id,
                session_erp_name=session.id,  # ERPNext uses room_name stored as session_id
            )
        except Exception as e:
            logger.error(
                f"ERPNext lead status update failed for {participant.lead_id}: {e}"
            )

        # 3. Auto-promote lead → Patient in ERPNext
        try:
            patient_id = await erp_bridge_service.get_or_create_patient(participant.lead_id)
            if patient_id:
                logger.info(
                    f"✅ Patient '{patient_id}' created/verified in ERPNext "
                    f"for lead {participant.lead_id}"
                )
                await event_logger.log(
                    entity_type="patient",
                    entity_id=patient_id,
                    event_type=EventType.PATIENT_CREATED,
                    payload={
                        "lead_id":    participant.lead_id,
                        "session_id": session.id,
                        "source":     "orientation_completion",
                    },
                    triggered_by="system",
                )
            else:
                logger.warning(
                    f"Patient auto-creation returned None for lead {participant.lead_id} "
                    "(ERP placeholder mode or lead not found — will retry at scheduling)"
                )
        except Exception as e:
            logger.error(
                f"Patient auto-creation failed for lead {participant.lead_id} "
                f"(non-critical — appointment flow will retry): {e}"
            )

        # 4. Emit domain event
        await event_logger.log(
            entity_type="orientation_participant",
            entity_id=participant.id,
            event_type=EventType.ORIENTATION_COMPLETED,
            payload={
                "lead_id":            participant.lead_id,
                "session_id":         session.id,
                "watch_seconds":      participant.watch_seconds,
                "watch_percentage":   round(participant.watch_percentage * 100, 1),
                "appointment_eligible": True,
            },
            triggered_by="system",
        )
        logger.info(
            f"✅ Orientation COMPLETED for lead {participant.lead_id} "
            f"({participant.watch_percentage * 100:.1f}% attended)"
        )

    # ── Private: Query helpers ────────────────────────────────────────────────

    async def _get_session_by_room(
        self, db: AsyncSession, room_name: str
    ) -> Optional[OrientationSession]:
        result = await db.execute(
            select(OrientationSession)
            .where(OrientationSession.livekit_room_name == room_name)
            .options(selectinload(OrientationSession.participants))
        )
        return result.scalar_one_or_none()

    async def _get_participant(
        self, db: AsyncSession, session_id: str, lead_id: str
    ) -> Optional[OrientationParticipant]:
        result = await db.execute(
            select(OrientationParticipant)
            .where(OrientationParticipant.session_id == session_id)
            .where(OrientationParticipant.lead_id == lead_id)
        )
        return result.scalar_one_or_none()


orientation_service = OrientationService()