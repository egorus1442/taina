import { Activity, ChevronDown, Clock, FileSearch, MapPin, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import type { PipelineMessage, PipelineStage } from "../types";

type Props = {
  messages: PipelineMessage[];
  loading: boolean;
  error?: string | null;
  onRefresh: () => void;
};

const STATUS_ORDER = ["clustered", "needs_review", "ai_rejected", "pre_ai_stopped", "event_without_coordinates", "event_without_cluster", "event_not_created", "review_rejected"];

const FIELD_HELP: Record<string, string> = {
  id: "Внутренний идентификатор записи.",
  message_id: "Внутренний идентификатор сообщения в таблице messages.",
  telegram_message_id: "ID сообщения Telegram; у комментариев может быть синтетическим.",
  source_id: "ID источника, из которого пришло сообщение.",
  source_title: "Название канала, чата или группы источников в админке.",
  source_type: "Тип источника: канал, чат или другой подключенный тип.",
  text: "Исходный текст сообщения, который прошел первичный фильтр.",
  sender_name: "Имя или идентификатор отправителя, если оно было получено.",
  message_date: "Дата сообщения в Telegram.",
  created_at: "Дата создания записи в базе микросервиса.",
  raw_json: "Сырой технический контекст входа: ручной тест, poll, comment_id, discussion chat и другие признаки.",
  processed: "Признак, что сообщение прошло первичный этап обработки.",
  keyword_matched: "Признак, что сообщение прошло фильтр по ключевым словам.",
  matched_terms: "Какие слова нашли регулярки. Сейчас это поле не сохраняется в базе.",
  ai_processed: "Признак, что для сообщения сохранен результат AI-классификации.",
  is_drone_related: "AI считает сообщение связанным с БПЛА или похожей угрозой.",
  useful_for_alert: "AI считает сообщение полезным для создания события/алерта.",
  should_create_event: "Итоговое решение пайплайна после AI: создавать событие или остановиться.",
  confidence: "Уверенность AI от 0 до 1.",
  risk_level: "Уровень риска, который вернул AI.",
  event_type: "Тип события по классификации AI.",
  summary: "Краткое описание события от AI.",
  location_text: "Текст локации, который AI извлек из сообщения.",
  ai_location_text: "Текст локации, который AI извлек из сообщения.",
  location_precision: "Точность локации от AI: город, район, улица, адрес, объект и т.д.",
  ai_location_precision: "Точность локации, которую вернул AI.",
  needs_geocoding: "AI считает, что локацию нужно превратить в координаты.",
  comment: "Комментарий AI к классификации.",
  raw_response: "Полный сырой ответ AI или геокодера.",
  event_city: "Город, который попал в событие.",
  event_region: "Регион, который попал в событие.",
  event_district: "Район, который попал в событие.",
  event_address: "Адрес или улица, которые попали в событие.",
  event_landmark: "Ориентир, который попал в событие.",
  event_precision: "Итоговая точность локации в событии.",
  review_status: "Текущий статус ручной проверки локации.",
  review_reason: "Причина, по которой событие отправлено на ручную проверку.",
  geocoding_result_id: "ID результата геокодинга.",
  query: "Запрос, который отправлялся в геокодер.",
  provider: "Провайдер геокодинга.",
  formatted_address: "Адрес, который вернул геокодер.",
  lat: "Широта.",
  lon: "Долгота.",
  precision: "Точность результата.",
  review_id: "ID записи в очереди ручной проверки.",
  status: "Статус этапа или записи.",
  reason: "Причина статуса.",
  original_location_text: "Исходная локация, которую нужно проверить.",
  suggested_query: "Запрос, предложенный для ручного уточнения локации.",
  operator_location_text: "Локация, введенная оператором.",
  operator_formatted_address: "Адрес, полученный после решения оператора.",
  operator_comment: "Комментарий оператора.",
  resolved_at: "Время, когда ревью было решено.",
  cluster_id: "ID кластера, куда попало событие.",
  cluster_status: "Статус кластера.",
  cluster_event_count: "Сколько событий входит в кластер.",
  cluster_source_count: "Сколько источников участвует в кластере.",
  note: "Пояснение к текущему состоянию этапа."
};

export function PipelineMessagesPage({ messages, loading, error, onRefresh }: Props) {
  const [activeStatus, setActiveStatus] = useState<string>("all");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const counts = useMemo(() => {
    const next = new Map<string, number>();
    messages.forEach((item) => next.set(item.status.code, (next.get(item.status.code) ?? 0) + 1));
    return next;
  }, [messages]);

  const statuses = useMemo(() => {
    const byCode = new Map(messages.map((item) => [item.status.code, item.status]));
    return Array.from(byCode.values()).sort((left, right) => {
      const leftIndex = STATUS_ORDER.indexOf(left.code);
      const rightIndex = STATUS_ORDER.indexOf(right.code);
      return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
    });
  }, [messages]);

  const visibleMessages = activeStatus === "all" ? messages : messages.filter((item) => item.status.code === activeStatus);

  return (
    <section className="review-page pipeline-page">
      <div className="review-page-header">
        <div>
          <h2>Диагностика сообщений</h2>
          <p>Сообщения, прошедшие первичный фильтр по ключевым словам.</p>
        </div>
        <button className="secondary-button" type="button" onClick={onRefresh} disabled={loading}>
          <RefreshCw size={16} />
          Обновить
        </button>
      </div>

      {error && <div className="notice">{error}</div>}

      <div className="pipeline-status-grid" aria-label="Статусы сообщений">
        <button
          className={activeStatus === "all" ? "pipeline-status-card active" : "pipeline-status-card"}
          type="button"
          onClick={() => setActiveStatus("all")}
        >
          <span className="pipeline-status-dot color-slate" />
          <strong>{messages.length}</strong>
          <span>Все после ключей</span>
          <small>Вся выборка кандидатов, которые не были отброшены keyword-фильтром.</small>
        </button>
        {statuses.map((status) => (
          <button
            key={status.code}
            className={activeStatus === status.code ? "pipeline-status-card active" : "pipeline-status-card"}
            type="button"
            onClick={() => setActiveStatus(status.code)}
          >
            <span className={`pipeline-status-dot color-${status.color}`} />
            <strong>{counts.get(status.code) ?? 0}</strong>
            <span>{status.label}</span>
            <small>{status.description}</small>
          </button>
        ))}
      </div>

      {loading ? (
        <p className="empty">Загрузка сообщений...</p>
      ) : !visibleMessages.length ? (
        <p className="empty">Сообщений с выбранным статусом пока нет.</p>
      ) : (
        <div className="pipeline-message-list">
          {visibleMessages.map((item) => (
            <PipelineMessageCard
              key={item.message.id}
              item={item}
              expanded={expandedId === item.message.id}
              onToggle={() => setExpandedId((current) => (current === item.message.id ? null : item.message.id))}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function PipelineMessageCard({
  item,
  expanded,
  onToggle
}: {
  item: PipelineMessage;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <article className={`pipeline-message-card color-${item.status.color}`}>
      <button className="pipeline-message-summary" type="button" onClick={onToggle}>
        <span className={`pipeline-status-dot color-${item.status.color}`} />
        <span className="pipeline-message-main">
          <strong>{item.classification?.summary || item.message.text || "Сообщение без текста"}</strong>
          <small>
            <FileSearch size={13} />
            {item.source.title} · #{item.message.id}
          </small>
        </span>
        <span className="pipeline-message-meta">
          <span className={`pipeline-badge color-${item.status.color}`}>{item.status.label}</span>
          <span>
            <Clock size={13} />
            {formatDate(item.message.message_date || item.message.created_at)}
          </span>
          {item.event?.location?.formatted_address || item.event?.location?.city ? (
            <span>
              <MapPin size={13} />
              {item.event.location.formatted_address || item.event.location.city}
            </span>
          ) : null}
        </span>
        <ChevronDown className={expanded ? "chevron open" : "chevron"} size={18} />
      </button>

      {expanded && (
        <div className="pipeline-message-details">
          <section className="pipeline-detail-section">
            <h3>Сообщение</h3>
            <p>{item.message.text}</p>
            <FieldGrid
              values={{
                message_id: item.message.id,
                telegram_message_id: item.message.telegram_message_id,
                source_title: item.source.title,
                source_type: item.source.type,
                sender_name: item.message.sender_name,
                message_date: item.message.message_date,
                created_at: item.message.created_at,
                raw_json: item.raw_json
              }}
            />
          </section>

          <section className="pipeline-detail-section">
            <h3>Этапы</h3>
            <div className="pipeline-stage-list">
              {item.stages.map((stage) => (
                <StageBlock key={stage.key} stage={stage} />
              ))}
            </div>
          </section>
        </div>
      )}
    </article>
  );
}

function StageBlock({ stage }: { stage: PipelineStage }) {
  return (
    <details className={`pipeline-stage stage-${stage.status}`} open={stage.status !== "passed" && stage.status !== "not_applicable"}>
      <summary>
        <span>
          <Activity size={14} />
          {stage.label}
        </span>
        <strong>{stage.status_label}</strong>
      </summary>
      <p>{stage.description}</p>
      <FieldGrid values={stage.details} />
    </details>
  );
}

function FieldGrid({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values);
  if (!entries.length) {
    return <p className="muted">Деталей по этому этапу нет.</p>;
  }

  return (
    <div className="pipeline-field-grid">
      {entries.map(([key, value]) => (
        <div className="pipeline-field-row" key={key}>
          <div>
            <strong>{key}</strong>
            <small>{FIELD_HELP[key] || "Техническое поле пайплайна."}</small>
          </div>
          <code>{formatValue(value)}</code>
        </div>
      ))}
    </div>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "нет даты";
  }
  return new Date(value).toLocaleString();
}
