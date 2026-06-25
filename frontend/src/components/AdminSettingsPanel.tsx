import { Save, SlidersHorizontal } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import type { AdminSettings } from "../types";

type Props = {
  settings: AdminSettings | null;
  loading: boolean;
  onSave: (settings: AdminSettings) => Promise<void>;
};

export function AdminSettingsPanel({ settings, loading, onSave }: Props) {
  const [keywordText, setKeywordText] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!settings) {
      return;
    }
    setKeywordText(settings.keyword_patterns.join("\n"));
    setPrompt(settings.ai_system_prompt);
  }, [settings]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSave({
        keyword_patterns: keywordText
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
        ai_system_prompt: prompt
      });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Не удалось сохранить настройки");
    } finally {
      setBusy(false);
    }
  }

  if (loading && !settings) {
    return <p className="empty">Загружаю настройки...</p>;
  }

  return (
    <form className="settings-form" onSubmit={handleSubmit}>
      <div className="settings-heading">
        <SlidersHorizontal size={18} />
        <h2>Ключевые слова и prompt</h2>
      </div>

      <label>
        Ключевые слова / regex
        <textarea
          className="settings-keywords"
          value={keywordText}
          onChange={(event) => setKeywordText(event.target.value)}
        />
      </label>

      <label>
        Prompt для ИИ
        <textarea
          className="settings-prompt"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
        />
      </label>

      {error && <p className="inline-error">{error}</p>}

      <button className="primary-button" type="submit" disabled={busy || loading}>
        <Save size={17} />
        Сохранить настройки
      </button>
    </form>
  );
}
