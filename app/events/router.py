"""
Events Router — Query Audit Logs
Read-only endpoints for accessing the event log.
Useful for compliance audits and debugging.
"""

from typing import List, Optional
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config.database import get_db
from app.events.logger import EventLog, EventLogResponse

router = APIRouter()


@router.get("/", response_model=List[EventLogResponse])
async def list_events(
    entity_type: Optional[str] = Query(None, description="Filter by entity type e.g. lead, orientation_session"),
    entity_id: Optional[str] = Query(None, description="Filter by specific entity ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type e.g. lead_created"),
    limit: int = Query(100, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """
    Query the structured audit event log.
    All system actions are captured here for compliance and debugging.
    """
    query = select(EventLog).order_by(EventLog.timestamp.desc()).limit(limit)

    if entity_type:
        query = query.where(EventLog.entity_type == entity_type)
    if entity_id:
        query = query.where(EventLog.entity_id == entity_id)
    if event_type:
        query = query.where(EventLog.event_type == event_type)

    result = await db.execute(query)
    events = result.scalars().all()

    output = []
    for e in events:
        output.append(EventLogResponse(
            id=e.id,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            event_type=e.event_type,
            payload=json.loads(e.payload) if e.payload else None,
            triggered_by=e.triggered_by,
            timestamp=e.timestamp,
        ))
    return output