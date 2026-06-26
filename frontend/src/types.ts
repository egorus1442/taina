export type Source = {
  id: number;
  telegram_id: string;
  title: string;
  username?: string | null;
  type: string;
  city: string;
  region?: string | null;
  enabled: boolean;
  last_message_id?: number | null;
  processed_count: number;
  event_count: number;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: number;
  source_id: number;
  telegram_message_id: number;
  text: string;
  sender_name?: string | null;
  message_date?: string | null;
  processed: boolean;
  keyword_matched: boolean;
  ai_processed: boolean;
  created_at: string;
};

export type Classification = {
  is_drone_related: boolean;
  useful_for_alert: boolean;
  confidence: number;
  risk_level: "low" | "medium" | "high" | string;
  event_type: string;
  summary?: string | null;
  location_text?: string | null;
  location_precision: string;
  needs_geocoding: boolean;
  comment?: string | null;
};

export type Location = {
  city: string;
  region?: string | null;
  district?: string | null;
  address?: string | null;
  landmark?: string | null;
  lat?: number | null;
  lon?: number | null;
  precision: string;
  formatted_address?: string | null;
};

export type EventItem = {
  id: number;
  created_at: string;
  cluster_id?: number | null;
  cluster?: EventCluster | null;
  source: Source;
  message: Message;
  classification: Classification;
  location: Location;
  review_status: string;
  review_reason?: string | null;
  location_review?: LocationReview | null;
  summary?: string | null;
  comment?: string | null;
};

export type EventCluster = {
  id: number;
  status: string;
  risk_level: "low" | "medium" | "high" | string;
  confidence: number;
  summary?: string | null;
  lat: number;
  lon: number;
  match_radius_meters: number;
  observed_radius_meters: number;
  first_seen_at: string;
  last_seen_at: string;
  expires_at: string;
  event_count: number;
  source_count: number;
  created_at: string;
  updated_at: string;
};

export type SourceCreate = {
  telegram_id: string;
  title: string;
  username?: string;
  type: string;
  city: string;
  region?: string;
  enabled: boolean;
};

export type SourceUpdate = Partial<{
  title: string;
  username: string | null;
  type: string;
  city: string;
  region: string | null;
  enabled: boolean;
}>;

export type CommentsFetchMode = "off" | "poll_replies" | "linked_chat_realtime" | "hybrid" | string;

export type SourceGroup = {
  id: number;
  title: string;
  channel_source_id?: number | null;
  chat_source_id?: number | null;
  channel_source?: Source | null;
  chat_source?: Source | null;
  city: string;
  region?: string | null;
  enabled: boolean;
  monitor_channel_posts: boolean;
  monitor_chat_messages: boolean;
  monitor_channel_comments: boolean;
  comments_fetch_mode: CommentsFetchMode;
  comments_poll_interval_seconds: number;
  comments_last_posts_limit: number;
  comments_last_checked_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type SourceGroupUpdate = Partial<{
  title: string;
  channel_source_id: number | null;
  chat_source_id: number | null;
  city: string;
  region: string | null;
  enabled: boolean;
  monitor_channel_posts: boolean;
  monitor_chat_messages: boolean;
  monitor_channel_comments: boolean;
  comments_fetch_mode: CommentsFetchMode;
  comments_poll_interval_seconds: number;
  comments_last_posts_limit: number;
}>;

export type TelegramDialog = {
  telegram_id: string;
  title: string;
  type: string;
  username?: string | null;
  is_connected: boolean;
  source_id?: number | null;
  enabled: boolean;
  city?: string | null;
  region?: string | null;
  processed_count: number;
  event_count: number;
  linked_chat?: TelegramLinkedChat | null;
};

export type TelegramLinkedChat = {
  telegram_id: string;
  title: string;
  type: string;
  username?: string | null;
  is_connected: boolean;
  source_id?: number | null;
  enabled: boolean;
  city?: string | null;
  region?: string | null;
  processed_count: number;
  event_count: number;
};

export type TelegramConnectRequest = {
  telegram_id: string;
  title: string;
  username?: string | null;
  type: string;
  paired_chat_source_id?: number | null;
  paired_chat_telegram_id?: string | null;
  paired_chat_title?: string | null;
  paired_chat_username?: string | null;
  city: string;
  region?: string | null;
  enabled: boolean;
  monitor_channel_posts: boolean;
  monitor_chat_messages: boolean;
  monitor_channel_comments: boolean;
  comments_fetch_mode: CommentsFetchMode;
  comments_poll_interval_seconds: number;
  comments_last_posts_limit: number;
};

export type GeoSuggestion = {
  value: string;
  city?: string | null;
  region?: string | null;
  fias_id?: string | null;
  kladr_id?: string | null;
};

export type LocationReview = {
  id: number;
  event_id?: number;
  status: string;
  reason: string;
  original_location_text?: string | null;
  suggested_query?: string | null;
  operator_location_text?: string | null;
  operator_formatted_address?: string | null;
  operator_comment?: string | null;
  resolved_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type LocationReviewItem = {
  id: number;
  status: string;
  reason: string;
  original_location_text?: string | null;
  suggested_query?: string | null;
  created_at: string;
  updated_at: string;
  event: EventItem;
};

export type LocationReviewResolveRequest = {
  location_text: string;
  city?: string | null;
  region?: string | null;
  precision: string;
  operator_comment?: string | null;
};

export type LocationReviewRejectRequest = {
  operator_comment?: string | null;
};

export type AdminSettings = {
  keyword_patterns: string[];
  ai_system_prompt: string;
};

export type AdminSettingsUpdate = Partial<AdminSettings>;

export type PipelineColor = "green" | "yellow" | "purple" | "red" | "orange" | "blue" | "slate" | "gray" | string;

export type PipelineStatus = {
  code: string;
  label: string;
  description: string;
  color: PipelineColor;
};

export type PipelineStage = {
  key: string;
  label: string;
  status: string;
  status_label: string;
  description: string;
  details: Record<string, unknown>;
};

export type ClassificationDebug = Classification & {
  id: number;
  message_id: number;
  raw_response?: string | null;
  created_at: string;
};

export type GeocodingResult = {
  id: number;
  query: string;
  provider: string;
  formatted_address?: string | null;
  lat?: number | null;
  lon?: number | null;
  precision: string;
  raw_response?: string | null;
  created_at: string;
};

export type PipelineMessage = {
  message: Message;
  source: Source;
  status: PipelineStatus;
  stages: PipelineStage[];
  raw_json?: Record<string, unknown> | null;
  classification?: ClassificationDebug | null;
  geocoding_result?: GeocodingResult | null;
  event?: EventItem | null;
  location_review?: LocationReview | null;
  cluster?: EventCluster | null;
};
