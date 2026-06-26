import { FormEvent, useEffect, useMemo, useState } from "react";
import { CheckCircle2, Power, RefreshCw, Search, X } from "lucide-react";
import { api } from "../api";
import type {
  GeoSuggestion,
  Source,
  SourceGroup,
  SourceGroupUpdate,
  TelegramConnectRequest,
  TelegramDialog
} from "../types";

const CITY_OPTIONS = [
  "Москва",
  "Санкт-Петербург",
  "Ростов-на-Дону",
  "Воронеж",
  "Белгород",
  "Курск",
  "Брянск",
  "Краснодар",
  "Севастополь",
  "Казань",
  "Нижний Новгород",
  "Екатеринбург",
  "Самара",
  "Волгоград",
  "Пермь",
  "Уфа",
  "Челябинск",
  "Новосибирск",
  "Красноярск"
];

type Props = {
  sources: Source[];
  groups: SourceGroup[];
  onConnect: (payload: TelegramConnectRequest) => Promise<void>;
  onToggleSource: (source: Source) => Promise<void>;
  onUpdateGroup: (groupId: number, payload: SourceGroupUpdate) => Promise<void>;
  onRefreshSources: () => Promise<void>;
  onClose: () => void;
};

export function TelegramSourcePicker({
  sources,
  groups,
  onConnect,
  onToggleSource,
  onUpdateGroup,
  onRefreshSources,
  onClose
}: Props) {
  const [dialogs, setDialogs] = useState<TelegramDialog[]>([]);
  const [query, setQuery] = useState("");
  const [cityByDialog, setCityByDialog] = useState<Record<string, string>>({});
  const [regionByDialog, setRegionByDialog] = useState<Record<string, string>>({});
  const [collectionModeByDialog, setCollectionModeByDialog] = useState<Record<string, string>>({});
  const [pairedChatByDialog, setPairedChatByDialog] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sourceById = useMemo(() => new Map(sources.map((source) => [source.id, source])), [sources]);
  const chatDialogs = useMemo(() => dialogs.filter((dialog) => dialog.type === "chat"), [dialogs]);
  const groupBySourceId = useMemo(() => {
    const next = new Map<number, SourceGroup>();
    groups.forEach((group) => {
      if (group.channel_source_id) {
        next.set(group.channel_source_id, group);
      }
    });
    return next;
  }, [groups]);

  async function loadDialogs(nextQuery = query) {
    setLoading(true);
    setError(null);
    try {
      setDialogs(await api.telegramDialogs(nextQuery));
      await onRefreshSources();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Не удалось загрузить Telegram-диалоги");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadDialogs("");
  }, []);

  async function submitSearch(event: FormEvent) {
    event.preventDefault();
    await loadDialogs(query);
  }

  async function connect(dialog: TelegramDialog) {
    const city = (cityByDialog[dialog.telegram_id] || "").trim();
    if (!city) {
      return;
    }
    const region = (regionByDialog[dialog.telegram_id] || "").trim();
    const isChannel = dialog.type === "channel";
    const collectionMode = isChannel ? collectionModeByDialog[dialog.telegram_id] || "off" : "off";
    const pairedChat = collectionMode === "linked_chat_realtime"
      ? chatDialogs.find((item) => item.telegram_id === pairedChatByDialog[dialog.telegram_id])
      : undefined;
    if (collectionMode === "linked_chat_realtime" && !pairedChat) {
      return;
    }
    setBusyId(dialog.telegram_id);
    try {
      await onConnect({
        telegram_id: dialog.telegram_id,
        title: dialog.title,
        username: dialog.username,
        type: dialog.type,
        paired_chat_source_id: pairedChat?.source_id ?? null,
        paired_chat_telegram_id: pairedChat && !pairedChat.source_id ? pairedChat.telegram_id : null,
        paired_chat_title: pairedChat && !pairedChat.source_id ? pairedChat.title : null,
        paired_chat_username: pairedChat && !pairedChat.source_id ? pairedChat.username : null,
        city,
        region: region || null,
        enabled: true,
        monitor_channel_posts: isChannel,
        monitor_chat_messages: dialog.type === "chat" || collectionMode === "linked_chat_realtime",
        monitor_channel_comments: isChannel && collectionMode !== "off",
        comments_fetch_mode: collectionMode,
        comments_poll_interval_seconds: 5,
        comments_last_posts_limit: 5
      });
      await loadDialogs(query);
    } finally {
      setBusyId(null);
    }
  }

  function groupForDialog(dialog: TelegramDialog) {
    if (dialog.type === "channel" && dialog.source_id && groupBySourceId.has(dialog.source_id)) {
      return groupBySourceId.get(dialog.source_id);
    }
    return undefined;
  }

  async function toggle(dialog: TelegramDialog) {
    if (!dialog.source_id) {
      return;
    }
    const source = sourceById.get(dialog.source_id);
    if (!source) {
      return;
    }
    setBusyId(dialog.telegram_id);
    try {
      await onToggleSource(source);
      await loadDialogs(query);
    } finally {
      setBusyId(null);
    }
  }

  async function updateCollectionMode(group: SourceGroup, mode: string) {
    await onUpdateGroup(group.id, {
      monitor_channel_comments: mode !== "off",
      comments_fetch_mode: mode,
      monitor_chat_messages: mode === "linked_chat_realtime",
      comments_poll_interval_seconds: 5,
      comments_last_posts_limit: 5
    });
  }

  return (
    <div className="modal-backdrop">
      <section className="modal wide-modal">
        <div className="section-title">
          <div>
            <h2>Telegram-источники</h2>
            <p className="muted">Выбранные источники мониторятся, остальные игнорируются.</p>
          </div>
          <button className="icon-button" type="button" title="Закрыть" onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        {error && <div className="notice">{error}</div>}

        <form className="source-picker-controls" onSubmit={submitSearch}>
          <label>
            Поиск
            <div className="input-with-icon">
              <Search size={16} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} />
            </div>
          </label>
          <button className="icon-button control-button" type="submit" title="Обновить" disabled={loading}>
            <RefreshCw size={17} />
          </button>
        </form>

        <div className="dialog-list">
          {loading && <p className="empty">Загружаю доступные каналы и чаты...</p>}
          {!loading && !dialogs.length && <p className="empty">Ничего не найдено.</p>}
          {!loading &&
            dialogs.map((dialog) => {
              const group = groupForDialog(dialog);
              return (
                <article className={dialog.enabled ? "dialog-row monitored" : "dialog-row"} key={dialog.telegram_id}>
                  <div className="dialog-main">
                    <div className="dialog-title-line">
                      <strong>{dialog.title}</strong>
                      <span className={dialog.enabled ? "status-pill live" : "status-pill"}>
                        {dialog.enabled ? "мониторится" : dialog.is_connected ? "подключен" : "доступен"}
                      </span>
                    </div>
                    <span>
                      {dialog.telegram_id} · {dialog.type}
                      {dialog.username ? ` · @${dialog.username}` : ""}
                    </span>
                    <small>
                      {dialog.city || "город не задан"} · {dialog.processed_count} сообщений ·{" "}
                      {dialog.event_count} событий
                    </small>
                    {dialog.type === "channel" && group?.chat_source && (
                      <small className="linked-chat-line">Прикрепленный чат: {group.chat_source.title}</small>
                    )}
                  </div>

                  {dialog.is_connected ? (
                    <div className="dialog-actions">
                      {dialog.type === "channel" && group && (
                        <div className="channel-collection-settings">
                          <label>
                            Режим сбора
                            <select
                              value={group.monitor_channel_comments ? group.comments_fetch_mode : "off"}
                              onChange={(event) => updateCollectionMode(group, event.target.value)}
                            >
                              <option value="off">Только посты</option>
                              <option value="linked_chat_realtime" disabled={!group.chat_source_id}>
                                Посты + комментарии из группы
                              </option>
                              <option value="poll_replies">Посты + опрос комментариев</option>
                            </select>
                          </label>
                          {group.comments_fetch_mode === "linked_chat_realtime" && group.chat_source && (
                            <small className="linked-chat-line">Группа комментариев: {group.chat_source.title}</small>
                          )}
                          {group.comments_fetch_mode === "poll_replies" && (
                            <small>Опрос: каждые 5 секунд · последние 5 постов</small>
                          )}
                          {!group.chat_source_id && (
                            <small>Для режима из группы сначала создай пару канал + чат в списке источников.</small>
                          )}
                        </div>
                      )}
                      <button
                        className={dialog.enabled ? "secondary-button danger" : "secondary-button"}
                        type="button"
                        disabled={busyId === dialog.telegram_id}
                        onClick={() => toggle(dialog)}
                      >
                        {dialog.enabled ? <Power size={16} /> : <CheckCircle2 size={16} />}
                        {dialog.enabled ? "Отключить" : "Включить"}
                      </button>
                    </div>
                  ) : (
                    <div className="dialog-connect-stack">
                      <div className="dialog-connect">
                        <GeoSuggestInput
                          kind="city"
                          value={cityByDialog[dialog.telegram_id] || ""}
                          region={regionByDialog[dialog.telegram_id] || ""}
                          fallbackOptions={CITY_OPTIONS}
                          onChange={(value) =>
                            setCityByDialog((current) => ({
                              ...current,
                              [dialog.telegram_id]: value
                            }))
                          }
                          onSelect={(suggestion) => {
                            const nextCity = suggestion.city || suggestion.value;
                            setCityByDialog((current) => ({
                              ...current,
                              [dialog.telegram_id]: nextCity
                            }));
                            if (suggestion.region && !regionByDialog[dialog.telegram_id]?.trim()) {
                              setRegionByDialog((current) => ({
                                ...current,
                                [dialog.telegram_id]: suggestion.region || ""
                              }));
                            }
                          }}
                          placeholder="Город"
                        />
                        <GeoSuggestInput
                          kind="region"
                          value={regionByDialog[dialog.telegram_id] || ""}
                          onChange={(value) =>
                            setRegionByDialog((current) => ({
                              ...current,
                              [dialog.telegram_id]: value
                            }))
                          }
                          onSelect={(suggestion) =>
                            setRegionByDialog((current) => ({
                              ...current,
                              [dialog.telegram_id]: suggestion.region || suggestion.value
                            }))
                          }
                          placeholder="Область"
                        />
                        <button
                          className="primary-inline-button"
                          type="button"
                          disabled={
                            busyId === dialog.telegram_id ||
                            !cityByDialog[dialog.telegram_id]?.trim() ||
                            (dialog.type === "channel" &&
                              (collectionModeByDialog[dialog.telegram_id] || "off") === "linked_chat_realtime" &&
                              !pairedChatByDialog[dialog.telegram_id])
                          }
                          onClick={() => connect(dialog)}
                        >
                          <CheckCircle2 size={16} />
                          Выбрать
                        </button>
                      </div>
                      {dialog.type === "channel" && (
                        <div className="new-channel-collection-settings">
                          <select
                            value={collectionModeByDialog[dialog.telegram_id] || "off"}
                            onChange={(event) =>
                              setCollectionModeByDialog((current) => ({
                                ...current,
                                [dialog.telegram_id]: event.target.value
                              }))
                            }
                          >
                            <option value="off">Только посты</option>
                            <option value="linked_chat_realtime" disabled={!chatDialogs.length}>
                              Посты + комментарии из группы
                            </option>
                            <option value="poll_replies">Посты + опрос комментариев</option>
                          </select>
                          {(collectionModeByDialog[dialog.telegram_id] || "off") === "linked_chat_realtime" && (
                            <select
                              value={pairedChatByDialog[dialog.telegram_id] || ""}
                              onChange={(event) =>
                                setPairedChatByDialog((current) => ({
                                  ...current,
                                  [dialog.telegram_id]: event.target.value
                                }))
                              }
                            >
                              <option value="">Выбери чат</option>
                              {chatDialogs.map((chat) => (
                                <option key={chat.telegram_id} value={chat.telegram_id}>
                                  {chat.title}
                                  {chat.is_connected ? " · подключен" : ""}
                                </option>
                              ))}
                            </select>
                          )}
                          {(collectionModeByDialog[dialog.telegram_id] || "off") === "poll_replies" && (
                            <small>Опрос: каждые 5 секунд · последние 5 постов</small>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
        </div>
      </section>
    </div>
  );
}

type GeoSuggestInputProps = {
  kind: "city" | "region";
  value: string;
  placeholder: string;
  region?: string | null;
  fallbackOptions?: string[];
  onChange: (value: string) => void;
  onSelect: (suggestion: GeoSuggestion) => void;
};

function GeoSuggestInput({
  kind,
  value,
  placeholder,
  region,
  fallbackOptions = [],
  onChange,
  onSelect
}: GeoSuggestInputProps) {
  const [suggestions, setSuggestions] = useState<GeoSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const query = value.trim();
    if (query.length < 2) {
      setSuggestions([]);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    const timerId = window.setTimeout(() => {
      api
        .geoSuggestions(kind, query, region)
        .then((items) => {
          if (!cancelled) {
            setSuggestions(items);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setSuggestions([]);
          }
        })
        .finally(() => {
          if (!cancelled) {
            setLoading(false);
          }
        });
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timerId);
    };
  }, [kind, value, region]);

  const fallbackSuggestions = useMemo(
    () =>
      fallbackOptions
        .filter((option) => option.toLocaleLowerCase("ru").includes(value.trim().toLocaleLowerCase("ru")))
        .slice(0, 6)
        .map((option): GeoSuggestion => ({ value: option, city: option, region: null })),
    [fallbackOptions, value]
  );
  const visibleSuggestions = suggestions.length ? suggestions : fallbackSuggestions;

  function selectSuggestion(suggestion: GeoSuggestion) {
    onSelect(suggestion);
    setOpen(false);
  }

  return (
    <div className="geo-suggest">
      <input
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        placeholder={placeholder}
        autoComplete="off"
      />
      {open && (visibleSuggestions.length > 0 || loading) && (
        <div className="geo-suggest-menu">
          {visibleSuggestions.map((suggestion) => (
            <button
              key={`${suggestion.value}-${suggestion.region || ""}`}
              type="button"
              className="geo-suggest-option"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => selectSuggestion(suggestion)}
            >
              <span>{suggestion.value}</span>
              {suggestion.region && kind === "city" && <small>{suggestion.region}</small>}
            </button>
          ))}
          {loading && <div className="geo-suggest-loading">Ищу...</div>}
        </div>
      )}
    </div>
  );
}
