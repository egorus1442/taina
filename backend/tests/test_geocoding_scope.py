from types import SimpleNamespace

from app.services.geocoding_attempts import GeocodingAttempt, GeocodingAttemptBuilder
from app.services.geocoding_validator import GeocodingResultValidator


def test_street_attempts_try_city_before_region() -> None:
    source = SimpleNamespace(
        city="Санкт-Петербург",
        region="Ленинградская область",
    )

    attempts = GeocodingAttemptBuilder().build_for_source(
        source=source,
        location_text="улица Марата",
        precision="street",
    )

    assert [attempt.query for attempt in attempts] == [
        "Санкт-Петербург, улица Марата",
        "Ленинградская область, улица Марата",
    ]
    assert [attempt.required_context for attempt in attempts] == [
        "Санкт-Петербург",
        "Ленинградская область",
    ]


def test_settlement_attempts_try_region_before_city() -> None:
    source = SimpleNamespace(
        city="Санкт-Петербург",
        region="Ленинградская область",
    )

    attempts = GeocodingAttemptBuilder().build_for_source(
        source=source,
        location_text="Агалатово",
        precision="settlement",
    )

    assert [attempt.query for attempt in attempts] == [
        "Ленинградская область, Агалатово",
        "Санкт-Петербург, Агалатово",
    ]


def test_moscow_settlement_attempts_include_moscow_oblast() -> None:
    source = SimpleNamespace(
        city="Москва",
        region="Москва",
    )

    attempts = GeocodingAttemptBuilder().build_for_source(
        source=source,
        location_text="Новодрожжино",
        precision="settlement",
    )

    assert [attempt.query for attempt in attempts] == [
        "Москва, Новодрожжино",
        "Московская область, Новодрожжино",
    ]


def test_validator_accepts_reordered_street_words_and_abbreviations() -> None:
    attempt = GeocodingAttempt(
        scope="city",
        query="Санкт-Петербург, ул. Лесная",
        location_text="ул. Лесная",
        precision="street",
        required_context="Санкт-Петербург",
    )

    assert GeocodingResultValidator().is_valid(
        attempt=attempt,
        formatted_address="Лесная улица, Санкт-Петербург, Россия",
    )


def test_validator_rejects_matching_street_in_wrong_city() -> None:
    attempt = GeocodingAttempt(
        scope="city",
        query="Санкт-Петербург, улица Лесная",
        location_text="улица Лесная",
        precision="street",
        required_context="Санкт-Петербург",
    )

    assert not GeocodingResultValidator().is_valid(
        attempt=attempt,
        formatted_address=(
            "Лесная улица, Пушкин, "
            "Московская область, Россия"
        ),
    )


def test_validator_ignores_context_words_in_non_administrative_address_parts() -> None:
    attempt = GeocodingAttempt(
        scope="city",
        query="Москва, Кучино",
        location_text="Кучино",
        precision="settlement",
        required_context="Москва",
    )

    assert not GeocodingResultValidator().is_valid(
        attempt=attempt,
        formatted_address=(
            "ВСМ Москва-Казань, Северное Кучино, "
            "Балашиха, Московская область, Россия"
        ),
        address_details={
            "road": "ВСМ Москва-Казань",
            "suburb": "Северное Кучино",
            "city": "Балашиха",
            "state": "Московская область",
        },
    )


def test_validator_requires_settlement_part_for_compound_location() -> None:
    attempt = GeocodingAttempt(
        scope="region",
        query=(
            "Ленинградская область, улица Лесная, Агалатово"
        ),
        location_text="улица Лесная, Агалатово",
        precision="street",
        required_context="Ленинградская область",
    )

    assert not GeocodingResultValidator().is_valid(
        attempt=attempt,
        formatted_address=(
            "Лесная улица, Ленинградская область, Россия"
        ),
    )
    assert GeocodingResultValidator().is_valid(
        attempt=attempt,
        formatted_address=(
            "Лесная улица, Агалатово, "
            "Ленинградская область, Россия"
        ),
    )
