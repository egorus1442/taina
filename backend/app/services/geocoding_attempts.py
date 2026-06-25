from dataclasses import dataclass
from typing import Protocol


CITY_SCOPE = "city"
REGION_SCOPE = "region"
FREE_SCOPE = "free"

ADJACENT_REGION_BY_CITY = {
    "москва": "Московская область",
    "санкт-петербург": "Ленинградская область",
}


@dataclass(frozen=True)
class GeocodingAttempt:
    scope: str
    query: str
    location_text: str
    precision: str
    required_context: str | None = None


class SourceLike(Protocol):
    city: str
    region: str | None


class GeocodingAttemptBuilder:
    def build_for_source(
        self,
        *,
        source: SourceLike,
        location_text: str | None,
        precision: str | None,
    ) -> list[GeocodingAttempt]:
        return self.build(
            city=source.city,
            region=source.region,
            location_text=location_text,
            precision=precision,
        )

    def build(
        self,
        *,
        city: str,
        region: str | None,
        location_text: str | None,
        precision: str | None,
    ) -> list[GeocodingAttempt]:
        normalized_location = (location_text or "").strip(" .,!?:;")
        normalized_precision = (precision or "unknown").lower()

        if not normalized_location:
            return [
                GeocodingAttempt(
                    scope=CITY_SCOPE,
                    query=city,
                    location_text=city,
                    precision="city",
                )
            ]

        if normalized_precision == "city":
            return [
                GeocodingAttempt(
                    scope=FREE_SCOPE,
                    query=normalized_location,
                    location_text=normalized_location,
                    precision="city",
                )
            ]

        if normalized_precision in {"settlement", "district"}:
            return self._ordered_attempts(
                first_scope=REGION_SCOPE,
                second_scope=CITY_SCOPE,
                city=city,
                region=region,
                location_text=normalized_location,
                precision=normalized_precision,
            )

        return self._ordered_attempts(
            first_scope=CITY_SCOPE,
            second_scope=REGION_SCOPE,
            city=city,
            region=region,
            location_text=normalized_location,
            precision=normalized_precision,
        )

    def _ordered_attempts(
        self,
        *,
        first_scope: str,
        second_scope: str,
        city: str,
        region: str | None,
        location_text: str,
        precision: str,
    ) -> list[GeocodingAttempt]:
        attempts: list[GeocodingAttempt] = []
        for scope in (first_scope, second_scope):
            scoped_attempts = self._attempts_for_scope(
                scope=scope,
                city=city,
                region=region,
                location_text=location_text,
                precision=precision,
            )
            for attempt in scoped_attempts:
                if attempt.query not in {item.query for item in attempts}:
                    attempts.append(attempt)
        return attempts

    def _attempts_for_scope(
        self,
        *,
        scope: str,
        city: str,
        region: str | None,
        location_text: str,
        precision: str,
    ) -> list[GeocodingAttempt]:
        if scope == CITY_SCOPE:
            return [
                GeocodingAttempt(
                    scope=CITY_SCOPE,
                    query=self._join(city, location_text),
                    location_text=location_text,
                    precision=precision,
                    required_context=city,
                )
            ]
        if scope == REGION_SCOPE:
            return [
                GeocodingAttempt(
                    scope=REGION_SCOPE,
                    query=self._join(region_value, location_text),
                    location_text=location_text,
                    precision=precision,
                    required_context=region_value,
                )
                for region_value in self._coverage_regions(city=city, region=region)
            ]
        return []

    def _coverage_regions(self, *, city: str, region: str | None) -> list[str]:
        regions: list[str] = []
        for value in (region, ADJACENT_REGION_BY_CITY.get(city.casefold())):
            if not value or not value.strip():
                continue
            normalized = value.strip()
            if normalized.casefold() not in {item.casefold() for item in regions}:
                regions.append(normalized)
        return regions

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
