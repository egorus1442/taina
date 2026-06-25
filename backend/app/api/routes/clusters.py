from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import get_event_cluster, list_event_clusters, list_events_for_cluster
from app.schemas import EventClusterRead, EventRead
from app.serializers import serialize_cluster, serialize_event


router = APIRouter()


@router.get("", response_model=list[EventClusterRead])
def get_clusters(
    limit: int = Query(default=100, ge=1, le=500),
    status: str | None = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
) -> list[EventClusterRead]:
    clusters = list_event_clusters(
        db,
        limit=limit,
        status=status,
        active_only=active_only,
        reference_time=datetime.now(timezone.utc),
    )
    return [serialize_cluster(cluster) for cluster in clusters]


@router.get("/active", response_model=list[EventClusterRead])
def get_active_clusters(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[EventClusterRead]:
    clusters = list_event_clusters(
        db,
        limit=limit,
        active_only=True,
        reference_time=datetime.now(timezone.utc),
    )
    return [serialize_cluster(cluster) for cluster in clusters]


@router.get("/{cluster_id}", response_model=EventClusterRead)
def get_cluster(cluster_id: int, db: Session = Depends(get_db)) -> EventClusterRead:
    cluster = get_event_cluster(db, cluster_id)
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return serialize_cluster(cluster)


@router.get("/{cluster_id}/events", response_model=list[EventRead])
def get_cluster_events(
    cluster_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[EventRead]:
    if not get_event_cluster(db, cluster_id):
        raise HTTPException(status_code=404, detail="Cluster not found")
    return [serialize_event(event) for event in list_events_for_cluster(db, cluster_id=cluster_id, limit=limit)]
