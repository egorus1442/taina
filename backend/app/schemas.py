from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceBase(BaseModel):
    telegram_id: str
    title: str
    username: str | None = None
    type: str = "channel"
    city: str
    region: str | None = None
    enabled: bool = True


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    title: str | None = None
    username: str | None = None
    type: str | None = None
    city: str | None = None
    region: str | None = None
    enabled: bool | None = None


class SourceRead(SourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_message_id: int | None = None
    processed_count: int
    event_count: int
    created_at: datetime
    updated_at: datetime


class SourceGroupBase(BaseModel):
    title: str
    channel_source_id: int | None = None
    chat_source_id: int | None = None
    city: str
    region: str | None = None
    enabled: bool = True
    monitor_channel_posts: bool = True
    monitor_chat_messages: bool = True
    monitor_channel_comments: bool = False
    comments_fetch_mode: str = "off"
    comments_poll_interval_seconds: int = Field(default=5, ge=5, le=3600)
    comments_last_posts_limit: int = Field(default=5, ge=1, le=20)


class SourceGroupCreate(SourceGroupBase):
    pass


class SourceGroupUpdate(BaseModel):
    title: str | None = None
    channel_source_id: int | None = None
    chat_source_id: int | None = None
    city: str | None = None
    region: str | None = None
    enabled: bool | None = None
    monitor_channel_posts: bool | None = None
    monitor_chat_messages: bool | None = None
    monitor_channel_comments: bool | None = None
    comments_fetch_mode: str | None = None
    comments_poll_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    comments_last_posts_limit: int | None = Field(default=None, ge=1, le=20)


class SourceGroupRead(SourceGroupBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_source: SourceRead | None = None
    chat_source: SourceRead | None = None
    comments_last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    telegram_message_id: int
    text: str
    sender_name: str | None = None
    message_date: datetime | None = None
    processed: bool
    keyword_matched: bool
    ai_processed: bool
    created_at: datetime


class ClassificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    is_drone_related: bool
    useful_for_alert: bool
    confidence: float
    risk_level: str
    event_type: str
    summary: str | None = None
    location_text: str | None = None
    location_precision: str
    needs_geocoding: bool
    comment: str | None = None


class ClassificationDebugRead(ClassificationRead):
    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: int
    raw_response: str | None = None
    created_at: datetime


class GeocodingResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    query: str
    provider: str
    formatted_address: str | None = None
    lat: float | None = None
    lon: float | None = None
    precision: str
    raw_response: str | None = None
    created_at: datetime


class LocationRead(BaseModel):
    city: str
    region: str | None = None
    district: str | None = None
    address: str | None = None
    landmark: str | None = None
    lat: float | None = None
    lon: float | None = None
    precision: str
    formatted_address: str | None = None


class EventClusterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    risk_level: str
    confidence: float
    summary: str | None = None
    lat: float
    lon: float
    match_radius_meters: float
    observed_radius_meters: float
    first_seen_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    event_count: int
    source_count: int
    created_at: datetime
    updated_at: datetime


class EventClusterMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cluster_id: int
    event_id: int
    distance_meters: float
    match_radius_meters: float
    event_age_seconds: int
    created_at: datetime


class LocationReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    status: str
    reason: str
    original_location_text: str | None = None
    suggested_query: str | None = None
    operator_location_text: str | None = None
    operator_formatted_address: str | None = None
    operator_comment: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EventRead(BaseModel):
    id: int
    created_at: datetime
    cluster_id: int | None = None
    cluster: EventClusterRead | None = None
    source: SourceRead
    message: MessageRead
    classification: ClassificationRead
    location: LocationRead
    review_status: str = "auto_approved"
    review_reason: str | None = None
    location_review: LocationReviewRead | None = None
    summary: str | None = None
    comment: str | None = None


class PipelineStatusRead(BaseModel):
    code: str
    label: str
    description: str
    color: str


class PipelineStageRead(BaseModel):
    key: str
    label: str
    status: str
    status_label: str
    description: str
    details: dict[str, Any] = Field(default_factory=dict)


class PipelineMessageRead(BaseModel):
    message: MessageRead
    source: SourceRead
    status: PipelineStatusRead
    stages: list[PipelineStageRead]
    raw_json: dict[str, Any] | None = None
    classification: ClassificationDebugRead | None = None
    geocoding_result: GeocodingResultRead | None = None
    event: EventRead | None = None
    location_review: LocationReviewRead | None = None
    cluster: EventClusterRead | None = None


class LocationReviewResolveRequest(BaseModel):
    location_text: str = Field(min_length=1)
    city: str | None = None
    region: str | None = None
    precision: str = "operator"
    operator_comment: str | None = None


class LocationReviewRejectRequest(BaseModel):
    operator_comment: str | None = None


class IngestTestRequest(BaseModel):
    source_id: int
    text: str = Field(min_length=1)
    telegram_message_id: int | None = None
    sender_name: str | None = "test"


class HealthRead(BaseModel):
    status: str
    app: str


class AdminSettingsRead(BaseModel):
    keyword_patterns: list[str]
    ai_system_prompt: str


class AdminSettingsUpdate(BaseModel):
    keyword_patterns: list[str] | None = None
    ai_system_prompt: str | None = Field(default=None, min_length=1)


class GeoSuggestionRead(BaseModel):
    value: str
    city: str | None = None
    region: str | None = None
    fias_id: str | None = None
    kladr_id: str | None = None


class TelegramLinkedChatRead(BaseModel):
    telegram_id: str
    title: str
    type: str
    username: str | None = None
    is_connected: bool = False
    source_id: int | None = None
    enabled: bool = False
    city: str | None = None
    region: str | None = None
    processed_count: int = 0
    event_count: int = 0


class TelegramDialogRead(BaseModel):
    telegram_id: str
    title: str
    type: str
    username: str | None = None
    is_connected: bool = False
    source_id: int | None = None
    enabled: bool = False
    city: str | None = None
    region: str | None = None
    processed_count: int = 0
    event_count: int = 0
    linked_chat: TelegramLinkedChatRead | None = None


class TelegramConnectRequest(BaseModel):
    telegram_id: str
    title: str
    type: str = "channel"
    username: str | None = None
    paired_chat_source_id: int | None = None
    paired_chat_telegram_id: str | None = None
    paired_chat_title: str | None = None
    paired_chat_username: str | None = None
    city: str = Field(min_length=1)
    region: str | None = None
    enabled: bool = True
    monitor_channel_posts: bool = True
    monitor_chat_messages: bool = True
    monitor_channel_comments: bool = False
    comments_fetch_mode: str = "off"
    comments_poll_interval_seconds: int = Field(default=5, ge=5, le=3600)
    comments_last_posts_limit: int = Field(default=5, ge=1, le=20)
