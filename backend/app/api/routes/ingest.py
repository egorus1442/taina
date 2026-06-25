from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import get_source
from app.schemas import EventRead, IngestTestRequest
from app.serializers import serialize_event
from app.services.event_broker import event_broker
from app.services.pipeline import MessagePipeline


router = APIRouter()


@router.post("/test", response_model=EventRead | None)
async def ingest_test(payload: IngestTestRequest, db: Session = Depends(get_db)) -> EventRead | None:
    source = get_source(db, payload.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    pipeline = MessagePipeline(event_broker=event_broker)
    event = await pipeline.process(
        db,
        source=source,
        telegram_message_id=payload.telegram_message_id
        or int(datetime.now(timezone.utc).timestamp() * 1000),
        text=payload.text,
        sender_name=payload.sender_name,
        message_date=datetime.now(timezone.utc),
        raw={"manual": True},
    )
    return serialize_event(event) if event else None
