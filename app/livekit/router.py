
from fastapi import APIRouter, Request, HTTPException, Header, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.config.database import get_db
from app.livekit.client import livekit_client
from app.orientation.service import orientation_service
from app.events.logger import event_logger, EventType

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/webhook")
async def livekit_webhook(
    request: Request,
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    body = await request.body()

    try:
        event = livekit_client.verify_webhook(body, authorization or "")
    except ValueError as e:
        logger.warning(f"Rejected unsigned webhook: {e}")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event_name = event.event
    room = event.room
    participant = event.participant

    room_name = room.name if room else None
    identity = participant.identity if participant else None

    logger.info(f"LiveKit event: {event_name} | room={room_name} | identity={identity}")

    if event_name == "participant_joined" and room_name and identity:
        await orientation_service.record_join(db, room_name, identity)

    elif event_name == "participant_left" and room_name and identity:
        await orientation_service.record_leave(db, room_name, identity)

    elif event_name == "room_started" and room_name:
        await event_logger.log(
            entity_type="livekit_room",
            entity_id=room_name,
            event_type=EventType.ROOM_STARTED,
            payload={"room_name": room_name},
            triggered_by="livekit_webhook",
        )

    elif event_name == "room_finished" and room_name:
        await event_logger.log(
            entity_type="livekit_room",
            entity_id=room_name,
            event_type=EventType.ROOM_FINISHED,
            payload={"room_name": room_name},
            triggered_by="livekit_webhook",
        )
        await orientation_service.end_session_by_room(db, room_name)

    return {"status": "ok", "event": event_name}
