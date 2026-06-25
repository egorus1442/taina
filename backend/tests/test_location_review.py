from types import SimpleNamespace

from app.services.location_review import LocationReviewDecider


def test_source_city_alias_does_not_require_review() -> None:
    source = SimpleNamespace(city="Санкт-Петербург", region="Ленинградская область")
    classification = SimpleNamespace(location_text=None, location_precision="city")

    decision = LocationReviewDecider().before_geocoding(
        source=source,
        message_text="Реально, над Питером взрывы",
        classification=classification,
    )

    assert not decision.needs_review


def test_precise_location_still_requires_review_when_classifier_misses_it() -> None:
    source = SimpleNamespace(city="Санкт-Петербург", region="Ленинградская область")
    classification = SimpleNamespace(location_text=None, location_precision="city")

    decision = LocationReviewDecider().before_geocoding(
        source=source,
        message_text="В Агалатово летят дроны",
        classification=classification,
    )

    assert decision.needs_review
    assert decision.reason == "classifier_missed_explicit_location"
    assert decision.original_location_text == "Агалатово"
