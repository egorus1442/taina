from typing import Any

import httpx
from fastapi import APIRouter, Query

from app.core.config import settings
from app.schemas import GeoSuggestionRead


router = APIRouter()


@router.get("/suggestions", response_model=list[GeoSuggestionRead])
async def get_geo_suggestions(
    query: str = Query(min_length=2, max_length=80),
    kind: str = Query(pattern="^(city|region)$"),
    region: str | None = None,
) -> list[GeoSuggestionRead]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{settings.photon_base_url}/api/",
                params={
                    "q": _query_for_kind(query=query, kind=kind, region=region),
                    "limit": 12,
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": settings.nominatim_user_agent,
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        return []

    suggestions: list[GeoSuggestionRead] = []
    seen: set[tuple[str, str | None]] = set()
    for item in data.get("features") or []:
        suggestion = _map_suggestion(item, kind=kind)
        if not suggestion:
            continue
        key = (suggestion.value.casefold(), (suggestion.region or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(suggestion)
    return suggestions


def _query_for_kind(*, query: str, kind: str, region: str | None) -> str:
    if kind == "city" and region:
        return f"{query}, {region}, Россия"
    return f"{query}, Россия"


def _map_suggestion(item: dict[str, Any], *, kind: str) -> GeoSuggestionRead | None:
    data = item.get("properties") or {}
    if not _is_russia(data):
        return None

    osm_value = str(data.get("osm_value") or "").casefold()
    if kind == "region":
        if osm_value not in {"state", "region", "county"}:
            return None
        value = data.get("state") or data.get("name")
        city = None
    else:
        if osm_value not in {"city", "town", "village", "hamlet", "suburb", "locality"}:
            return None
        city = data.get("name")
        value = city

    region = data.get("state")

    if not value:
        return None

    return GeoSuggestionRead(
        value=value,
        city=city,
        region=region,
    )


def _is_russia(data: dict[str, Any]) -> bool:
    country_code = str(data.get("countrycode") or data.get("country_code") or "").casefold()
    country = str(data.get("country") or "").casefold()
    return country_code == "ru" or country in {"russia", "россия", "российская федерация"}
