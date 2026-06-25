from app import models
from app.schemas import (
    ClassificationRead,
    EventClusterRead,
    EventRead,
    LocationRead,
    LocationReviewRead,
    MessageRead,
    SourceRead,
)


def serialize_cluster(cluster: models.EventCluster) -> EventClusterRead:
    return EventClusterRead.model_validate(cluster)


def serialize_event(event: models.Event) -> EventRead:
    return EventRead(
        id=event.id,
        created_at=event.created_at,
        cluster_id=event.cluster_id,
        cluster=serialize_cluster(event.cluster) if event.cluster else None,
        source=SourceRead.model_validate(event.source),
        message=MessageRead.model_validate(event.message),
        classification=ClassificationRead.model_validate(event.classification),
        location=LocationRead(
            city=event.city,
            region=event.region,
            district=event.district,
            address=event.address,
            landmark=event.landmark,
            lat=event.lat,
            lon=event.lon,
            precision=event.precision,
            formatted_address=event.formatted_address,
        ),
        review_status=event.review_status,
        review_reason=event.review_reason,
        location_review=LocationReviewRead.model_validate(event.location_review) if event.location_review else None,
        summary=event.summary,
        comment=event.comment,
    )
