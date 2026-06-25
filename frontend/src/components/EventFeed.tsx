import { AlertTriangle, Circle, MapPin } from "lucide-react";
import type { EventItem } from "../types";

type Props = {
  events: EventItem[];
  selectedEventId: number | null;
  onSelect: (id: number) => void;
};

export function EventFeed({ events, selectedEventId, onSelect }: Props) {
  if (!events.length) {
    return <p className="empty">События появятся здесь сразу после обработки.</p>;
  }

  return (
    <div className="event-feed">
      {events.map((event) => (
        <button
          key={event.id}
          className={event.id === selectedEventId ? "event-item selected" : "event-item"}
          onClick={() => onSelect(event.id)}
        >
          <div className={`risk-dot ${event.classification.risk_level}`}>
            {event.classification.risk_level === "high" ? <AlertTriangle size={14} /> : <Circle size={10} />}
          </div>
          <div>
            <strong>{event.summary || event.classification.summary || "Возможное сообщение о БПЛА"}</strong>
            <span>
              <MapPin size={13} />
              {event.location.formatted_address || event.location.city}
            </span>
            <p>{event.message.text}</p>
            <small>
              {event.source.title} · {Math.round(event.classification.confidence * 100)}% ·{" "}
              {new Date(event.created_at).toLocaleString()}
            </small>
          </div>
        </button>
      ))}
    </div>
  );
}

