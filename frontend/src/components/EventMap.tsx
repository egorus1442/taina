import { useEffect, useRef } from "react";
import L from "leaflet";
import type { EventCluster } from "../types";

type Props = {
  clusters: EventCluster[];
  selectedCluster: EventCluster | null;
  onSelect: (id: number) => void;
};

const MOSCOW: [number, number] = [55.751244, 37.618423];

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function EventMap({ clusters, selectedCluster, onSelect }: Props) {
  const nodeRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!nodeRef.current || mapRef.current) {
      return;
    }
    mapRef.current = L.map(nodeRef.current, {
      attributionControl: false,
      zoomControl: false
    }).setView(MOSCOW, 8);
    L.control.zoom({ position: "bottomright" }).addTo(mapRef.current);
    L.control.attribution({ prefix: false }).addTo(mapRef.current);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap"
    }).addTo(mapRef.current);
    layerRef.current = L.layerGroup().addTo(mapRef.current);
  }, []);

  useEffect(() => {
    if (!mapRef.current || !layerRef.current) {
      return;
    }
    layerRef.current.clearLayers();

    clusters.forEach((cluster) => {
      const circle = L.circle([cluster.lat, cluster.lon], {
        radius: cluster.match_radius_meters,
        color: riskColor(cluster.risk_level),
        fillColor: riskColor(cluster.risk_level),
        fillOpacity: cluster.id === selectedCluster?.id ? 0.16 : 0.08,
        opacity: cluster.id === selectedCluster?.id ? 0.9 : 0.45,
        weight: cluster.id === selectedCluster?.id ? 2 : 1
      });
      circle.on("click", () => onSelect(cluster.id));
      circle.addTo(layerRef.current!);

      const marker = L.marker([cluster.lat, cluster.lon], {
        icon: L.divIcon({
          className: "",
          html: `<button class="cluster-marker ${cluster.risk_level}" aria-label="cluster marker">${cluster.event_count}</button>`,
          iconSize: [34, 34],
          iconAnchor: [17, 17]
        })
      });
      marker.on("click", () => onSelect(cluster.id));
      const popupText = `<strong>Кластер #${cluster.id}</strong><br/>${escapeHtml(
        cluster.summary || "Гео-временная группа событий"
      )}<br/>${cluster.event_count} событий · ${cluster.source_count} источников · ${Math.round(
        cluster.confidence * 100
      )}%<br/>${formatTime(cluster.first_seen_at)} - ${formatTime(cluster.last_seen_at)}`;
      marker.bindPopup(popupText).addTo(layerRef.current!);
    });

    if (selectedCluster) {
      mapRef.current.setView([selectedCluster.lat, selectedCluster.lon], 12, { animate: true });
    } else if (clusters.length) {
      const bounds = L.latLngBounds(clusters.map((cluster) => [cluster.lat, cluster.lon]));
      mapRef.current.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
    }
  }, [clusters, selectedCluster, onSelect]);

  return (
    <div className="map-wrap">
      <div ref={nodeRef} className="map" />
    </div>
  );
}

function riskColor(riskLevel: string): string {
  if (riskLevel === "high") {
    return "#c23b22";
  }
  if (riskLevel === "medium") {
    return "#d99a1f";
  }
  return "#2f8c6c";
}
