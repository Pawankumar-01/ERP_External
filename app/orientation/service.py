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
        self, db: AsyncSession, data: SessionCreate
    ) -> OrientationSession:
        """
        1. Generate session ID + LiveKit room name.
        2. Provision room on LiveKit Cloud.
        3. Persist session record to local PostgreSQL.
        4. Mirror session to ERPNext (non-blocking — failure is logged, not raised).
        """
        import uuid
        from app.erp_bridge.service import erp_bridge_service

        session_id = str(uuid.uuid4())
        room_name  = f"orientation-{session_id[:8]}"

        # Provision LiveKit room
        await livekit_client.create_room(room_name)

        # Persist locally (analytics/operational data)
        session = OrientationSession(
            id=session_id,
            title=data.title,
            livekit_room_name=room_name,
            scheduled_at=data.scheduled_at,
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
                status="Scheduled",   # ERPNext options: Scheduled, On Going, Completed, Cancelled
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
        self, db: AsyncSession, session_id: str, lead_id: str, lead_name: str
    ) -> dict:
        """Generate a LiveKit JWT for a patient participant."""
        session = await self.get_session(db, session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        identity = f"{lead_id}:{lead_name.replace(' ', '_')}"
        token = livekit_client.generate_token(
            room_name=session.livekit_room_name,
            identity=identity,
            display_name=lead_name,
            is_host=False,
        )
        return {
            "token":      token,
            "livekit_url": settings.LIVEKIT_URL,
            "room_name":  session.livekit_room_name,
            "identity":   identity,
            "session_id": session_id,
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
        if session.status == SessionStatus.ENDED:
            return None 
        session.status     = SessionStatus.LIVE
        session.started_at = datetime.now(timezone.utc)
        await db.flush()

        # Mirror status update to ERP (non-blocking)
        try:
            from app.erp_bridge.service import erp_bridge_service
            await erp_bridge_service.update_orientation_session_status(session_id, "LIVE")
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
        session = await self.get_session(db, session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        now = datetime.now(timezone.utc)

        # Set started_at if not already set (first join time as fallback)
        if not session.started_at:
            # Use earliest join_time from participants as session start
            join_times = [p.join_time for p in session.participants if p.join_time]
            session.started_at = min(join_times) if join_times else now

        session.status   = SessionStatus.ENDED
        session.ended_at = now
        session.duration_seconds = int((now - session.started_at).total_seconds())

        await db.flush()
        await self._finalize_attendance(db, session)
        # ... rest stays the same
    
    async def end_session_by_room(self, db: AsyncSession, room_name: str) -> None:
        """Called by LiveKit room_finished webhook."""
        session = await self._get_session_by_room(db, room_name)
        if not session:
            logger.warning(f"room_finished: no session for room {room_name}")
            return
        try:
            await self.end_session(db, session.id)
        except Exception as e:
            logger.error(f"end_session_by_room failed for {room_name}: {e}")

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
          2. Update SGP Lead status to ORIENTATION_COMPLETED in ERPNext.
          3. Emit orientation_completed event.

        NOTE:
          - lead_service.mark_orientation_attended() does NOT take a db session.
            It talks to ERPNext directly via ERP Bridge.
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
            )
        except Exception as e:
            logger.error(
                f"ERP attendance creation failed for lead {participant.lead_id}: {e}"
            )

        # 2. Update lead status in ERPNext
        try:
            await lead_service.mark_orientation_attended(participant.lead_id)
        except Exception as e:
            logger.error(
                f"ERPNext lead status update failed for {participant.lead_id}: {e}"
            )

        # 3. Emit domain event
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