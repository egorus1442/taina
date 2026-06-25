from dataclasses import dataclass
import re

from app import models
from app.services.location_resolver import ResolvedLocation


LOCATION_PREPOSITIONS = r"(?:в|во|на|у|около|возле|над)"
EVENT_WORDS = [
    "бомбеж",
    "дрон",
    "дроны",
    "бпла",
    "взрыв",
    "взрывы",
    "летит",
    "летят",
    "слыш",
    "гул",
    "жужж",
    "пво",
    "сирен",
    "тревог",
]

@dataclass(frozen=True)
class LocationReviewDecision:
    needs_review: bool
    reason: str | None = None
    original_location_text: str | None = None
    suggested_query: str | None = None


class LocationReviewDecider:
    def before_geocoding(
        self,
        *,
        source: models.Source,
        message_text: str,
        classification: models.Classification,
    ) -> LocationReviewDecision:
        explicit_location = extract_explicit_location(message_text)
        if (
            explicit_location
            and not (classification.location_text or "").strip()
            and not explicit_location_is_source_city(explicit_location, source.city)
        ):
            return LocationReviewDecision(
                needs_review=True,
                reason="classifier_missed_explicit_location",
                original_location_text=explicit_location,
                suggested_query=self._join(source.region, source.city, explicit_location),
            )

        return LocationReviewDecision(needs_review=False)

    def after_geocoding(
        self,
        *,
        source: models.Source,
        classification: models.Classification,
        resolved_location: ResolvedLocation,
        geocoding_payload: dict,
    ) -> LocationReviewDecision:
        location_text = (classification.location_text or "").strip()
        geocoding_reason = geocoding_payload.get("reason")
        review_reasons = {
            "geocoding_scope_failed",
            "no_geocoding_attempts",
            "geocoding_provider_error",
        }
        if geocoding_reason in review_reasons:
            return LocationReviewDecision(
                needs_review=True,
                reason=geocoding_reason,
                original_location_text=location_text or source.city,
                suggested_query=geocoding_payload.get("query")
                or resolved_location.query
                or self._join(source.region, source.city, location_text),
            )
        if location_text and (geocoding_payload.get("lat") is None or geocoding_payload.get("lon") is None):
            return LocationReviewDecision(
                needs_review=True,
                reason="geocoding_failed_for_extracted_location",
                original_location_text=location_text,
                suggested_query=resolved_location.query or self._join(source.region, source.city, location_text),
            )
        if location_text and not location_matches_formatted_address(
            location_text,
            geocoding_payload.get("formatted_address"),
        ):
            return LocationReviewDecision(
                needs_review=True,
                reason="geocoding_mismatch_for_extracted_location",
                original_location_text=location_text,
                suggested_query=resolved_location.query or self._join(source.region, source.city, location_text),
            )

        return LocationReviewDecision(needs_review=False)

    def _join(self, *parts: str | None) -> str:
        unique: list[str] = []
        seen: set[str] = set()
        for part in parts:
            if not part or not part.strip():
                continue
            value = part.strip()
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return ", ".join(unique)


def extract_explicit_location(text: str) -> str | None:
    normalized = " ".join(text.split())
    match = re.search(rf"(?:^|\s){LOCATION_PREPOSITIONS}\s+", normalized, flags=re.IGNORECASE | re.UNICODE)
    if not match:
        return None

    candidate = normalized[match.end() : match.end() + 80].strip(" .,!?:;")
    candidate = re.split(r"[,;:!?]", candidate, maxsplit=1)[0].strip()
    candidate = _trim_event_words(candidate)
    if not candidate or len(candidate) < 2:
        return None
    return candidate[:120]


def _trim_event_words(value: str) -> str:
    lower = value.casefold()
    split_at = len(value)
    for word in EVENT_WORDS:
        index = lower.find(word)
        if index > 0:
            split_at = min(split_at, index)
    return value[:split_at].strip(" .,!?:;")


def location_matches_formatted_address(location_text: str, formatted_address: str | None) -> bool:
    if not formatted_address:
        return False

    haystack = formatted_address.casefold()
    tokens = _significant_tokens(location_text)
    if not tokens:
        return True
    for token in tokens:
        if token in haystack:
            return True
    return False


def explicit_location_is_source_city(location_text: str, source_city: str) -> bool:
    city_key = _normalize_location_token(source_city)
    location_tokens = {
        _normalize_location_token(token)
        for token in re.findall(r"[A-Za-zА-ЯЁа-яё0-9-]{2,}", location_text)
    }
    return city_key in location_tokens


def _normalize_location_token(value: str) -> str:
    return value.strip(" .,!?:;").casefold().replace("ё", "е")


def _significant_tokens(value: str) -> list[str]:
    stopwords = {"район", "р-н", "город", "поселок", "посёлок", "село", "деревня", "ул", "улица"}
    tokens = re.findall(r"[A-Za-zА-ЯЁа-яё0-9]{3,}", value.casefold())
    return [token for token in tokens if token not in stopwords]
