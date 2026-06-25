from datetime import datetime
import json

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app import models
from app.schemas import SourceCreate, SourceGroupCreate, SourceGroupUpdate, SourceUpdate
from app.services.admin_settings import (
    DEFAULT_AI_SYSTEM_PROMPT,
    DEFAULT_KEYWORD_PATTERNS,
    SETTINGS_KEY_AI_SYSTEM_PROMPT,
    SETTINGS_KEY_KEYWORD_PATTERNS,
)


COMMENT_FETCH_MODES = {"off", "poll_replies", "linked_chat_realtime", "hybrid"}


def get_app_setting(db: Session, key: str) -> models.AppSetting | None:
    return db.scalar(select(models.AppSetting).where(models.AppSetting.key == key))


def upsert_app_setting(db: Session, *, key: str, value: str) -> models.AppSetting:
    setting = get_app_setting(db, key)
    if setting is None:
        setting = models.AppSetting(key=key, value=value)
    else:
        setting.value = value
        setting.updated_at = models.utcnow()
    db.add(setting)
    db.commit()
    db.refresh(setting)
    return setting


def get_keyword_patterns_setting(db: Session) -> list[str]:
    setting = get_app_setting(db, SETTINGS_KEY_KEYWORD_PATTERNS)
    if not setting:
        return DEFAULT_KEYWORD_PATTERNS
    try:
        value = json.loads(setting.value)
    except json.JSONDecodeError:
        return DEFAULT_KEYWORD_PATTERNS
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return DEFAULT_KEYWORD_PATTERNS
    return value


def get_ai_system_prompt_setting(db: Session) -> str:
    setting = get_app_setting(db, SETTINGS_KEY_AI_SYSTEM_PROMPT)
    if not setting or not setting.value.strip():
        return DEFAULT_AI_SYSTEM_PROMPT
    return setting.value


def list_sources(db: Session) -> list[models.Source]:
    return list(db.scalars(select(models.Source).order_by(models.Source.title)))


def list_enabled_sources(db: Session) -> list[models.Source]:
    return list(db.scalars(select(models.Source).where(models.Source.enabled.is_(True))))


def get_source(db: Session, source_id: int) -> models.Source | None:
    return db.get(models.Source, source_id)


def get_source_by_telegram_id(db: Session, telegram_id: str) -> models.Source | None:
    return db.scalar(select(models.Source).where(models.Source.telegram_id == telegram_id))


