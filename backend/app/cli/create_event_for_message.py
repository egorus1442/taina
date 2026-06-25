import argparse
import asyncio

from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.models import Message
from app.repositories import create_event, create_geocoding_result
from app.services.event_clusterer import EventClusterer
from app.services.geocoder import Geocoder
from app.services.geocoding_attempts import GeocodingAttemptBuilder
from app.services.location_resolver import LocationResolver


async def create_event_for_message(message_id: int, replace: bool = False) -> None:
    db = SessionLocal()
    try:
        message = (
            db.query(Message)
            .options(
                joinedload(Message.source),
                joinedload(Message.classification),
                joinedload(Message.event),
            )
            .filter(Message.id == message_id)
            .one_or_none()
        )
        if not message:
            raise SystemExit(f"Message {message_id} not found")
        if message.event and not replace:
            print(f"Message {message_id} already has event_id={message.event.id}")
            return
        if not message.classification:
            raise SystemExit(f"Message {message_id} has no classification")

        resolver = LocationResolver()
        attempt_builder = GeocodingAttemptBuilder()
        geocoder = Geocoder()
        clusterer = EventClusterer()
        resolved_location = resolver.resolve(
            source=message.source,
            classification=message.classification,
        )
        attempts = attempt_builder.build_for_source(
            source=message.source,
            location_text=message.classification.location_text,
            precision=message.classification.location_precision,
        )
        geocoding_payload = await geocoder.geocode_attempts(attempts)
        geocoding_result = create_geocoding_result(db, geocoding_payload)
        location = {
            "city": resolved_location.city,
            "region": resolved_location.region,
            "district": resolved_location.district,
            "address": resolved_location.address,
            "landmark": resolved_location.landmark,
            "lat": geocoding_result.lat,
            "lon": geocoding_result.lon,
            "precision": resolved_location.precision,
            "formatted_address": geocoding_result.formatted_address,
        }
        if message.event:
            event = message.event
            event.geocoding_result_id = geocoding_result.id
            event.city = location["city"]
            event.region = location.get("region")
            event.district = location.get("district")
            event.address = location.get("address")
            event.landmark = location.get("landmark")
            event.lat = location.get("lat")
            event.lon = location.get("lon")
            event.precision = location.get("precision") or "city"
            event.formatted_address = location.get("formatted_address")
            db.add(event)
            db.commit()
            db.refresh(event)
        else:
            event = create_event(
                db,
                source=message.source,
                message=message,
                classification=message.classification,
                geocoding_result=geocoding_result,
                location=location,
            )
        clusterer.assign(db, event)
        print(f"Created event_id={event.id} for message_id={message_id}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create event for an already classified message.")
    parser.add_argument("message_id", type=int)
    parser.add_argument("--replace", action="store_true", help="Recompute location for an existing event.")
    args = parser.parse_args()
    asyncio.run(create_event_for_message(args.message_id, replace=args.replace))


if __name__ == "__main__":
    main()
