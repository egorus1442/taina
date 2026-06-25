import { AlertTriangle, Circle, Clock, Layers, MapPin } from "lucide-react";
import type { EventCluster, EventItem } from "../types";
import { EventFeed } from "./EventFeed";

type Props = {
  clusters: EventCluster[];
  selectedClusterId: number | null;
  clusterEvents: EventItem[];
  loadingEvents: boolean;
  onSelect: (id: number) => void;
};

export function ClusterFeed({ clusters, selectedClusterId, clusterEvents, loadingEvents, onSelect }: Props) {
  if (!clusters.length) {
    return <p className="empty">Активные кластеры появятся здесь после геокодированных событий.</p>;
  }

  return (
    <div className="cluster-feed">
      <div className="cluster-list">
        {clusters.map((cluster) => (
          <button
            key={cluster.id}
            className={cluster.id === selectedClusterId ? "cluster-item selected" : "cluster-item"}
            onClick={() => onSelect(cluster.id)}
          >
            <div className={`risk-dot ${cluster.risk_level}`}>
              {cluster.risk_level === "high" ? <AlertTriangle size={14} /> : <Circle size={10} />}
            </div>
            <div>
              <strong>{cluster.summary || `Кластер #${cluster.id}`}</strong>
              <span>
                <Layers size={13} />
                {cluster.event_count} событий · {cluster.source_count} источников ·{" "}
                {Math.round(cluster.confidence * 100)}%
              </span>
              <span>
                <MapPin size={13} />
                {formatRadius(cluster.match_radius_meters)} радиус группировки
              </span>
              <small>
                <Clock size={12} />
                {formatDateTime(cluster.first_seen_at)} - {formatDateTime(cluster.last_seen_at)}
              </small>
            </div>
          </button>
        ))}
      </div>

      {selectedClusterId && (
        <section className="cluster-detail">
          <div className="section-title compact-title">
            <h2>События кластера</h2>
            <span className="status-pill live">{clusterEvents.length}</span>
          </div>
          {loadingEvents ? (
            <p className="empty">Загружаю события кластера...</p>
          ) : (
            <EventFeed events={clusterEvents} selectedEventId={null} onSelect={() => undefined} />
          )}
        </section>
      )}
    </div>
  );
}

function formatRadius(value: number): string {
  if (value >= 1000) {
    return `${Math.round(value / 1000)} км`;
  }
  return `${Math.round(value)} м`;
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString([], {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}
