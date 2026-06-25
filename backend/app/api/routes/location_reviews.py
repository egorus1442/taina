from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import (
    create_geocoding_result,
    get_event,
    get_location_review,
    list_location_reviews,
    reject_location_review,
    resolve_location_review,
)
from app.schemas import EventRead, LocationReviewRejectRequest, LocationReviewResolveRequest
from app.serializers import serialize_event
from app.services.event_broker import event_broker
from app.services.event_clusterer import EventClusterer
from app.services.geocoder import Geocoder
from app.services.geocoding_attempts import GeocodingAttemptBuilder
from app.services.location_resolver import ResolvedLocation


router = APIRouter()


@router.get("")
def get_location_reviews(
    status: str | None = "pending",
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict]:
    reviews = list_location_reviews(db, status=status, limit=limit)
    return [
        {
            "id": review.id,
            "status": review.status,
            "reason": review.reason,
            "original_location_text": review.original_location_text,
            "suggested_query": review.suggested_query,
            "created_at": review.created_at,
            "updated_at": review.updated_at,
            "event": serialize_event(review.event).model_dump(mode="json"),
        }
        for review in reviews
    ]


@router.post("/{review_id}/resolve", response_model=EventRead)
async def resolve_review_location(
    review_id: int,
    payload: LocationReviewResolveRequest,
    db: Session = Depends(get_db),
) -> EventRead:
    review = get_location_review(db, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Location review not found")
    if review.status != "pending":
        raise HTTPException(status_code=409, detail="Location review is already closed")

    city = payload.city or review.event.source.city
    region = payload.region if payload.region is not None else review.event.source.region
    location = ResolvedLocation(
        city=city,
        region=region,
        query=_join(region, city, payload.location_text),
        precision=payload.precision,
        address=payload.location_text,
    )
    attempts = GeocodingAttemptBuilder().build(
        city=city,
        region=region,
        location_text=payload.location_text,
        precision=payload.precision,
    )
    geocoder = Geocoder()
    geocoding_payload = await geocoder.geocode_attempts(attempts)
    if geocoding_payload.get("lat") is None or geocoding_payload.get("lon") is None:
        raise HTTPException(status_code=422, detail="Operator location was not geocoded")

    geocoding_result = create_geocoding_result(db, geocoding_payload)
    event = resolve_location_review(
        db,
        review=review,
        geocoding_result=geocoding_result,
        location={
            "city": location.city,
            "region": location.region,
            "district": None,
            "address": payload.location_text,
            "landmark": None,
            "lat": geocoding_result.lat,
            "lon": geocoding_result.lon,
            "precision": payload.precision,
            "formatted_address": geocoding_result.formatted_address,
        },
        operator_location_text=payload.location_text,
        operator_comment=payload.operator_comment,
    )
    EventClusterer().assign(db, event)
    event = get_event(db, event.id) or event
    serialized = serialize_event(event).model_dump(mode="json")
    await event_broker.publish(serialized)
    return EventRead.model_validate(serialized)


@router.post("/{review_id}/reject", response_model=EventRead)
async def reject_review_location(
    review_id: int,
    payload: LocationReviewRejectRequest,
    db: Session = Depends(get_db),
) -> EventRead:
    review = get_location_review(db, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Location review not found")
    if review.status != "pending":
        raise HTTPException(status_code=409, detail="Location review is already closed")

    event = reject_location_review(db, review=review, operator_comment=payload.operator_comment)
    serialized = serialize_event(event).model_dump(mode="json")
    await event_broker.publish(serialized)
    return EventRead.model_validate(serialized)


def _join(*parts: str | None) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part or not part.strip():
            continue
        value = part.strip()
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return ", ".join(unique)
