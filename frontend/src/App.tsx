import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, FileSearch, Layers, MapPin, Plus, Radio, ShieldAlert, SlidersHorizontal } from "lucide-react";
import { api, wsUrl } from "./api";
import { AdminSettingsPanel } from "./components/AdminSettingsPanel";
import { ClusterFeed } from "./components/ClusterFeed";
import { EventMap } from "./components/EventMap";
import { PipelineMessagesPage } from "./components/PipelineMessagesPage";
import { ReviewQueue } from "./components/ReviewQueue";
import { SourceList } from "./components/SourceList";
import { SystemPage } from "./components/SystemPage";
import { TelegramSourcePicker } from "./components/TelegramSourcePicker";
import type {
  EventCluster,
  EventItem,
  AdminSettings,
  LocationReviewItem,
  LocationReviewResolveRequest,
  PipelineMessage,
  Source,
  SourceUpdate,
  SourceGroup,
  SourceGroupUpdate,
  SystemStatus,
  TelegramConnectRequest
} from "./types";

export function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceGroups, setSourceGroups] = useState<SourceGroup[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [clusters, setClusters] = useState<EventCluster[]>([]);
  const [locationReviews, setLocationReviews] = useState<LocationReviewItem[]>([]);
  const [pipelineMessages, setPipelineMessages] = useState<PipelineMessage[]>([]);
  const [adminSettings, setAdminSettings] = useState<AdminSettings | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [selectedClusterId, setSelectedClusterId] = useState<number | null>(null);
  const [clusterEvents, setClusterEvents] = useState<EventItem[]>([]);
  const [loadingClusterEvents, setLoadingClusterEvents] = useState(false);
  const [loadingReviews, setLoadingReviews] = useState(false);
  const [loadingPipelineMessages, setLoadingPipelineMessages] = useState(false);
  const [loadingSettings, setLoadingSettings] = useState(false);
  const [loadingSystemStatus, setLoadingSystemStatus] = useState(false);
  const [systemError, setSystemError] = useState<string | null>(null);
  const [activePage, setActivePage] = useState<"map" | "reviews" | "pipeline" | "system">("map");
  const [rightTab, setRightTab] = useState<"clusters" | "settings">("clusters");
  const [showSourcePicker, setShowSourcePicker] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedClusterIdRef = useRef<number | null>(null);

  async function loadInitialData() {
    try {
      const [sourceData, groupData, clusterData, eventData, reviewData, settingsData] = await Promise.all([
        api.sources(),
        api.sourceGroups(),
        api.clusters(),
        api.events(),
        api.locationReviews(),
        api.adminSettings()
      ]);
      setSources(sourceData);
      setSourceGroups(groupData);
      setClusters(sortClusters(clusterData));
      setEvents(eventData);
      setLocationReviews(reviewData);
      setAdminSettings(settingsData);
      setSelectedClusterId((current) => current ?? clusterData[0]?.id ?? null);
      setError(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Не удалось загрузить данные");
    }
  }

  async function refreshLiveData() {
    const [sourceData, clusterData, eventData, reviewData] = await Promise.all([
      api.sources(),
      api.clusters(),
      api.events(),
      api.locationReviews()
    ]);
    setSources(sourceData);
    setClusters(sortClusters(clusterData));
    setEvents(eventData);
    setLocationReviews(reviewData);
    setError(null);
    setSelectedClusterId((current) => {
      if (current && clusterData.some((cluster) => cluster.id === current)) {
        return current;
      }
      return clusterData[0]?.id ?? null;
    });
  }

  async function refreshSourcesAndGroups() {
    const [sourceData, groupData] = await Promise.all([api.sources(), api.sourceGroups()]);
    setSources(sourceData);
    setSourceGroups(groupData);
  }

  async function loadPipelineMessages() {
    setLoadingPipelineMessages(true);
    try {
      const data = await api.pipelineMessages();
      setPipelineMessages(data);
      setError(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Не удалось загрузить диагностику сообщений");
    } finally {
      setLoadingPipelineMessages(false);
    }
  }

  async function loadSystemStatus() {
    setLoadingSystemStatus(true);
    try {
      setSystemStatus(await api.systemStatus());
      setSystemError(null);
    } catch (nextError) {
      setSystemError(nextError instanceof Error ? nextError.message : "Не удалось проверить систему");
    } finally {
      setLoadingSystemStatus(false);
    }
  }

  useEffect(() => {
    loadInitialData();
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      refreshLiveData().catch((nextError) => {
        setError(nextError instanceof Error ? nextError.message : "Не удалось обновить данные карты");
      });
    }, 10000);
    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    selectedClusterIdRef.current = selectedClusterId;
  }, [selectedClusterId]);

  useEffect(() => {
    const socket = new WebSocket(wsUrl());
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as EventItem;
      setEvents((current) => [event, ...current.filter((item) => item.id !== event.id)].slice(0, 200));
      if (event.cluster) {
        setClusters((current) => upsertCluster(current, event.cluster!));
        setSelectedClusterId(event.cluster.id);
        setClusterEvents((current) =>
          event.cluster_id === selectedClusterIdRef.current
            ? [event, ...current.filter((item) => item.id !== event.id)].slice(0, 200)
            : current
        );
      }
      setSources((current) =>
        current.map((source) =>
          source.id === event.source.id
            ? { ...source, event_count: event.source.event_count, processed_count: event.source.processed_count }
            : source
        )
      );
    };
    socket.onerror = () => setError("WebSocket не подключился. Проверь backend и VITE_API_BASE.");
    return () => socket.close();
  }, []);

  const selectedCluster = useMemo(
    () => clusters.find((cluster) => cluster.id === selectedClusterId) ?? clusters[0] ?? null,
    [clusters, selectedClusterId]
  );
  const activeSignalCount = useMemo(
    () => clusters.reduce((total, cluster) => total + cluster.event_count, 0),
    [clusters]
  );

  useEffect(() => {
    if (!selectedCluster?.id) {
      setClusterEvents([]);
      return;
    }

    let cancelled = false;
    setLoadingClusterEvents(true);
    api
      .clusterEvents(selectedCluster.id)
      .then((data) => {
        if (!cancelled) {
          setClusterEvents(data);
        }
      })
      .catch((nextError) => {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "Не удалось загрузить события кластера");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingClusterEvents(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedCluster?.id]);

  useEffect(() => {
    if (activePage !== "reviews") {
      return;
    }
    let cancelled = false;
    setLoadingReviews(true);
    api
      .locationReviews()
      .then((data) => {
        if (!cancelled) {
          setLocationReviews(data);
        }
      })
      .catch((nextError) => {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "Не удалось загрузить очередь ревью");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingReviews(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [activePage]);

  useEffect(() => {
    if (activePage !== "pipeline") {
      return;
    }
    loadPipelineMessages();
  }, [activePage]);

  useEffect(() => {
    if (activePage !== "system") {
      return;
    }
    loadSystemStatus();
    const intervalId = window.setInterval(loadSystemStatus, 5000);
    return () => window.clearInterval(intervalId);
  }, [activePage]);

  useEffect(() => {
    if (rightTab !== "settings") {
      return;
    }
    let cancelled = false;
    setLoadingSettings(true);
    api
      .adminSettings()
      .then((data) => {
        if (!cancelled) {
          setAdminSettings(data);
        }
      })
      .catch((nextError) => {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "Не удалось загрузить настройки");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingSettings(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [rightTab]);

  async function connectTelegramDialog(payload: TelegramConnectRequest) {
    const createdSources = await api.connectTelegramDialog(payload);
    const nextGroups = await api.sourceGroups();
    setSources((current) => {
      const byId = new Map(current.map((source) => [source.id, source]));
      createdSources.forEach((source) => byId.set(source.id, source));
      return Array.from(byId.values()).sort((left, right) => left.title.localeCompare(right.title));
    });
    setSourceGroups(nextGroups);
  }

  async function toggleSource(source: Source) {
    const updated = await api.toggleSource(source);
    setSources((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    setSourceGroups(await api.sourceGroups());
  }

  async function updateSource(sourceId: number, payload: SourceUpdate) {
    const updated = await api.updateSource(sourceId, payload);
    setSources((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    setSourceGroups(await api.sourceGroups());
  }

  async function toggleSourceGroup(group: SourceGroup) {
    const updated = await api.updateSourceGroup(group.id, { enabled: !group.enabled });
    setSourceGroups((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    setSources((current) =>
      current.map((source) =>
        source.id === updated.channel_source_id || source.id === updated.chat_source_id
          ? { ...source, enabled: updated.enabled }
          : source
      )
    );
  }

  async function updateSourceGroup(groupId: number, payload: SourceGroupUpdate) {
    const updated = await api.updateSourceGroup(groupId, payload);
    setSourceGroups((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    setSources((current) =>
      current.map((source) =>
        source.id === updated.channel_source_id || source.id === updated.chat_source_id
          ? { ...source, enabled: updated.enabled }
          : source
      )
    );
  }

  async function resolveLocationReview(reviewId: number, payload: LocationReviewResolveRequest) {
    const event = await api.resolveLocationReview(reviewId, payload);
    setLocationReviews((current) => current.filter((review) => review.id !== reviewId));
    setEvents((current) => [event, ...current.filter((item) => item.id !== event.id)].slice(0, 200));
    if (event.cluster) {
      setClusters((current) => upsertCluster(current, event.cluster!));
      setSelectedClusterId(event.cluster.id);
      setActivePage("map");
      setRightTab("clusters");
    }
    await refreshLiveData();
  }

  async function rejectLocationReview(reviewId: number, operatorComment: string) {
    const event = await api.rejectLocationReview(reviewId, { operator_comment: operatorComment || null });
    setLocationReviews((current) => current.filter((review) => review.id !== reviewId));
    setEvents((current) => [event, ...current.filter((item) => item.id !== event.id)].slice(0, 200));
    await refreshLiveData();
  }

  async function saveAdminSettings(nextSettings: AdminSettings) {
    const updated = await api.updateAdminSettings(nextSettings);
    setAdminSettings(updated);
    setError(null);
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <header className="brand">
          <ShieldAlert size={28} />
          <div>
            <h1>Safety Monitor</h1>
            <p>Telegram OSINT dashboard</p>
          </div>
        </header>

        <section className="metric-grid" aria-label="Сводка">
          <div className="metric">
            <Radio size={18} />
            <strong>{sources.length}</strong>
            <span>источников</span>
          </div>
          <div className="metric">
            <MapPin size={18} />
            <strong>{clusters.length}</strong>
            <span>кластеров</span>
          </div>
          <div className="metric">
            <Layers size={18} />
            <strong>{activeSignalCount}</strong>
            <span>активных сигналов</span>
          </div>
        </section>

        <nav className="sidebar-nav" aria-label="Разделы">
          <button
            className={activePage === "map" ? "sidebar-nav-button active" : "sidebar-nav-button"}
            type="button"
            onClick={() => setActivePage("map")}
          >
            <Layers size={17} />
            Карта
          </button>
          <button
            className={activePage === "reviews" ? "sidebar-nav-button active" : "sidebar-nav-button"}
            type="button"
            onClick={() => setActivePage("reviews")}
          >
            <ShieldAlert size={17} />
            Ревью
            <span>{locationReviews.length}</span>
          </button>
          <button
            className={activePage === "pipeline" ? "sidebar-nav-button active" : "sidebar-nav-button"}
            type="button"
            onClick={() => setActivePage("pipeline")}
          >
            <FileSearch size={17} />
            Сообщения
            <span>{pipelineMessages.length}</span>
          </button>
          <button
            className={activePage === "system" ? "sidebar-nav-button active" : "sidebar-nav-button"}
            type="button"
            onClick={() => setActivePage("system")}
          >
            <Activity size={17} />
            Система
          </button>
        </nav>

        <div className="section-title">
          <h2>Источники</h2>
          <button className="icon-button" title="Выбрать Telegram-источники" onClick={() => setShowSourcePicker(true)}>
            <Plus size={18} />
          </button>
        </div>

        <SourceList
          sources={sources}
          groups={sourceGroups}
          onToggle={toggleSource}
          onToggleGroup={toggleSourceGroup}
          onUpdateSource={updateSource}
          onUpdateGroup={updateSourceGroup}
        />
      </aside>

      {activePage === "reviews" ? (
        <section className="review-page">
          {error && <div className="notice">{error}</div>}
          <div className="review-page-header">
            <div>
              <h2>Ревью событий</h2>
              <p>Проверка событий, которым нужна ручная геопривязка или отклонение.</p>
            </div>
            <span className="status-pill live">{locationReviews.length}</span>
          </div>
          <ReviewQueue
            reviews={locationReviews}
            loading={loadingReviews}
            onResolve={resolveLocationReview}
            onReject={rejectLocationReview}
            layout="page"
          />
        </section>
      ) : activePage === "pipeline" ? (
        <PipelineMessagesPage
          messages={pipelineMessages}
          loading={loadingPipelineMessages}
          error={error}
          onRefresh={loadPipelineMessages}
        />
      ) : activePage === "system" ? (
        <SystemPage
          status={systemStatus}
          loading={loadingSystemStatus}
          error={systemError}
          onRefresh={loadSystemStatus}
          onImported={refreshSourcesAndGroups}
        />
      ) : (
        <>
          <section className="map-pane">
            <EventMap clusters={clusters} selectedCluster={selectedCluster} onSelect={setSelectedClusterId} />
          </section>

          <aside className="right-pane">
            {error && <div className="notice">{error}</div>}

            <section className="panel feed-panel">
              <div className="panel-tabs" role="tablist" aria-label="Правая панель">
                <button
                  className={rightTab === "clusters" ? "panel-tab active" : "panel-tab"}
                  type="button"
                  onClick={() => setRightTab("clusters")}
                >
                  <Layers size={16} />
                  Кластеры
                  <span>{clusters.length}</span>
                </button>
                <button
                  className={rightTab === "settings" ? "panel-tab active" : "panel-tab"}
                  type="button"
                  onClick={() => setRightTab("settings")}
                >
                  <SlidersHorizontal size={16} />
                  Настройки
                </button>
              </div>
              {rightTab === "clusters" ? (
                <ClusterFeed
                  clusters={clusters}
                  selectedClusterId={selectedCluster?.id ?? null}
                  clusterEvents={clusterEvents}
                  loadingEvents={loadingClusterEvents}
                  onSelect={setSelectedClusterId}
                />
              ) : (
                <AdminSettingsPanel settings={adminSettings} loading={loadingSettings} onSave={saveAdminSettings} />
              )}
            </section>
          </aside>
        </>
      )}

      {showSourcePicker && (
        <TelegramSourcePicker
          sources={sources}
          groups={sourceGroups}
          onConnect={connectTelegramDialog}
          onToggleSource={toggleSource}
          onUpdateGroup={updateSourceGroup}
          onRefreshSources={refreshSourcesAndGroups}
          onClose={() => setShowSourcePicker(false)}
        />
      )}
    </main>
  );
}

function upsertCluster(clusters: EventCluster[], cluster: EventCluster): EventCluster[] {
  const exists = clusters.some((item) => item.id === cluster.id);
  const next = exists ? clusters.map((item) => (item.id === cluster.id ? cluster : item)) : [cluster, ...clusters];
  return sortClusters(next).slice(0, 200);
}

function sortClusters(clusters: EventCluster[]): EventCluster[] {
  return [...clusters].sort(
    (left, right) => new Date(right.last_seen_at).getTime() - new Date(left.last_seen_at).getTime()
  );
}
