from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    type: Mapped[str] = mapped_column(String(32), default="channel")
    city: Mapped[str] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list["Message"]] = relationship(back_populates="source")
    events: Mapped[list["Event"]] = relationship(back_populates="source")
    channel_groups: Mapped[list["SourceGroup"]] = relationship(
        back_populates="channel_source",
        foreign_keys="SourceGroup.channel_source_id",
    )
    chat_groups: Mapped[list["SourceGroup"]] = relationship(
        back_populates="chat_source",
        foreign_keys="SourceGroup.chat_source_id",
    )


class SourceGroup(Base):
    __tablename__ = "source_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    channel_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id"), nullable=True, index=True
    )
    chat_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id"), nullable=True, index=True
    )
    city: Mapped[str] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    monitor_channel_posts: Mapped[bool] = mapped_column(Boolean, default=True)
    monitor_chat_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    monitor_channel_comments: Mapped[bool] = mapped_column(Boolean, default=False)
    comments_fetch_mode: Mapped[str] = mapped_column(String(32), default="off")
    comments_poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=15)
    comments_last_posts_limit: Mapped[int] = mapped_column(Integer, default=5)
    comments_last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channel_source: Mapped[Source | None] = relationship(
        foreign_keys=[channel_source_id],
        back_populates="channel_groups",
    )
    chat_source: Mapped[Source | None] = relationship(
        foreign_keys=[chat_source_id],
        back_populates="chat_groups",
    )


class CommentThreadState(Base):
    __tablename__ = "comment_thread_states"
    __table_args__ = (
        UniqueConstraint(
            "source_group_id",
            "channel_source_id",
            "channel_post_id",
            name="uq_comment_thread_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_group_id: Mapped[int] = mapped_column(ForeignKey("source_groups.id"), index=True)
    channel_source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    channel_post_id: Mapped[int] = mapped_column(Integer, index=True)
    discussion_chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    discussion_root_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_comment_id: Mapped[int] = mapped_column(Integer, default=0)
    post_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    consecutive_idle_polls: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source_group: Mapped[SourceGroup] = relationship()
    channel_source: Mapped[Source] = relationship()


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("source_id", "telegram_message_id", name="uq_source_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer, index=True)
    text: Mapped[str] = mapped_column(Text)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    keyword_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="messages")
    classification: Mapped["Classification | None"] = relationship(back_populates="message")
    event: Mapped["Event | None"] = relationship(back_populates="message")


class Classification(Base):
    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), unique=True, index=True)
    is_drone_related: Mapped[bool] = mapped_column(Boolean, default=False)
    useful_for_alert: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(32), default="low")
    event_type: Mapped[str] = mapped_column(String(64), default="none")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_precision: Mapped[str] = mapped_column(String(32), default="unknown")
    needs_geocoding: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    message: Mapped[Message] = relationship(back_populates="classification")


class GeocodingResult(Base):
    __tablename__ = "geocoding_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    query: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(64))
    formatted_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision: Mapped[str] = mapped_column(String(32), default="unknown")
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_clusters.id"), nullable=True, index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), unique=True, index=True)
    classification_id: Mapped[int] = mapped_column(ForeignKey("classifications.id"))
    geocoding_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("geocoding_results.id"), nullable=True
    )
    city: Mapped[str] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    district: Mapped[str | None] = mapped_column(String(128), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    landmark: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision: Mapped[str] = mapped_column(String(32), default="city")
    formatted_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(32), default="low")
    event_type: Mapped[str] = mapped_column(String(64), default="possible_drone_sighting")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(String(32), default="auto_approved", index=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    source: Mapped[Source] = relationship(back_populates="events")
    message: Mapped[Message] = relationship(back_populates="event")
    classification: Mapped[Classification] = relationship()
    geocoding_result: Mapped[GeocodingResult | None] = relationship()
    cluster: Mapped["EventCluster | None"] = relationship(back_populates="events")
    cluster_membership: Mapped["EventClusterMember | None"] = relationship(back_populates="event")
    location_review: Mapped["LocationReview | None"] = relationship(back_populates="event")


class LocationReview(Base):
    __tablename__ = "location_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    reason: Mapped[str] = mapped_column(String(64), index=True)
    original_location_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_location_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_formatted_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    event: Mapped[Event] = relationship(back_populates="location_review")


class EventCluster(Base):
    __tablename__ = "event_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    risk_level: Mapped[str] = mapped_column(String(32), default="low")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    match_radius_meters: Mapped[float] = mapped_column(Float)
    observed_radius_meters: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    events: Mapped[list[Event]] = relationship(back_populates="cluster")
    members: Mapped[list["EventClusterMember"]] = relationship(back_populates="cluster")


class EventClusterMember(Base):
    __tablename__ = "event_cluster_members"
    __table_args__ = (UniqueConstraint("event_id", name="uq_event_cluster_member_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("event_clusters.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    distance_meters: Mapped[float] = mapped_column(Float)
    match_radius_meters: Mapped[float] = mapped_column(Float)
    event_age_seconds: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cluster: Mapped[EventCluster] = relationship(back_populates="members")
    event: Mapped[Event] = relationship(back_populates="cluster_membership")


class DeliveryLog(Base):
    __tablename__ = "delivery_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    destination: Mapped[str] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