def create_source(db: Session, payload: SourceCreate) -> models.Source:
    source = models.Source(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def update_source(db: Session, source: models.Source, payload: SourceUpdate) -> models.Source:
    updates = payload.model_dump(exclude_unset=True)
    previous_type = source.type
    for key, value in updates.items():
        setattr(source, key, value)
    source.updated_at = models.utcnow()
    db.add(source)
    if "type" in updates and updates["type"] != previous_type:
        _sync_single_source_groups_after_type_change(db, source)
    db.commit()
    db.refresh(source)
    return source


def list_source_groups(db: Session) -> list[models.SourceGroup]:
    stmt = (
        select(models.SourceGroup)
        .options(
            joinedload(models.SourceGroup.channel_source),
            joinedload(models.SourceGroup.chat_source),
        )
        .order_by(models.SourceGroup.title)
    )
    return list(db.scalars(stmt))


def list_enabled_comment_source_groups(db: Session) -> list[models.SourceGroup]:
    stmt = (
        select(models.SourceGroup)
        .options(
            joinedload(models.SourceGroup.channel_source),
            joinedload(models.SourceGroup.chat_source),
        )
        .where(models.SourceGroup.enabled.is_(True))
        .where(models.SourceGroup.monitor_channel_comments.is_(True))
        .where(models.SourceGroup.comments_fetch_mode.in_(("poll_replies", "hybrid")))
        .where(models.SourceGroup.channel_source_id.is_not(None))
    )
    return list(db.scalars(stmt))


def get_source_group(db: Session, group_id: int) -> models.SourceGroup | None:
    stmt = (
        select(models.SourceGroup)
        .options(
            joinedload(models.SourceGroup.channel_source),
            joinedload(models.SourceGroup.chat_source),
        )
        .where(models.SourceGroup.id == group_id)
    )
    return db.scalar(stmt)


def get_source_group_for_source_ids(
    db: Session,
    *,
    channel_source_id: int | None,
    chat_source_id: int | None,
) -> models.SourceGroup | None:
    if channel_source_id is None and chat_source_id is None:
        return None

    stmt = select(models.SourceGroup)
    if channel_source_id is not None:
        stmt = stmt.where(models.SourceGroup.channel_source_id == channel_source_id)
    else:
        stmt = stmt.where(models.SourceGroup.channel_source_id.is_(None))
    if chat_source_id is not None:
        stmt = stmt.where(models.SourceGroup.chat_source_id == chat_source_id)
    else:
        stmt = stmt.where(models.SourceGroup.chat_source_id.is_(None))
    return db.scalar(stmt)


def create_source_group(db: Session, payload: SourceGroupCreate) -> models.SourceGroup:
    values = payload.model_dump()
    _validate_source_group_payload(db, values)
    _sync_source_enabled_from_group_values(db, values)
    group = models.SourceGroup(**values)
    db.add(group)
    db.commit()
    db.refresh(group)
    return get_source_group(db, group.id) or group


def update_source_group(
    db: Session,
    group: models.SourceGroup,
    payload: SourceGroupUpdate,
) -> models.SourceGroup:
    updates = payload.model_dump(exclude_unset=True)
    next_values = {
        "channel_source_id": group.channel_source_id,
        "chat_source_id": group.chat_source_id,
        "comments_fetch_mode": group.comments_fetch_mode,
        **updates,
    }
    _validate_source_group_payload(db, next_values)
    for key, value in updates.items():
        setattr(group, key, value)
    if {"enabled", "channel_source_id", "chat_source_id"} & set(updates):
        _sync_source_enabled_from_group_values(db, next_values)
    group.updated_at = models.utcnow()
    db.add(group)
    db.commit()
    db.refresh(group)
    return get_source_group(db, group.id) or group


def touch_source_group_comments_checked(db: Session, group: models.SourceGroup) -> None:
    group.comments_last_checked_at = models.utcnow()
    group.updated_at = models.utcnow()
    db.add(group)
    db.commit()


def get_comment_thread_state(
    db: Session,
    *,
    source_group_id: int,
    channel_source_id: int,
    channel_post_id: int,
) -> models.CommentThreadState | None:
    stmt = select(models.CommentThreadState).where(
        models.CommentThreadState.source_group_id == source_group_id,
        models.CommentThreadState.channel_source_id == channel_source_id,
        models.CommentThreadState.channel_post_id == channel_post_id,
    )
    return db.scalar(stmt)


def get_recorded_comment_thread_max_id(
    db: Session,
    *,
    source_group_id: int,
    channel_source_id: int,
    channel_post_id: int,
) -> int:
    rows = db.scalars(
        select(models.Message.raw_json).where(models.Message.source_id == channel_source_id)
    )
    max_comment_id = 0
    for raw_json in rows:
        if not raw_json:
            continue
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        if (
            payload.get("source_group_id") != source_group_id
            or payload.get("channel_post_id") != channel_post_id
        ):
            continue
        try:
            comment_id = int(payload.get("comment_id") or 0)
        except (TypeError, ValueError):
            continue
        max_comment_id = max(max_comment_id, comment_id)
    return max_comment_id


def upsert_comment_thread_state(
    db: Session,
    *,
    source_group_id: int,
    channel_source_id: int,
    channel_post_id: int,
    discussion_chat_id: str | None,
    discussion_root_msg_id: int | None,
    last_comment_id: int,
) -> models.CommentThreadState:
    state = get_comment_thread_state(
        db,
        source_group_id=source_group_id,
        channel_source_id=channel_source_id,
        channel_post_id=channel_post_id,
    )
    if state is None:
        state = models.CommentThreadState(
            source_group_id=source_group_id,
            channel_source_id=channel_source_id,
            channel_post_id=channel_post_id,
            discussion_chat_id=discussion_chat_id,
            discussion_root_msg_id=discussion_root_msg_id,
            last_comment_id=last_comment_id,
        )
    else:
        state.discussion_chat_id = discussion_chat_id
        state.discussion_root_msg_id = discussion_root_msg_id
        state.last_comment_id = max(state.last_comment_id or 0, last_comment_id)
        state.updated_at = models.utcnow()
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


def upsert_source_group_for_sources(
    db: Session,
    *,
    title: str,
    channel_source: models.Source | None,
    chat_source: models.Source | None,
    city: str,
    region: str | None,
    enabled: bool,
    monitor_channel_posts: bool,
    monitor_chat_messages: bool,
    monitor_channel_comments: bool,
    comments_fetch_mode: str,
    comments_poll_interval_seconds: int,
    comments_last_posts_limit: int,
) -> models.SourceGroup:
    existing = get_source_group_for_source_ids(
        db,
        channel_source_id=channel_source.id if channel_source else None,
        chat_source_id=chat_source.id if chat_source else None,
    )
    payload = {
        "title": title,
        "channel_source_id": channel_source.id if channel_source else None,
        "chat_source_id": chat_source.id if chat_source else None,
        "city": city,
        "region": region,
        "enabled": enabled,
        "monitor_channel_posts": monitor_channel_posts,
        "monitor_chat_messages": monitor_chat_messages,
        "monitor_channel_comments": monitor_channel_comments,
        "comments_fetch_mode": comments_fetch_mode,
        "comments_poll_interval_seconds": comments_poll_interval_seconds,
        "comments_last_posts_limit": comments_last_posts_limit,
    }
    if existing:
        return update_source_group(db, existing, SourceGroupUpdate(**payload))
    return create_source_group(db, SourceGroupCreate(**payload))


def _validate_source_group_payload(db: Session, payload: dict) -> None:
    from fastapi import HTTPException

    if not payload.get("channel_source_id") and not payload.get("chat_source_id"):
        raise HTTPException(status_code=422, detail="Source group must include a channel, a chat, or both")

    mode = payload.get("comments_fetch_mode") or "off"
    if mode not in COMMENT_FETCH_MODES:
        raise HTTPException(status_code=422, detail=f"Unsupported comments_fetch_mode: {mode}")

    channel_source_id = payload.get("channel_source_id")
    chat_source_id = payload.get("chat_source_id")
    if channel_source_id:
        channel = get_source(db, int(channel_source_id))
        if not channel:
            raise HTTPException(status_code=404, detail="Channel source not found")
        if channel.type != "channel":
            raise HTTPException(status_code=422, detail="channel_source_id must reference a channel source")
    if chat_source_id:
        chat = get_source(db, int(chat_source_id))
        if not chat:
            raise HTTPException(status_code=404, detail="Chat source not found")
        if chat.type != "chat":
            raise HTTPException(status_code=422, detail="chat_source_id must reference a chat source")
    if mode == "linked_chat_realtime" and not chat_source_id:
        raise HTTPException(status_code=422, detail="linked_chat_realtime requires chat_source_id")
    if mode == "poll_replies" and not channel_source_id:
        raise HTTPException(status_code=422, detail="poll_replies requires channel_source_id")


def _sync_source_enabled_from_group_values(db: Session, values: dict) -> None:
    enabled = bool(values.get("enabled", True))
    for source_id in (values.get("channel_source_id"), values.get("chat_source_id")):
        if not source_id:
            continue
        source = get_source(db, int(source_id))
        if not source:
            continue
        source.enabled = enabled
        source.updated_at = models.utcnow()
        db.add(source)


def _sync_single_source_groups_after_type_change(db: Session, source: models.Source) -> None:
    groups = list(
        db.scalars(
            select(models.SourceGroup).where(
                (
                    (models.SourceGroup.channel_source_id == source.id)
                    & (models.SourceGroup.chat_source_id.is_(None))
                )
                | (
                    (models.SourceGroup.chat_source_id == source.id)
                    & (models.SourceGroup.channel_source_id.is_(None))
                )
            )
        )
    )
    for group in groups:
        if source.type == "channel":
            group.channel_source_id = source.id
            group.chat_source_id = None
            group.monitor_channel_posts = True
            group.monitor_chat_messages = False
            group.monitor_channel_comments = False
            group.comments_fetch_mode = "off"
        elif source.type == "chat":
            group.channel_source_id = None
            group.chat_source_id = source.id
            group.monitor_channel_posts = False
            group.monitor_chat_messages = True
            group.monitor_channel_comments = False
            group.comments_fetch_mode = "off"
        group.updated_at = models.utcnow()
        db.add(group)


def create_message(
    db: Session,
    *,
    source: models.Source,
    telegram_message_id: int,
    text: str,
    sender_name: str | None,
    message_date: datetime | None,
    raw_json: str | None = None,
) -> models.Message | None:
    message = models.Message(
        source_id=source.id,
        telegram_message_id=telegram_message_id,
        text=text,
        sender_name=sender_name,
        message_date=message_date,
        raw_json=raw_json,
    )
    db.add(message)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(message)
    return message


def message_exists(
    db: Session,
    *,
    source_id: int,
    telegram_message_id: int,
) -> bool:
    stmt = select(models.Message.id).where(
        models.Message.source_id == source_id,
        models.Message.telegram_message_id == telegram_message_id,
    )
    return db.scalar(stmt) is not None


def list_messages(db: Session, limit: int = 100) -> list[models.Message]:
    stmt = select(models.Message).order_by(desc(models.Message.created_at)).limit(limit)
    return list(db.scalars(stmt))


def list_keyword_matched_pipeline_messages(db: Session, limit: int = 200) -> list[models.Message]:
    stmt = (
        select(models.Message)
        .options(
            joinedload(models.Message.source),
            joinedload(models.Message.classification),
            joinedload(models.Message.event).joinedload(models.Event.geocoding_result),
            joinedload(models.Message.event).joinedload(models.Event.location_review),
            joinedload(models.Message.event).joinedload(models.Event.cluster),
        )
        .where(models.Message.keyword_matched.is_(True))
        .order_by(desc(models.Message.created_at))
        .limit(limit)
    )
    return list(db.scalars(stmt))


def create_classification(
    db: Session,
    *,
    message: models.Message,
    payload: dict,
    raw_response: str | None,
) -> models.Classification:
    classification = models.Classification(
        message_id=message.id,
        is_drone_related=payload.get("is_drone_related", False),
        useful_for_alert=payload.get("useful_for_alert", False),
        confidence=float(payload.get("confidence") or 0.0),
        risk_level=payload.get("risk_level") or "low",
        event_type=payload.get("event_type") or "none",
        summary=payload.get("summary"),
        location_text=payload.get("location_text"),
        location_precision=payload.get("location_precision") or "unknown",
        needs_geocoding=payload.get("needs_geocoding", False),
        comment=payload.get("comment"),
        raw_response=raw_response,
    )
    db.add(classification)
    message.ai_processed = True
    db.commit()
    db.refresh(classification)
    return classification


def create_geocoding_result(db: Session, payload: dict) -> models.GeocodingResult:
    result = models.GeocodingResult(
        query=payload.get("query") or "",
        provider=payload.get("provider") or "unknown",
        formatted_address=payload.get("formatted_address"),
        lat=payload.get("lat"),
        lon=payload.get("lon"),
        precision=payload.get("precision") or "unknown",
        raw_response=payload.get("raw_response"),
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


def create_event(
    db: Session,
    *,
    source: models.Source,
    message: models.Message,
    classification: models.Classification,
    geocoding_result: models.GeocodingResult | None,
    location: dict,
    review_status: str = "auto_approved",
    review_reason: str | None = None,
) -> models.Event:
    event = models.Event(
        source_id=source.id,
        message_id=message.id,
        classification_id=classification.id,
        geocoding_result_id=geocoding_result.id if geocoding_result else None,
        city=location["city"],
        region=location.get("region"),
        district=location.get("district"),
        address=location.get("address"),
        landmark=location.get("landmark"),
        lat=location.get("lat"),
        lon=location.get("lon"),
        precision=location.get("precision") or "city",
        formatted_address=location.get("formatted_address"),
        confidence=classification.confidence,
        risk_level=classification.risk_level,
        event_type=classification.event_type,
        summary=classification.summary,
        comment=classification.comment,
        review_status=review_status,
        review_reason=review_reason,
    )
    db.add(event)
    source.event_count += 1
    db.commit()
    db.refresh(event)
    return get_event(db, event.id) or event


def create_location_review(
    db: Session,
    *,
    event: models.Event,
    reason: str,
    original_location_text: str | None,
    suggested_query: str | None,
) -> models.LocationReview:
    review = models.LocationReview(
        event_id=event.id,
        status="pending",
        reason=reason,
        original_location_text=original_location_text,
        suggested_query=suggested_query,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def get_location_review(db: Session, review_id: int) -> models.LocationReview | None:
    stmt = (
        select(models.LocationReview)
        .options(
            joinedload(models.LocationReview.event).joinedload(models.Event.source),
            joinedload(models.LocationReview.event).joinedload(models.Event.message),
            joinedload(models.LocationReview.event).joinedload(models.Event.classification),
            joinedload(models.LocationReview.event).joinedload(models.Event.cluster),
        )
        .where(models.LocationReview.id == review_id)
    )
    return db.scalar(stmt)


def list_location_reviews(
    db: Session,
    *,
    status: str | None = "pending",
    limit: int = 100,
) -> list[models.LocationReview]:
    stmt = (
        select(models.LocationReview)
        .options(
            joinedload(models.LocationReview.event).joinedload(models.Event.source),
            joinedload(models.LocationReview.event).joinedload(models.Event.message),
            joinedload(models.LocationReview.event).joinedload(models.Event.classification),
            joinedload(models.LocationReview.event).joinedload(models.Event.cluster),
        )
        .order_by(desc(models.LocationReview.created_at))
        .limit(limit)
    )
    if status:
        stmt = stmt.where(models.LocationReview.status == status)
    return list(db.scalars(stmt))


def resolve_location_review(
    db: Session,
    *,
    review: models.LocationReview,
    geocoding_result: models.GeocodingResult,
    location: dict,
    operator_location_text: str,
    operator_comment: str | None,
) -> models.Event:
    event = review.event
    event.geocoding_result_id = geocoding_result.id
    event.city = location["city"]
    event.region = location.get("region")
    event.district = location.get("district")
    event.address = location.get("address")
    event.landmark = location.get("landmark")
    event.lat = location.get("lat")
    event.lon = location.get("lon")
    event.precision = location.get("precision") or "operator"
    event.formatted_address = location.get("formatted_address")
    event.review_status = "operator_approved"
    event.review_reason = None

    review.status = "resolved"
    review.operator_location_text = operator_location_text
    review.operator_formatted_address = geocoding_result.formatted_address
    review.operator_comment = operator_comment
    review.resolved_at = models.utcnow()
    review.updated_at = models.utcnow()

    db.add(event)
    db.add(review)
    db.commit()
    db.refresh(event)
    return get_event(db, event.id) or event


def reject_location_review(
    db: Session,
    *,
    review: models.LocationReview,
    operator_comment: str | None,
) -> models.Event:
    event = review.event
    event.review_status = "rejected"
    review.status = "rejected"
    review.operator_comment = operator_comment
    review.resolved_at = models.utcnow()
    review.updated_at = models.utcnow()
    db.add(event)
    db.add(review)
    db.commit()
    db.refresh(event)
    return get_event(db, event.id) or event


def create_event_cluster(
    db: Session,
    *,
    event: models.Event,
    match_radius_meters: float,
    expires_at: datetime,
) -> models.EventCluster:
    cluster = models.EventCluster(
        status="active",
        risk_level=event.risk_level,
        confidence=event.confidence,
        summary=event.summary,
        lat=event.lat,
        lon=event.lon,
        match_radius_meters=match_radius_meters,
        observed_radius_meters=0.0,
        first_seen_at=event.created_at,
        last_seen_at=event.created_at,
        expires_at=expires_at,
        event_count=1,
        source_count=1,
    )
    db.add(cluster)
    db.flush()
    event.cluster_id = cluster.id
    db.add(event)
    db.add(
        models.EventClusterMember(
            cluster_id=cluster.id,
            event_id=event.id,
            distance_meters=0.0,
            match_radius_meters=match_radius_meters,
            event_age_seconds=0,
        )
    )
    db.commit()
    db.refresh(cluster)
    return cluster


def attach_event_to_cluster(
    db: Session,
    *,
    event: models.Event,
    cluster: models.EventCluster,
    distance_meters: float,
    event_age_seconds: int,
) -> models.EventCluster:
    event.cluster_id = cluster.id
    cluster.last_seen_at = max(cluster.last_seen_at, event.created_at)
    cluster.updated_at = models.utcnow()
    cluster.event_count += 1
    cluster.observed_radius_meters = max(cluster.observed_radius_meters or 0.0, distance_meters)
    cluster.risk_level = _max_risk_level(cluster.risk_level, event.risk_level)
    previous_confidence = cluster.confidence
    if event.summary and (not cluster.summary or event.confidence >= previous_confidence):
        cluster.summary = event.summary
    cluster.confidence = max(cluster.confidence, event.confidence)

    db.add(event)
    db.add(cluster)
    db.add(
        models.EventClusterMember(
            cluster_id=cluster.id,
            event_id=event.id,
            distance_meters=distance_meters,
            match_radius_meters=cluster.match_radius_meters,
            event_age_seconds=event_age_seconds,
        )
    )
    db.commit()
    refresh_event_cluster_rollups(db, cluster)
    return get_event_cluster(db, cluster.id) or cluster


def refresh_event_cluster_rollups(db: Session, cluster: models.EventCluster) -> None:
    source_count = db.scalar(
        select(func.count(func.distinct(models.Event.source_id))).where(models.Event.cluster_id == cluster.id)
    )
    first_seen_at, last_seen_at = db.execute(
        select(func.min(models.Event.created_at), func.max(models.Event.created_at)).where(
            models.Event.cluster_id == cluster.id
        )
    ).one()
    cluster.source_count = int(source_count or 0)
    cluster.event_count = int(
        db.scalar(select(func.count(models.Event.id)).where(models.Event.cluster_id == cluster.id)) or 0
    )
    if first_seen_at:
        cluster.first_seen_at = first_seen_at
    if last_seen_at:
        cluster.last_seen_at = last_seen_at
    cluster.confidence = _cluster_confidence(db, cluster)
    cluster.status = _cluster_status(cluster)
    cluster.updated_at = models.utcnow()
    db.add(cluster)
    db.commit()
    db.refresh(cluster)


def get_event_cluster(db: Session, cluster_id: int) -> models.EventCluster | None:
    stmt = select(models.EventCluster).where(models.EventCluster.id == cluster_id)
    return db.scalar(stmt)


def list_active_event_clusters(
    db: Session,
    *,
    reference_time: datetime,
    limit: int = 500,
) -> list[models.EventCluster]:
    stmt = (
        select(models.EventCluster)
        .where(models.EventCluster.status.in_(("active", "corroborated")))
        .where(models.EventCluster.expires_at >= reference_time)
        .order_by(desc(models.EventCluster.last_seen_at))
        .limit(limit)
    )
    return list(db.scalars(stmt))


def list_event_clusters(
    db: Session,
    *,
    limit: int = 100,
    status: str | None = None,
    active_only: bool = False,
    reference_time: datetime | None = None,
) -> list[models.EventCluster]:
    stmt = select(models.EventCluster)
    if status:
        stmt = stmt.where(models.EventCluster.status == status)
    if active_only and reference_time:
        stmt = stmt.where(models.EventCluster.expires_at >= reference_time)
        stmt = stmt.where(models.EventCluster.status.in_(("active", "corroborated")))
    stmt = stmt.order_by(desc(models.EventCluster.last_seen_at)).limit(limit)
    return list(db.scalars(stmt))


def list_events_for_cluster(
    db: Session,
    *,
    cluster_id: int,
    limit: int = 100,
) -> list[models.Event]:
    stmt = (
        select(models.Event)
        .options(
            joinedload(models.Event.source),
            joinedload(models.Event.message),
            joinedload(models.Event.classification),
            joinedload(models.Event.cluster),
            joinedload(models.Event.location_review),
        )
        .where(models.Event.cluster_id == cluster_id)
        .order_by(desc(models.Event.created_at))
        .limit(limit)
    )
    return list(db.scalars(stmt))


def get_event(db: Session, event_id: int) -> models.Event | None:
    stmt = (
        select(models.Event)
        .options(
            joinedload(models.Event.source),
            joinedload(models.Event.message),
            joinedload(models.Event.classification),
            joinedload(models.Event.cluster),
            joinedload(models.Event.location_review),
        )
        .where(models.Event.id == event_id)
    )
    return db.scalar(stmt)


def list_events(
    db: Session,
    *,
    limit: int = 100,
    city: str | None = None,
    risk_level: str | None = None,
) -> list[models.Event]:
    stmt = select(models.Event).options(
        joinedload(models.Event.source),
        joinedload(models.Event.message),
            joinedload(models.Event.classification),
            joinedload(models.Event.cluster),
            joinedload(models.Event.location_review),
        )
    if city:
        stmt = stmt.where(models.Event.city == city)
    if risk_level:
        stmt = stmt.where(models.Event.risk_level == risk_level)
    stmt = stmt.order_by(desc(models.Event.created_at)).limit(limit)
    return list(db.scalars(stmt))


def _max_risk_level(current: str, next_value: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return next_value if order.get(next_value, 0) > order.get(current, 0) else current


def _cluster_confidence(db: Session, cluster: models.EventCluster) -> float:
    max_event_confidence = float(
        db.scalar(select(func.max(models.Event.confidence)).where(models.Event.cluster_id == cluster.id)) or 0.0
    )
    source_bonus = min(max(cluster.source_count - 1, 0) * 0.08, 0.24)
    event_bonus = min(max(cluster.event_count - 1, 0) * 0.03, 0.12)
    single_source_penalty = 0.1 if cluster.source_count <= 1 else 0.0
    return max(0.0, min(max_event_confidence + source_bonus + event_bonus - single_source_penalty, 1.0))


def _cluster_status(cluster: models.EventCluster) -> str:
    if cluster.status in {"operator_confirmed", "false_positive", "resolved"}:
        return cluster.status
    if cluster.source_count >= 2 or cluster.event_count >= 3:
        return "corroborated"
    return "active"


def mark_message_processed(
    db: Session,
    *,
    source: models.Source,
    message: models.Message,
    keyword_matched: bool,
) -> None:
    message.processed = True
    message.keyword_matched = keyword_matched
    source.processed_count += 1
    source.last_message_id = max(source.last_message_id or 0, message.telegram_message_id)
    source.updated_at = models.utcnow()
    db.add(message)
    db.add(source)
    db.commit()


def create_delivery_log(
    db: Session,
    *,
    event_id: int,
    destination: str,
    success: bool,
    status_code: int | None = None,
    error: str | None = None,
) -> models.DeliveryLog:
    log = models.DeliveryLog(
        event_id=event_id,
        destination=destination,
        success=success,
        status_code=status_code,
        error=error,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
