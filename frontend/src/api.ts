import type {
  EventCluster,
  EventItem,
  GeoSuggestion,
  AdminSettings,
  AdminSettingsUpdate,
  LocationReviewItem,
  LocationReviewRejectRequest,
  LocationReviewResolveRequest,
  PipelineMessage,
  Source,
  SourceCreate,
  SourceGroup,
  SourceGroupUpdate,
  SourceUpdate,
  SystemStatus,
  TelegramBulkConnectRequest,
  TelegramBulkConnectResult,
  TelegramConnectRequest,
  TelegramDialog
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api";

export function wsUrl(): string {
  const api = new URL(API_BASE);
  api.protocol = api.protocol === "https:" ? "wss:" : "ws:";
  api.pathname = `${api.pathname.replace(/\/$/, "")}/ws/events`;
  return api.toString();
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {})
    }
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export const api = {
  sources: () => request<Source[]>("/sources"),
  sourceGroups: () => request<SourceGroup[]>("/sources/groups"),
  events: () => request<EventItem[]>("/events?limit=200"),
  pipelineMessages: () => request<PipelineMessage[]>("/messages/pipeline?limit=300"),
  clusters: () => request<EventCluster[]>("/clusters/active?limit=200"),
  clusterEvents: (clusterId: number) => request<EventItem[]>(`/clusters/${clusterId}/events?limit=200`),
  locationReviews: (status = "pending") =>
    request<LocationReviewItem[]>(`/location-reviews?status=${encodeURIComponent(status)}&limit=200`),
  resolveLocationReview: (reviewId: number, payload: LocationReviewResolveRequest) =>
    request<EventItem>(`/location-reviews/${reviewId}/resolve`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  rejectLocationReview: (reviewId: number, payload: LocationReviewRejectRequest) =>
    request<EventItem>(`/location-reviews/${reviewId}/reject`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  adminSettings: () => request<AdminSettings>("/settings"),
  updateAdminSettings: (payload: AdminSettingsUpdate) =>
    request<AdminSettings>("/settings", {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  telegramDialogs: (query = "") => {
    const search = new URLSearchParams({ limit: "200" });
    if (query.trim()) {
      search.set("query", query.trim());
    }
    return request<TelegramDialog[]>(`/telegram/dialogs?${search.toString()}`);
  },
  connectTelegramDialog: (payload: TelegramConnectRequest) =>
    request<Source[]>("/telegram/connect", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  systemStatus: () => request<SystemStatus>("/system/status"),
  bulkConnectTelegram: (payload: TelegramBulkConnectRequest) =>
    request<TelegramBulkConnectResult>("/telegram/bulk-connect", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  createSource: (payload: SourceCreate) =>
    request<Source>("/sources", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  toggleSource: (source: Source) =>
    request<Source>(`/sources/${source.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !source.enabled })
    }),
  updateSource: (sourceId: number, payload: SourceUpdate) =>
    request<Source>(`/sources/${sourceId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  updateSourceGroup: (groupId: number, payload: SourceGroupUpdate) =>
    request<SourceGroup>(`/sources/groups/${groupId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  geoSuggestions: (kind: "city" | "region", query: string, region?: string | null) => {
    const search = new URLSearchParams({ kind, query });
    if (region?.trim()) {
      search.set("region", region.trim());
    }
    return request<GeoSuggestion[]>(`/geo/suggestions?${search.toString()}`);
  }
};
