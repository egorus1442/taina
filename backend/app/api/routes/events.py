from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import get_event, list_events
from app.schemas import EventRead
from app.serializers import serialize_event


router = APIRouter()


@router.get("", response_model=list[EventRead])
def get_events(
    limit: int = Query(default=100, ge=1, le=500),
    city: str | None = None,
    risk_level: str | None = None,
    db: Session = Depends(get_db),
) -> list[EventRead]:
    return [serialize_event(event) for event in list_events(db, limit=limit, city=city, risk_level=risk_level)]


@router.get("/{event_id}", response_model=EventRead)
def get_event_by_id(event_id: int, db: Session = Depends(get_db)) -> EventRead:
    event = get_event(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return serialize_event(event)

