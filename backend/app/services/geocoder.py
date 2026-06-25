import json
from typing import Any

import httpx

from app.core.config import settings
from app.services.geocoding_attempts import GeocodingAttempt
from app.services.geocoding_validator import GeocodingResultValidator
from app.services.location_resolver import ResolvedLocation


class Geocoder:
    def __init__(self) -> None:
        self.validator = GeocodingResultValidator()

    async def geocode(self, location: ResolvedLocation) -> dict[str, Any]:
        attempt = GeocodingAttempt(
            scope="legacy",
            query=location.query,
            location_text=location.query,
            precision=location.precision,
        )
        return await self.geocode_attempts([attempt])

    async def geocode_attempts(self, attempts: list[GeocodingAttempt]) -> dict[str, Any]:
        if not attempts:
            return self._empty_for_attempts(
                [],
                provider=self._provider(),
                reason="no_geocoding_attempts",
            )

        provider = self._provider()
        attempted: list[dict[str, Any]] = []
        last_raw: str | None = None
        for attempt in attempts:
            candidates = await self._search_attempt(attempt, provider=provider)
            if candidates.get("error"):
                return self._empty_for_attempts(
                    attempts,
                    provider=provider,
                    reason="geocoding_provider_error",
                    raw_response=candidates.get("raw_response"),
                    error=candidates.get("error"),
                )

            last_raw = candidates.get("raw_response") or last_raw
            valid_candidate = self._first_valid_candidate(attempt, candidates.get("items", []))
            attempted.append(
                {
                    "scope": attempt.scope,
                    "query": attempt.query,
                    "required_context": attempt.required_context,
                    "candidate_count": len(candidates.get("items", [])),
                    "valid": valid_candidate is not None,
                }
            )
            if valid_candidate:
                return {
                    "query": attempt.query,
                    "provider": provider,
                    "formatted_address": valid_candidate.get("formatted_address"),
                    "lat": valid_candidate.get("lat"),
                    "lon": valid_candidate.get("lon"),
                    "precision": attempt.precision,
                    "scope": attempt.scope,
                    "required_context": attempt.required_context,
                    "raw_response": valid_candidate.get("raw_response") or last_raw,
                }

        return self._empty_for_attempts(
            attempts,
            provider=provider,
            reason="geocoding_scope_failed",
            raw_response=last_raw,
            attempted=attempted,
        )

    async def _search_attempt(self, attempt: GeocodingAttempt, *, provider: str) -> dict[str, Any]:
        if provider == "yandex":
            return await self._yandex_candidates(attempt)
        return await self._nominatim_candidates(attempt)

    async def _nominatim_candidates(self, attempt: GeocodingAttempt) -> dict[str, Any]:
        headers = {"User-Agent": settings.nominatim_user_agent}
        params = {
            "q": attempt.query,
            "format": "json",
            "limit": 5,
            "addressdetails": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{settings.nominatim_base_url}/search",
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
            raw = response.text
            data = response.json()
        except Exception as exc:
            return {"items": [], "raw_response": None, "error": str(exc)}

        return {
            "items": [
                {
                    "formatted_address": item.get("display_name"),
                    "lat": float(item["lat"]) if item.get("lat") else None,
                    "lon": float(item["lon"]) if item.get("lon") else None,
                    "address_details": item.get("address") or {},
                    "raw_response": raw,
                }
                for item in data
            ],
            "raw_response": raw,
        }

    async def _yandex_candidates(self, attempt: GeocodingAttempt) -> dict[str, Any]:
        params = {
            "apikey": settings.yandex_geocoder_api_key,
            "geocode": attempt.query,
            "format": "json",
            "results": 5,
            "lang": "ru_RU",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://geocode-maps.yandex.ru/1.x/",
                    params=params,
                )
                response.raise_for_status()
            raw = response.text
            data = response.json()
            members = data["response"]["GeoObjectCollection"]["featureMember"]
        except Exception as exc:
            return {"items": [], "raw_response": None, "error": str(exc)}

        return {
            "items": [self._yandex_candidate(member, raw) for member in members],
            "raw_response": raw,
        }

    def _yandex_candidate(self, member: dict[str, Any], raw: str) -> dict[str, Any]:
        geo_object = member["GeoObject"]
        lon, lat = geo_object["Point"]["pos"].split()
        metadata = geo_object.get("metaDataProperty", {}).get("GeocoderMetaData", {})
        return {
            "formatted_address": metadata.get("text"),
            "lat": float(lat),
            "lon": float(lon),
            "address_details": self._flatten_yandex_address(metadata),
            "raw_response": raw,
        }

    def _flatten_yandex_address(self, metadata: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        address = metadata.get("Address") or {}
        for component in address.get("Components") or []:
            kind = component.get("kind")
            name = component.get("name")
            if kind and name:
                values[kind] = name
        return values

    def _first_valid_candidate(
        self,
        attempt: GeocodingAttempt,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for candidate in candidates:
            if candidate.get("lat") is None or candidate.get("lon") is None:
                continue
            if self.validator.is_valid(
                attempt=attempt,
                formatted_address=candidate.get("formatted_address"),
                address_details=candidate.get("address_details"),
            ):
                return candidate
        return None

    def _empty_for_attempts(
        self,
        attempts: list[GeocodingAttempt],
        *,
        provider: str,
        reason: str,
        raw_response: str | None = None,
        error: str | None = None,
        attempted: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "reason": reason,
            "error": error,
            "provider_raw_response": raw_response,
            "attempts": attempted
            or [
                {
                    "scope": attempt.scope,
                    "query": attempt.query,
                    "required_context": attempt.required_context,
                }
                for attempt in attempts
            ],
        }
        first_attempt = attempts[0] if attempts else None
        return {
            "query": first_attempt.query if first_attempt else "",
            "provider": provider,
            "formatted_address": None,
            "lat": None,
            "lon": None,
            "precision": first_attempt.precision if first_attempt else "unknown",
            "raw_response": json.dumps(payload, ensure_ascii=False),
            "reason": reason,
        }

    def _provider(self) -> str:
        if settings.geocoder_provider == "yandex" and settings.yandex_geocoder_api_key:
            return "yandex"
        return "nominatim"
