import argparse
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.models import DeliveryLog, Event, EventClusterMember
from app.repositories import get_event_cluster, refresh_event_cluster_rollups
from app.services.event_deduplicator import DUPLICATE_WINDOW, normalize_message_text


@dataclass(frozen=True)
class DuplicatePair:
    duplicate: Event
    original: Event


def find_duplicate_events() -> list[DuplicatePair]:
    db = SessionLocal()
    try:
        events = list(
            db.scalars(
                select(Event)
                .options(joinedload(Event.message), joinedload(Event.source))
                .order_by(Event.created_at)
            )
        )
        originals_by_text: dict[str, list[Event]] = {}
        duplicates: list[DuplicatePair] = []
        for event in events:
            key = normalize_message_text(event.message.text)
            if not key:
                continue

            original = _find_original(originals_by_text.get(key, []), event)
            if original:
                duplicates.append(DuplicatePair(duplicate=event, original=original))
            else:
                originals_by_text.setdefault(key, []).append(event)

        return duplicates
    finally:
        db.close()


def delete_duplicate_events(duplicates: list[DuplicatePair]) -> None:
    db = SessionLocal()
    changed_clusters: set[int] = set()
    try:
        for pair in duplicates:
            event = db.get(Event, pair.duplicate.id)
            if not event:
                continue

            cluster_id = event.cluster_id
            source = event.source
            db.query(DeliveryLog).filter(DeliveryLog.event_id == event.id).delete()
            db.query(EventClusterMember).filter(EventClusterMember.event_id == event.id).delete()
            if source and source.event_count > 0:
                source.event_count -= 1
                db.add(source)
            db.delete(event)
            if cluster_id:
                changed_clusters.add(cluster_id)

        db.commit()
        for cluster_id in changed_clusters:
            cluster = get_event_cluster(db, cluster_id)
            if cluster:
                refresh_event_cluster_rollups(db, cluster)
    finally:
        db.close()


def _find_original(candidates: list[Event], event: Event) -> Event | None:
    for candidate in reversed(candidates):
        delta = abs(event.created_at - candidate.created_at)
        if delta <= DUPLICATE_WINDOW:
            return candidate
        if delta > timedelta(hours=3):
            break
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Find or remove exact duplicate events.")
    parser.add_argument("--apply", action="store_true", help="Delete duplicates and refresh cluster rollups.")
    args = parser.parse_args()

    duplicates = find_duplicate_events()
    for pair in duplicates:
        print(
            f"duplicate_event_id={pair.duplicate.id} original_event_id={pair.original.id} "
            f"cluster_id={pair.duplicate.cluster_id} text={pair.duplicate.message.text!r}",
            flush=True,
        )

    if args.apply:
        delete_duplicate_events(duplicates)
        print(f"Deleted {len(duplicates)} duplicate events", flush=True)
    else:
        print(f"Found {len(duplicates)} duplicate events. Re-run with --apply to delete them.", flush=True)


if __name__ == "__main__":
    main()
