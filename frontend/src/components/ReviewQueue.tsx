import { CheckCircle2, Clock, MapPin, ShieldQuestion, XCircle } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";
import type { LocationReviewItem, LocationReviewResolveRequest } from "../types";

type Props = {
  reviews: LocationReviewItem[];
  loading: boolean;
  onResolve: (reviewId: number, payload: LocationReviewResolveRequest) => Promise<void>;
  onReject: (reviewId: number, operatorComment: string) => Promise<void>;
  layout?: "panel" | "page";
};

export function ReviewQueue({ reviews, loading, onResolve, onReject, layout = "panel" }: Props) {
  if (loading) {
    return <p className="empty">Загружаю очередь ревью...</p>;
  }

  if (!reviews.length) {
    return <p className="empty">Очередь ревью пуста. Сомнительные события появятся здесь.</p>;
  }

  return (
    <div className={layout === "page" ? "review-list review-list-page" : "review-list"}>
      {reviews.map((review) => (
        <ReviewCard key={review.id} review={review} onResolve={onResolve} onReject={onReject} />
      ))}
    </div>
  );
}

type ReviewCardProps = {
  review: LocationReviewItem;
  onResolve: (reviewId: number, payload: LocationReviewResolveRequest) => Promise<void>;
  onReject: (reviewId: number, operatorComment: string) => Promise<void>;
};

function initialLocation(review: LocationReviewItem): string {
  return review.original_location_text || review.event.location.address || review.event.classification.location_text || "";
}

function reviewPrecision(review: LocationReviewItem): string {
  const locationPrecision = review.event.location.precision;
  if (locationPrecision && locationPrecision !== "unresolved" && locationPrecision !== "unknown") {
    return locationPrecision;
  }
  const classificationPrecision = review.event.classification.location_precision;
  if (classificationPrecision && classificationPrecision !== "unknown") {
    return classificationPrecision;
  }
  return "operator";
}

function ReviewCard({ review, onResolve, onReject }: ReviewCardProps) {
  const [locationText, setLocationText] = useState(initialLocation(review));
  const [operatorComment, setOperatorComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reasonLabel = useMemo(() => formatReason(review.reason), [review.reason]);

  async function handleResolve(event: FormEvent) {
    event.preventDefault();
    if (!locationText.trim()) {
      setError("Укажи локацию для подтверждения");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onResolve(review.id, {
        location_text: locationText.trim(),
        city: review.event.source.city,
        region: review.event.source.region,
        precision: reviewPrecision(review),
        operator_comment: operatorComment.trim() || null
      });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Не удалось подтвердить событие");
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    setBusy(true);
    setError(null);
    try {
      await onReject(review.id, operatorComment.trim());
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Не удалось отклонить событие");
      setBusy(false);
    }
  }

  return (
    <article className="review-card">
      <div className="review-card-header">
        <div className="risk-dot medium">
          <ShieldQuestion size={15} />
        </div>
        <div>
          <strong>{reasonLabel}</strong>
          <small>
            <Clock size={12} />
            {formatDateTime(review.created_at)}
          </small>
        </div>
      </div>

      <p className="review-message">{review.event.message.text}</p>

      <div className="review-meta">
        <span>
          <MapPin size={13} />
          {review.original_location_text || review.event.location.formatted_address || review.event.location.city}
        </span>
        <span>{review.event.source.title}</span>
        {review.suggested_query && <span>Запрос: {review.suggested_query}</span>}
      </div>

      <form className="review-form" onSubmit={handleResolve}>
        <label>
          Куда поставить событие
          <input
            value={locationText}
            onChange={(event) => setLocationText(event.target.value)}
            placeholder="Например: Химки, Рождествено"
          />
        </label>
        <label>
          Комментарий оператора
          <textarea value={operatorComment} onChange={(event) => setOperatorComment(event.target.value)} />
        </label>
        {error && <p className="inline-error">{error}</p>}
        <div className="review-actions">
          <button className="primary-inline-button" type="submit" disabled={busy}>
            <CheckCircle2 size={16} />
            Подтвердить
          </button>
          <button className="secondary-button danger" type="button" onClick={handleReject} disabled={busy}>
            <XCircle size={16} />
            Отклонить
          </button>
        </div>
      </form>
    </article>
  );
}

function formatReason(reason: string): string {
  const labels: Record<string, string> = {
    classifier_missed_explicit_location: "ИИ пропустил локацию",
    geocoding_failed_for_extracted_location: "Геокодинг не нашел локацию",
    geocoding_mismatch_for_extracted_location: "Геокодинг не совпал с текстом",
    geocoding_scope_failed: "Локация вне ожидаемой области",
    no_geocoding_attempts: "Нет вариантов геокодинга",
    geocoding_provider_error: "Ошибка геокодера",
    location_needs_review: "Нужна проверка локации"
  };
  return labels[reason] || reason;
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString([], {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}
