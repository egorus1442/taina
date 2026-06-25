import argparse

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.models import Event
from app.services.event_clusterer import EventClusterer


def cluster_existing_events(limit: int | None = None) -> None:
    db = SessionLocal()
    try:
        stmt = (
            select(Event)
            .options(joinedload(Event.cluster))
            .where(Event.cluster_id.is_(None))
            .where(Event.lat.is_not(None))
            .where(Event.lon.is_not(None))
            .order_by(Event.created_at)
        )
        if limit:
            stmt = stmt.limit(limit)

        clusterer = EventClusterer()
        processed = 0
        for event in db.scalars(stmt):
            cluster = clusterer.assign(db, event)
            processed += 1
            print(
                f"event_id={event.id} cluster_id={cluster.id if cluster else None}",
                flush=True,
            )

        print(f"Clustered {processed} existing events", flush=True)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Assign existing geocoded events to event clusters.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    cluster_existing_events(limit=args.limit)


if __name__ == "__main__":
    main()
