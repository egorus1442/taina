import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { GeoSuggestion } from "../types";

type Props = {
  kind: "city" | "region";
  value: string;
  placeholder: string;
  region?: string | null;
  fallbackOptions?: string[];
  onChange: (value: string) => void;
  onSelect: (suggestion: GeoSuggestion) => void;
};

export function GeoSuggestInput({
  kind,
  value,
  placeholder,
  region,
  fallbackOptions = [],
  onChange,
  onSelect
}: Props) {
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
