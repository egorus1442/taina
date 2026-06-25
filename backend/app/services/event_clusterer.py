from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

from sqlalchemy.orm import Session

from app import models
from app.repositories import (
    attach_event_to_cluster,
    create_event_cluster,
    get_event_cluster,
    list_active_event_clusters,
)


CLUSTER_TTL = timedelta(hours=3)

MATCH_RADIUS_BY_PRECISION = {
    "address": 2_000.0,
    "landmark": 2_000.0,
    "street": 3_000.0,
    "district": 5_000.0,
    "settlement": 5_000.0,
    "city": 8_000.0,
    "unknown": 8_000.0,
}


@dataclass(frozen=True)
class ClusterCandidate:
    cluster: models.EventCluster
    distance_meters: float


class EventClusterer:
    def assign(self, db: Session, event: models.Event) -> models.EventCluster | None:
        if event.cluster_id is not None:
            return get_event_cluster(db, event.cluster_id)

        if event.lat is None or event.lon is None:
            return None

        event_time = self._ensure_utc(event.created_at)
        candidate = self._find_best_candidate(db, event=event, event_time=event_time)
        if candidate:
            age_seconds = int((event_time - self._ensure_utc(candidate.cluster.first_seen_at)).total_seconds())
            return attach_event_to_cluster(
                db,
                event=event,
                cluster=candidate.cluster,
                distance_meters=candidate.distance_meters,
                event_age_seconds=max(age_seconds, 0),
            )

        return create_event_cluster(
            db,
            event=event,
            match_radius_meters=self._match_radius(event.precision),
            expires_at=event_time + CLUSTER_TTL,
        )

    def _find_best_candidate(
        self,
        db: Session,
        *,
        event: models.Event,
        event_time: datetime,
    ) -> ClusterCandidate | None:
        candidates: list[ClusterCandidate] = []
        for cluster in list_active_event_clusters(db, reference_time=event_time):
            first_seen_at = self._ensure_utc(cluster.first_seen_at)
            expires_at = self._ensure_utc(cluster.expires_at)
            if not first_seen_at <= event_time <= expires_at:
                continue

            distance = haversine_meters(event.lat, event.lon, cluster.lat, cluster.lon)
            if distance <= cluster.match_radius_meters:
                candidates.append(ClusterCandidate(cluster=cluster, distance_meters=distance))

        if not candidates:
            return None
        return min(candidates, key=lambda candidate: candidate.distance_meters)

    def _match_radius(self, precision: str | None) -> float:
        return MATCH_RADIUS_BY_PRECISION.get((precision or "unknown").lower(), 8_000.0)

    def _ensure_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_meters = 6_371_000.0
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)

    value = sin(delta_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2) ** 2
    return 2 * earth_radius_meters * asin(sqrt(value))
