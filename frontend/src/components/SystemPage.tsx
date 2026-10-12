import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  FileUp,
  RadioTower,
  RefreshCw,
  Server
} from "lucide-react";
import { ChangeEvent, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import type { SystemStatus, TelegramBulkConnectResult } from "../types";

type Props = {
  status: SystemStatus | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => Promise<void>;
  onImported: () => Promise<void>;
};

export function SystemPage({ status, loading, error, onRefresh, onImported }: Props) {
  const [channelText, setChannelText] = useState("");
  const [city, setCity] = useState("Ижевск");
  const [region, setRegion] = useState("Удмуртская Республика");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [result, setResult] = useState<TelegramBulkConnectResult | null>(null);
  const channels = useMemo(() => parseChannels(channelText), [channelText]);
  const commentsPollingOk = Boolean(
    status?.telegram.comments_poll_last_at
      && Date.now() - new Date(status.telegram.comments_poll_last_at).getTime() < 30_000
  );

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    setChannelText(await file.text());
    event.target.value = "";
  }

  async function handleImport() {
    if (!channels.length || !city.trim()) {
      return;
    }
    setImporting(true);
    setImportError(null);
    setResult(null);
    try {
      const nextResult = await api.bulkConnectTelegram({
        channels,
        city: city.trim(),
        region: region.trim() || null
      });
      setResult(nextResult);
      await Promise.all([onImported(), onRefresh()]);
    } catch (nextError) {
      setImportError(nextError instanceof Error ? nextError.message : "Не удалось подключить каналы");
    } finally {
      setImporting(false);
    }
  }

  return (
    <section className="system-page">
      <div className="system-page-header">
        <div>
          <h2>Система</h2>
          <p>Состояние Telegram, polling, обработки и массовое подключение источников.</p>
        </div>
        <button className="secondary-button" type="button" onClick={onRefresh} disabled={loading}>
          <RefreshCw size={16} className={loading ? "spin" : ""} />
          Обновить
        </button>
      </div>

      {error && <div className="notice">{error}</div>}
      {!status && loading ? <p className="empty">Проверяю систему…</p> : null}

      {status && (
        <>
          <div className={`system-state system-state-${status.status}`}>
            {status.status === "ok" ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}
            <div>
              <strong>{statusLabel(status)}</strong>
              <span>
                {status.telegram.account ? `@${status.telegram.account}` : "Telegram-аккаунт не определён"}
                {` · проверено ${formatDate(status.checked_at)}`}
              </span>
            </div>
          </div>

          <div className="system-status-grid">
            <StatusCard
              icon={<RadioTower size={18} />}
              title="Telegram"
              value={status.telegram.connected ? "Подключён" : "Нет связи"}
              detail={`Запущен: ${formatDate(status.telegram.started_at)}`}
              tone={status.telegram.connected ? "ok" : "error"}
            />
            <StatusCard
              icon={<Activity size={18} />}
              title="Polling постов"
              value={status.telegram.source_poll_last_at ? "Работает" : "Ожидает цикл"}
              detail={`Последний цикл: ${formatDate(status.telegram.source_poll_last_at)}`}
              tone={status.telegram.source_poll_last_at ? "ok" : "neutral"}
            />
            <StatusCard
              icon={<Clock3 size={18} />}
              title="Polling комментариев"
              value={commentsPollingOk ? "Работает" : "Нет свежего цикла"}
              detail={`Последний цикл: ${formatDate(status.telegram.comments_poll_last_at)}`}
              tone={commentsPollingOk ? "ok" : "warning"}
            />
            <StatusCard
              icon={<Server size={18} />}
              title="Telegram-лимит"
              value={status.telegram.rate_limited ? "FloodWait" : "Ограничений нет"}
              detail={
                status.telegram.rate_limited
                  ? `Пауза до ${formatDate(status.telegram.flood_wait_until)}`
                  : `FloodWait за запуск: ${status.telegram.flood_wait_count}`
              }
              tone={status.telegram.rate_limited ? "warning" : "ok"}
            />
          </div>

          <div className="system-metrics">
            <Metric label="Активные источники" value={status.enabled_sources} />
            <Metric label="С комментариями" value={status.enabled_comment_sources} />
            <Metric label="Активные ветки" value={status.active_comment_threads} />
            <Metric label="Ветки в очереди" value={status.due_comment_threads} />
            <Metric label="Сообщения за час" value={status.messages_last_hour} />
            <Metric label="События за час" value={status.events_last_hour} />
            <Metric label="Ошибки webhook / 24 ч" value={status.failed_webhooks_last_24h} />
            <Metric label="Свободно на диске" value={`${status.disk_free_gb} ГБ`} />
          </div>

          {status.telegram.last_error && (
            <div className="system-warning">
              <AlertTriangle size={17} />
              <div>
                <strong>Последнее предупреждение</strong>
                <span>{status.telegram.last_error}</span>
              </div>
            </div>
          )}
        </>
      )}

      <section className="bulk-import-panel">
        <div className="bulk-import-heading">
          <div>
            <h2>Массовое подключение</h2>
            <p>Каналы будут включены: realtime для постов + адаптивный polling комментариев.</p>
          </div>
          <label className="file-button">
            <FileUp size={16} />
            Выбрать .txt
            <input type="file" accept=".txt,text/plain" onChange={handleFile} />
          </label>
        </div>

        <div className="bulk-location-grid">
          <label>
            Город
            <input value={city} onChange={(event) => setCity(event.target.value)} />
          </label>
          <label>
            Регион
            <input value={region} onChange={(event) => setRegion(event.target.value)} />
          </label>
        </div>

        <label className="bulk-channel-label">
          Каналы — по одному @username или t.me/username на строку
          <textarea
            value={channelText}
            onChange={(event) => setChannelText(event.target.value)}
            placeholder="@izhevsk_smi\n@sminews18\n@topsmi18"
          />
        </label>

        <div className="bulk-import-actions">
          <span>{channels.length} уникальных каналов</span>
          <button
            className="primary-inline-button"
            type="button"
            disabled={importing || !channels.length || !city.trim()}
            onClick={handleImport}
          >
            {importing ? <RefreshCw size={16} className="spin" /> : <RadioTower size={16} />}
            {importing ? "Подключаю…" : "Подключить все"}
          </button>
        </div>

        {importError && <div className="notice">{importError}</div>}
        {result && (
          <div className="bulk-result">
            <strong>Готово: {result.connected} новых, {result.updated} обновлено, {result.failed} ошибок.</strong>
            {result.failed > 0 && (
              <div className="bulk-failures">
                {result.items
                  .filter((item) => item.status === "failed")
                  .map((item) => (
                    <span key={item.channel}>{item.channel} — {item.detail}</span>
                  ))}
              </div>
            )}
          </div>
        )}
      </section>
    </section>
  );
}

function StatusCard({
  icon,
  title,
  value,
  detail,
  tone
}: {
  icon: ReactNode;
  title: string;
  value: string;
  detail: string;
  tone: "ok" | "warning" | "error" | "neutral";
}) {
  return (
    <article className={`system-status-card tone-${tone}`}>
      <div className="system-status-icon">{icon}</div>
      <div>
        <span>{title}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </article>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <article>
      <strong>{value}</strong>
      <span>{label}</span>
    </article>
  );
}

function parseChannels(value: string): string[] {
  const byKey = new Map<string, string>();
  value
    .split(/[\s,;]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .forEach((item) => byKey.set(item.toLowerCase(), item));
  return Array.from(byKey.values());
}

function statusLabel(status: SystemStatus): string {
  if (status.status === "ok") {
    return "Все системы работают";
  }
  if (status.status === "warning" && status.telegram.rate_limited) {
    return "Telegram временно ограничил запросы";
  }
  if (status.status === "disabled") {
    return "Telegram collector выключен";
  }
  return "Требуется внимание";
}

function formatDate(value?: string | null): string {
  if (!value) {
    return "ещё не было";
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(new Date(value));
}
