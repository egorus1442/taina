import { Check, Link2, PauseCircle, Power, RadioTower, Settings2, X } from "lucide-react";
import { useState } from "react";
import { CITY_OPTIONS } from "../geoOptions";
import { GeoSuggestInput } from "./GeoSuggestInput";
import type { Source, SourceGroup, SourceGroupUpdate, SourceUpdate } from "../types";

type Props = {
  sources: Source[];
  groups: SourceGroup[];
  onToggle: (source: Source) => Promise<void>;
  onToggleGroup: (group: SourceGroup) => Promise<void>;
  onUpdateSource: (sourceId: number, payload: SourceUpdate) => Promise<void>;
  onUpdateGroup: (groupId: number, payload: SourceGroupUpdate) => Promise<void>;
};

export function SourceList({
  sources,
  groups,
  onToggle,
  onToggleGroup,
  onUpdateSource,
  onUpdateGroup
}: Props) {
  const chatSources = sources.filter((source) => source.type === "chat");
  const pairedChatSourceIds = new Set(
    groups
      .filter((group) => group.channel_source_id && group.chat_source_id)
      .map((group) => group.chat_source_id)
      .filter((sourceId): sourceId is number => sourceId != null)
  );
  const availableChatSources = chatSources.filter((source) => !pairedChatSourceIds.has(source.id));
  const channelGroups = groups.filter((group) => group.channel_source);
  const unpairedChannelGroups = channelGroups.filter((group) => !group.chat_source_id);
  const visibleGroups = groups.filter(
    (group) => !(group.chat_source_id && !group.channel_source_id && pairedChatSourceIds.has(group.chat_source_id))
  );
  const [pairChannelGroupId, setPairChannelGroupId] = useState("");
  const [pairChatSourceId, setPairChatSourceId] = useState("");
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editCity, setEditCity] = useState("");
  const [editRegion, setEditRegion] = useState("");
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const canCreatePair = Boolean(pairChannelGroupId && pairChatSourceId);

  if (!sources.length && !groups.length) {
    return <p className="empty">Пока нет источников. Добавь канал или чат для теста.</p>;
  }

  if (groups.length) {
    return (
      <div className="source-list">
        {unpairedChannelGroups.length > 0 && availableChatSources.length > 0 && (
          <section className="source-pair-panel">
            <div className="source-pair-title">
              <Link2 size={16} />
              <strong>Создать пару</strong>
            </div>
            <select value={pairChannelGroupId} onChange={(event) => setPairChannelGroupId(event.target.value)}>
              <option value="">Канал</option>
              {unpairedChannelGroups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.channel_source?.title}
                </option>
              ))}
            </select>
            <select value={pairChatSourceId} onChange={(event) => setPairChatSourceId(event.target.value)}>
              <option value="">Чат</option>
              {availableChatSources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.title}
                </option>
              ))}
            </select>
            <button
              className="primary-inline-button"
              type="button"
              disabled={!canCreatePair}
              onClick={() => {
                if (!canCreatePair) {
                  return;
                }
                onUpdateGroup(Number(pairChannelGroupId), {
                  chat_source_id: Number(pairChatSourceId),
                  monitor_chat_messages: true,
                  monitor_channel_comments: true,
                  comments_fetch_mode: "linked_chat_realtime",
                  comments_poll_interval_seconds: 5,
                  comments_last_posts_limit: 5
                });
                setPairChannelGroupId("");
                setPairChatSourceId("");
              }}
            >
              <Link2 size={16} />
              Создать пару
            </button>
          </section>
        )}

        {visibleGroups.map((group) => (
          <article className={group.enabled ? "source-row grouped" : "source-row grouped disabled"} key={group.id}>
            <div className="source-icon">
              <RadioTower size={18} />
            </div>
            <div className="source-body">
              <div className="source-title-line">
                <strong>{group.title}</strong>
                <span className={group.enabled ? "status-pill live" : "status-pill"}>
                  {group.enabled ? "live" : "off"}
                </span>
              </div>
              <span>{sourcePairLabel(group)}</span>
              <small>{sourceGroupMeta(group)}</small>
              {editingKey === groupEditKey(group) && (
                <LocationEditor
                  city={editCity}
                  region={editRegion}
                  saving={savingKey === groupEditKey(group)}
                  onCityChange={setEditCity}
                  onRegionChange={setEditRegion}
                  onCancel={() => setEditingKey(null)}
                  onSave={async () => {
                    const city = editCity.trim();
                    if (!city) {
                      return;
                    }
                    const region = editRegion.trim() || null;
                    const key = groupEditKey(group);
                    setSavingKey(key);
                    try {
                      await onUpdateGroup(group.id, { city, region });
                      const sourceIds = [group.channel_source_id, group.chat_source_id].filter(
                        (sourceId): sourceId is number => sourceId != null
                      );
                      await Promise.all(sourceIds.map((sourceId) => onUpdateSource(sourceId, { city, region })));
                      setEditingKey(null);
                    } finally {
                      setSavingKey(null);
                    }
                  }}
                />
              )}
              {group.channel_source && (
                <div className="source-group-controls">
                  <select
                    value={group.monitor_channel_comments ? group.comments_fetch_mode : "off"}
                    onChange={(event) => {
                      const mode = event.target.value;
                      onUpdateGroup(group.id, {
                        monitor_channel_comments: mode !== "off",
                        comments_fetch_mode: mode,
                        monitor_chat_messages: mode === "linked_chat_realtime",
                        comments_poll_interval_seconds: 5,
                        comments_last_posts_limit: 5
                      });
                    }}
                  >
                    <option value="off">Только посты</option>
                    <option value="linked_chat_realtime" disabled={!group.chat_source_id}>
                      Комментарии из группы
                    </option>
                    <option value="poll_replies">Комментарии под постами</option>
                  </select>
                  {group.comments_fetch_mode === "linked_chat_realtime" && (
                    <select
                      value={group.chat_source_id ?? ""}
                      onChange={(event) =>
                        onUpdateGroup(group.id, {
                          chat_source_id: Number(event.target.value),
                          monitor_chat_messages: true,
                          monitor_channel_comments: true,
                          comments_fetch_mode: "linked_chat_realtime"
                        })
                      }
                    >
                      <option value="" disabled>
                        Чат
                      </option>
                      {chatSources.map((source) => (
                        <option key={source.id} value={source.id}>
                          {source.title}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}
            </div>
            <div className="source-actions">
              <button
                className="icon-button"
                title="Редактировать город и область"
                onClick={() => {
                  setEditingKey(groupEditKey(group));
                  setEditCity(group.city);
                  setEditRegion(group.region || "");
                }}
              >
                <Settings2 size={16} />
              </button>
              <button
                className={group.enabled ? "icon-button active" : "icon-button"}
                title={group.enabled ? "Отключить группу" : "Включить группу"}
                onClick={() => onToggleGroup(group)}
              >
                {group.enabled ? <Power size={16} /> : <PauseCircle size={16} />}
              </button>
            </div>
          </article>
        ))}
      </div>
    );
  }

  return (
    <div className="source-list">
      {sources.map((source) => (
        <article className={source.enabled ? "source-row" : "source-row disabled"} key={source.id}>
          <div className="source-icon">
            <RadioTower size={18} />
          </div>
          <div className="source-body">
            <div className="source-title-line">
              <strong>{source.title}</strong>
              <span className={source.enabled ? "status-pill live" : "status-pill"}>
                {source.enabled ? "live" : "off"}
              </span>
            </div>
            <span>
              {source.telegram_id} · {source.city}
              {source.region ? ` · ${source.region}` : ""} · {source.type}
            </span>
            <small>
              {source.processed_count} сообщений · {source.event_count} событий
            </small>
            {editingKey === sourceEditKey(source) && (
              <LocationEditor
                city={editCity}
                region={editRegion}
                saving={savingKey === sourceEditKey(source)}
                onCityChange={setEditCity}
                onRegionChange={setEditRegion}
                onCancel={() => setEditingKey(null)}
                onSave={async () => {
                  const city = editCity.trim();
                  if (!city) {
                    return;
                  }
                  const region = editRegion.trim() || null;
                  const key = sourceEditKey(source);
                  setSavingKey(key);
                  try {
                    await onUpdateSource(source.id, { city, region });
                    setEditingKey(null);
                  } finally {
                    setSavingKey(null);
                  }
                }}
              />
            )}
          </div>
          <div className="source-actions">
            <button
              className="icon-button"
              title="Редактировать город и область"
              onClick={() => {
                setEditingKey(sourceEditKey(source));
                setEditCity(source.city);
                setEditRegion(source.region || "");
              }}
            >
              <Settings2 size={16} />
            </button>
            <button
              className={source.enabled ? "icon-button active" : "icon-button"}
              title={source.enabled ? "Отключить" : "Включить"}
              onClick={() => onToggle(source)}
            >
              {source.enabled ? <Power size={16} /> : <PauseCircle size={16} />}
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}

type LocationEditorProps = {
  city: string;
  region: string;
  saving: boolean;
  onCityChange: (value: string) => void;
  onRegionChange: (value: string) => void;
  onSave: () => Promise<void>;
  onCancel: () => void;
};

function LocationEditor({
  city,
  region,
  saving,
  onCityChange,
  onRegionChange,
  onSave,
  onCancel
}: LocationEditorProps) {
  return (
    <div className="source-location-editor">
      <GeoSuggestInput
        kind="city"
        value={city}
        region={region}
        fallbackOptions={CITY_OPTIONS}
        onChange={onCityChange}
        onSelect={(suggestion) => {
          onCityChange(suggestion.city || suggestion.value);
          if (suggestion.region && !region.trim()) {
            onRegionChange(suggestion.region);
          }
        }}
        placeholder="Город"
      />
      <GeoSuggestInput
        kind="region"
        value={region}
        onChange={onRegionChange}
        onSelect={(suggestion) => onRegionChange(suggestion.region || suggestion.value)}
        placeholder="Область"
      />
      <div className="source-location-actions">
        <button className="icon-button active" type="button" title="Сохранить" disabled={saving || !city.trim()} onClick={onSave}>
          <Check size={16} />
        </button>
        <button className="icon-button" type="button" title="Отменить" disabled={saving} onClick={onCancel}>
          <X size={16} />
        </button>
      </div>
    </div>
  );
}

function sourceEditKey(source: Source) {
  return `source:${source.id}`;
}

function groupEditKey(group: SourceGroup) {
  return `group:${group.id}`;
}

function commentsModeLabel(mode: string) {
  if (mode === "poll_replies") {
    return "poll";
  }
  if (mode === "linked_chat_realtime") {
    return "realtime";
  }
  if (mode === "hybrid") {
    return "hybrid";
  }
  return "off";
}

function sourcePairLabel(group: SourceGroup) {
  if (group.channel_source && group.chat_source) {
    return `Канал: ${group.channel_source.title} · Чат: ${group.chat_source.title}`;
  }
  if (group.channel_source) {
    return `Канал: ${group.channel_source.title} · чат не прикреплен`;
  }
  if (group.chat_source) {
    return `Чат: ${group.chat_source.title}`;
  }
  return "Источник не задан";
}

function sourceGroupMeta(group: SourceGroup) {
  const location = `${group.city}${group.region ? ` · ${group.region}` : ""}`;
  if (!group.channel_source) {
    return location;
  }
  const comments = group.monitor_channel_comments ? commentsModeLabel(group.comments_fetch_mode) : "off";
  return `${location} · комментарии: ${comments}`;
}
