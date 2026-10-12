import asyncio

from app.services import geocoder as geocoder_module
from app.services.geocoder import Geocoder
from app.services.geocoding_attempts import GeocodingAttempt


def test_geocoder_reuses_cached_query(monkeypatch) -> None:
    calls = 0

    async def fake_yandex(_attempt):
        nonlocal calls
        calls += 1
        return {"items": [{"lat": 55.75, "lon": 37.61}], "raw_response": "{}"}

    geocoder_module._GEOCODER_CACHE.clear()
    geocoder = Geocoder()
    monkeypatch.setattr(geocoder, "_yandex_candidates", fake_yandex)
    attempt = GeocodingAttempt(
        scope="city",
        query="Москва",
        location_text="Москва",
        precision="city",
    )

    async def run() -> None:
        first = await geocoder._search_attempt(attempt, provider="yandex")
        second = await geocoder._search_attempt(attempt, provider="yandex")
        assert first == second

    asyncio.run(run())
    assert calls == 1
