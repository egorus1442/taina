import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app import models
from app.repositories import (
    create_classification,
    create_event,
    create_geocoding_result,
    create_location_review,
    create_message,
    get_ai_system_prompt_setting,
    get_event,
    get_keyword_patterns_setting,
    mark_message_processed,
)
from app.serializers import serialize_event
from app.services.event_broker import EventBroker
from app.services.event_clusterer import EventClusterer
from app.services.event_deduplicator import EventDeduplicator
from app.services.geocoder import Geocoder
from app.services.geocoding_attempts import GeocodingAttemptBuilder
from app.services.keyword_filter import KeywordFilter
from app.services.location_review import LocationReviewDecider, LocationReviewDecision
from app.services.location_resolver import LocationResolver
from app.services.openrouter_client import OpenRouterClient
from app.services.webhook_delivery import WebhookDelivery


logger = logging.getLogger("uvicorn.error")


class MessagePipeline:
    def __init__(self, *, event_broker: EventBroker) -> None:
        self.keyword_filter = KeywordFilter()
        self.ai = OpenRouterClient()
        self.location_resolver = LocationResolver()
        self.location_review = LocationReviewDecider()
        self.geocoding_attempts = GeocodingAttemptBuilder()
        self.geocoder = Geocoder()
        self.clusterer = EventClusterer()
        self.deduplicator = EventDeduplicator()
        self.webhook_delivery = WebhookDelivery()
        self.event_broker = event_broker

    async def process(
        self,
        db: Session,
        *,
        source: models.Source,
        telegram_message_id: int,
        text: str,
        sender_name: str | None = None,
        message_date: datetime | None = None,
        raw: dict | None = None,
    ) -> models.Event | None:
        source_label = self._source_label(source)
        logger.info(
            "MESSAGE source=%s telegram_message_id=%s text=%s",
            source_label,
            telegram_message_id,
            self._preview(text),
        )
        message = create_message(
            db,
            source=source,
            telegram_message_id=telegram_message_id,
            text=text,
            sender_name=sender_name,
            message_date=message_date,
            raw_json=json.dumps(raw, ensure_ascii=False) if raw else None,
        )
        if message is None:
            logger.info(
                "SKIP source=%s telegram_message_id=%s reason=duplicate_telegram_message",
                source_label,
                telegram_message_id,
            )
            return None

        keyword_match = KeywordFilter(patterns=get_keyword_patterns_setting(db)).analyze(text)
        mark_message_processed(db, source=source, message=message, keyword_matched=keyword_match.matched)

        if not keyword_match.matched:
            logger.info(
                "SKIP message_id=%s source=%s reason=no_keywords",
                message.id,
                source_label,
            )
            return None

        duplicate_message = self.deduplicator.find_recent_duplicate_message(
            db,
            message=message,
        )
        if duplicate_message:
            logger.info(
                "SKIP message_id=%s source=%s reason=recent_duplicate duplicate_message_id=%s",
                message.id,
                source_label,
                duplicate_message.id,
            )
            return None

        classification_payload, raw_response = await self.ai.classify(
            text=text,
            city=source.city,
            region=source.region,
            system_prompt=get_ai_system_prompt_setting(db),
        )
        classification = create_classification(
            db,
            message=message,
            payload=classification_payload,
            raw_response=raw_response,
        )
        logger.info(
            "AI message_id=%s useful=%s event_type=%s risk=%s confidence=%.2f location=%s precision=%s",
            message.id,
            classification.useful_for_alert,
            classification.event_type,
            classification.risk_level,
            classification.confidence,
            classification.location_text,
            classification.location_precision,
        )

        if not self._should_create_event(classification):
            logger.info(
                "SKIP message_id=%s source=%s reason=not_useful_for_alert",
                message.id,
                source_label,
            )
            return None

        review_before_geocoding = self.location_review.before_geocoding(
            source=source,
            message_text=text,
            classification=classification,
        )
        if review_before_geocoding.needs_review:
            event = create_event(
                db,
                source=source,
                message=message,
                classification=classification,
                geocoding_result=None,
                location=self._unresolved_location(source=source, review=review_before_geocoding),
                review_status="needs_review",
                review_reason=review_before_geocoding.reason,
            )
            create_location_review(
                db,
                event=event,
                reason=review_before_geocoding.reason or "location_needs_review",
                original_location_text=review_before_geocoding.original_location_text,
                suggested_query=review_before_geocoding.suggested_query,
            )
            event = get_event(db, event.id) or event
            await self._publish_event(db, event)
            self._log_review_event(
                event=event,
                reason=review_before_geocoding.reason,
                suggested_query=review_before_geocoding.suggested_query,
            )
            return event

        resolved_location = self.location_resolver.resolve(source=source, classification=classification)
        attempts = self.geocoding_attempts.build_for_source(
            source=source,
            location_text=classification.location_text,
            precision=classification.location_precision,
        )
        logger.info(
            "GEOCODING message_id=%s attempts=%s",
            message.id,
            " | ".join(attempt.query for attempt in attempts) or "none",
        )
        geocoding_payload = await self.geocoder.geocode_attempts(attempts)
        logger.info(
            "GEOCODING_RESULT message_id=%s query=%s lat=%s lon=%s address=%s reason=%s",
            message.id,
            geocoding_payload.get("query"),
            geocoding_payload.get("lat"),
            geocoding_payload.get("lon"),
            geocoding_payload.get("formatted_address"),
            geocoding_payload.get("reason"),
        )
        geocoding_result = create_geocoding_result(db, geocoding_payload)
        review_after_geocoding = self.location_review.after_geocoding(
            source=source,
            classification=classification,
            resolved_location=resolved_location,
            geocoding_payload=geocoding_payload,
        )

        if review_after_geocoding.needs_review:
            event = create_event(
                db,
                source=source,
                message=message,
                classification=classification,
                geocoding_result=geocoding_result,
                location=self._unresolved_location(source=source, review=review_after_geocoding),
                review_status="needs_review",
                review_reason=review_after_geocoding.reason,
            )
            create_location_review(
                db,
                event=event,
                reason=review_after_geocoding.reason or "location_needs_review",
                original_location_text=review_after_geocoding.original_location_text,
                suggested_query=review_after_geocoding.suggested_query,
            )
            event = get_event(db, event.id) or event
            await self._publish_event(db, event)
            self._log_review_event(
                event=event,
                reason=review_after_geocoding.reason,
                suggested_query=review_after_geocoding.suggested_query,
            )
            return event

        location = {
            "city": resolved_location.city,
            "region": self._event_region(
                resolved_location=resolved_location,
                geocoding_payload=geocoding_payload,
            ),
            "district": resolved_location.district,
            "address": resolved_location.address,
            "landmark": resolved_location.landmark,
            "lat": geocoding_result.lat,
            "lon": geocoding_result.lon,
            "precision": resolved_location.precision,
            "formatted_address": geocoding_result.formatted_address,
        }
        event = create_event(
            db,
            source=source,
            message=message,
            classification=classification,
            geocoding_result=geocoding_result,
            location=location,
        )
        self.clusterer.assign(db, event)
        event = get_event(db, event.id) or event
        await self._publish_event(db, event)
        self._log_map_event(event)
        return event

    async def _publish_event(self, db: Session, event: models.Event) -> None:
        payload = serialize_event(event).model_dump(mode="json")
        await self.event_broker.publish(payload)
        await self.webhook_delivery.send(db, event_id=event.id, payload=payload)

    def _unresolved_location(self, *, source: models.Source, review: LocationReviewDecision) -> dict:
        return {
            "city": source.city,
            "region": source.region,
            "district": None,
            "address": review.original_location_text,
            "landmark": None,
            "lat": None,
            "lon": None,
            "precision": "unresolved",
            "formatted_address": None,
        }

    def _event_region(self, *, resolved_location: object, geocoding_payload: dict) -> str | None:
        if geocoding_payload.get("scope") == "region":
            return geocoding_payload.get("required_context") or resolved_location.region
        return resolved_location.region

    def _should_create_event(self, classification: models.Classification) -> bool:
        if classification.useful_for_alert:
            return True

        drone_event_types = {"possible_drone_sighting", "possible_drone_sound"}
        return (
            classification.is_drone_related
            and classification.event_type in drone_event_types
            and classification.confidence >= 0.5
        )

    def _log_review_event(
        self,
        *,
        event: models.Event,
        reason: str | None,
        suggested_query: str | None,
    ) -> None:
        logger.info(
            "REVIEW event_id=%s source=%s reason=%s suggested_query=%s original_location=%s",
            event.id,
            self._source_label(event.source),
            reason,
            suggested_query,
            event.address,
        )

    def _log_map_event(self, event: models.Event) -> None:
        logger.info(
            "MAP_POINT event_id=%s source=%s lat=%.6f lon=%.6f precision=%s address=%s cluster_id=%s",
            event.id,
            self._source_label(event.source),
            event.lat,
            event.lon,
            event.precision,
            event.formatted_address,
            event.cluster_id,
        )

    def _source_label(self, source: models.Source) -> str:
        return f"{source.title} ({source.telegram_id})"

    def _preview(self, value: str, limit: int = 180) -> str:
        preview = " ".join(value.split())
        if len(preview) <= limit:
            return preview
        return f"{preview[: limit - 1]}…"
