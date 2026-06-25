import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import models
from app.core.database import get_db
from app.repositories import list_keyword_matched_pipeline_messages, list_messages
from app.schemas import (
    ClassificationDebugRead,
    EventClusterRead,
    EventRead,
    GeocodingResultRead,
    LocationReviewRead,
    MessageRead,
    PipelineMessageRead,
    PipelineStageRead,
    PipelineStatusRead,
    SourceRead,
)
from app.serializers import serialize_event


router = APIRouter()


@router.get("", response_model=list[MessageRead])
def get_messages(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[MessageRead]:
    return list_messages(db, limit=limit)


@router.get("/pipeline", response_model=list[PipelineMessageRead])
def get_pipeline_messages(
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[PipelineMessageRead]:
    messages = list_keyword_matched_pipeline_messages(db, limit=limit)
    return [_serialize_pipeline_message(message) for message in messages]


def _serialize_pipeline_message(message: models.Message) -> PipelineMessageRead:
    event = message.event
    classification = message.classification
    geocoding_result = event.geocoding_result if event else None
    location_review = event.location_review if event else None
    cluster = event.cluster if event else None
    status = _message_status(message)

    return PipelineMessageRead(
        message=MessageRead.model_validate(message),
        source=SourceRead.model_validate(message.source),
        status=status,
        stages=_message_stages(
            message=message,
            classification=classification,
            event=event,
            geocoding_result=geocoding_result,
            location_review=location_review,
            cluster=cluster,
        ),
        raw_json=_parse_raw_json(message.raw_json),
        classification=ClassificationDebugRead.model_validate(classification) if classification else None,
        geocoding_result=GeocodingResultRead.model_validate(geocoding_result) if geocoding_result else None,
        event=serialize_event(event) if event else None,
        location_review=LocationReviewRead.model_validate(location_review) if location_review else None,
        cluster=EventClusterRead.model_validate(cluster) if cluster else None,
    )


def _message_status(message: models.Message) -> PipelineStatusRead:
    event = message.event
    classification = message.classification

    if event:
        if event.review_status == "rejected":
            return _status(
                "review_rejected",
                "Отклонено на ревью",
                "Оператор отклонил событие после ручной проверки.",
                "red",
            )
        if event.review_status == "needs_review":
            return _status(
                "needs_review",
                "Нужно ревью",
                "Событие создано, но локация или геокодинг требуют ручной проверки.",
                "yellow",
            )
        if event.cluster_id:
            return _status(
                "clustered",
                "В кластере",
                "Сообщение дошло до конца пайплайна и привязано к кластеру на карте.",
                "green",
            )
        if event.lat is None or event.lon is None:
            return _status(
                "event_without_coordinates",
                "Событие без координат",
                "Событие создано, но координаты не определены или не сохранены.",
                "orange",
            )
        return _status(
            "event_without_cluster",
            "Событие без кластера",
            "Событие создано с координатами, но не было привязано к кластеру.",
            "blue",
        )

    if classification:
        if not _should_create_event(classification):
            return _status(
                "ai_rejected",
                "Отсеяно AI",
                "AI-классификация сохранена, но сообщение не признано полезным для события.",
                "purple",
            )
        return _status(
            "event_not_created",
            "Остановлено после AI",
            "AI пропустил сообщение дальше, но событие не создано. Нужна проверка логов пайплайна.",
            "slate",
        )

    return _status(
        "pre_ai_stopped",
        "Остановлено до AI",
        "Сообщение прошло ключевые слова, но AI-ответ не сохранен. В текущей схеме точная причина не хранится.",
        "gray",
    )


def _message_stages(
    *,
    message: models.Message,
    classification: models.Classification | None,
    event: models.Event | None,
    geocoding_result: models.GeocodingResult | None,
    location_review: models.LocationReview | None,
    cluster: models.EventCluster | None,
) -> list[PipelineStageRead]:
    stages: list[PipelineStageRead] = [
        _stage(
            "message_saved",
            "Сохранение сообщения",
            "passed",
            "Исходное сообщение сохранено в таблицу messages.",
            {
                "message_id": message.id,
                "telegram_message_id": message.telegram_message_id,
                "source_id": message.source_id,
                "message_date": message.message_date,
                "created_at": message.created_at,
                "raw_json": _parse_raw_json(message.raw_json),
            },
        ),
        _stage(
            "keyword_filter",
            "Ключевые слова",
            "passed" if message.keyword_matched else "rejected",
            "Первичный фильтр по регуляркам из настроек. В этой выборке показываются только прошедшие его сообщения.",
            {
                "processed": message.processed,
                "keyword_matched": message.keyword_matched,
                "matched_terms": "Не сохраняются текущей схемой БД",
            },
        ),
    ]

    if not classification:
        stages.append(
            _stage(
                "pre_ai_gate",
                "До AI",
                "stopped",
                "После keyword-фильтра AI-классификация не появилась. Возможные причины: дубль за 30 секунд, ошибка AI или обрыв обработки; точная причина сейчас не сохраняется.",
                {"ai_processed": message.ai_processed},
            )
        )
        return stages

    stages.append(
        _stage(
            "ai_classification",
            "AI-классификация",
            "passed",
            "Ответ AI сохранен в classifications.",
            _classification_details(classification),
        )
    )

    should_create_event = _should_create_event(classification)
    stages.append(
        _stage(
            "event_gate",
            "Решение о событии",
            "passed" if should_create_event else "rejected",
            "Пайплайн решает, создавать ли событие: useful_for_alert=true либо drone-related событие с confidence >= 0.5.",
            {
                "should_create_event": should_create_event,
                "useful_for_alert": classification.useful_for_alert,
                "is_drone_related": classification.is_drone_related,
                "event_type": classification.event_type,
                "confidence": classification.confidence,
            },
        )
    )
    if not should_create_event:
        return stages

    if not event:
        stages.append(
            _stage(
                "event_creation",
                "Создание события",
                "stopped",
                "AI пропустил сообщение дальше, но запись events отсутствует.",
                {},
            )
        )
        return stages

    stages.append(
        _stage(
            "location_resolution",
            "Определение локации",
            "needs_review" if event.review_status == "needs_review" and not geocoding_result else "passed",
            "Локация собрана из города/региона источника и location_text, который вернул AI.",
            {
                "ai_location_text": classification.location_text,
                "ai_location_precision": classification.location_precision,
                "event_city": event.city,
                "event_region": event.region,
                "event_district": event.district,
                "event_address": event.address,
                "event_landmark": event.landmark,
                "event_precision": event.precision,
                "review_status": event.review_status,
                "review_reason": event.review_reason,
            },
        )
    )

    stages.append(
        _stage(
            "geocoding",
            "Геокодинг",
            _geocoding_stage_status(event=event, geocoding_result=geocoding_result),
            "Геокодер пытается превратить текст локации в координаты.",
            _geocoding_details(event=event, geocoding_result=geocoding_result),
        )
    )

    stages.append(
        _stage(
            "location_review",
            "Ревью локации",
            _review_stage_status(event=event, location_review=location_review),
            "Ручная очередь появляется, когда классификатор пропустил явную локацию, геокодинг не дал координаты или адрес выглядит неверным.",
            _review_details(event=event, location_review=location_review),
        )
    )

    stages.append(
        _stage(
            "clustering",
            "Кластеризация",
            "passed" if cluster else "not_applicable" if event.lat is None or event.lon is None else "stopped",
            "События с координатами объединяются с ближайшим активным кластером или создают новый.",
            {
                "cluster_id": event.cluster_id,
                "lat": event.lat,
                "lon": event.lon,
                "cluster_status": cluster.status if cluster else None,
                "cluster_event_count": cluster.event_count if cluster else None,
                "cluster_source_count": cluster.source_count if cluster else None,
            },
        )
    )
    return stages


def _classification_details(classification: models.Classification) -> dict[str, Any]:
    return {
        "is_drone_related": classification.is_drone_related,
        "useful_for_alert": classification.useful_for_alert,
        "confidence": classification.confidence,
        "risk_level": classification.risk_level,
        "event_type": classification.event_type,
        "summary": classification.summary,
        "location_text": classification.location_text,
        "location_precision": classification.location_precision,
        "needs_geocoding": classification.needs_geocoding,
        "comment": classification.comment,
        "raw_response": classification.raw_response,
    }


def _geocoding_stage_status(
    *,
    event: models.Event,
    geocoding_result: models.GeocodingResult | None,
) -> str:
    if not geocoding_result:
        return "not_applicable" if event.review_status == "needs_review" else "missing"
    if geocoding_result.lat is None or geocoding_result.lon is None:
        return "needs_review" if event.review_status == "needs_review" else "failed"
    return "passed"


def _geocoding_details(
    *,
    event: models.Event,
    geocoding_result: models.GeocodingResult | None,
) -> dict[str, Any]:
    if not geocoding_result:
        return {
            "geocoding_result_id": None,
            "lat": event.lat,
            "lon": event.lon,
            "note": "Запись geocoding_results отсутствует.",
        }
    return {
        "geocoding_result_id": geocoding_result.id,
        "query": geocoding_result.query,
        "provider": geocoding_result.provider,
        "formatted_address": geocoding_result.formatted_address,
        "lat": geocoding_result.lat,
        "lon": geocoding_result.lon,
        "precision": geocoding_result.precision,
        "raw_response": geocoding_result.raw_response,
    }


def _review_stage_status(
    *,
    event: models.Event,
    location_review: models.LocationReview | None,
) -> str:
    if event.review_status == "needs_review":
        return "needs_review"
    if event.review_status == "rejected":
        return "rejected"
    if event.review_status == "operator_approved":
        return "passed"
    if location_review:
        return location_review.status
    return "not_applicable"


def _review_details(
    *,
    event: models.Event,
    location_review: models.LocationReview | None,
) -> dict[str, Any]:
    if not location_review:
        return {
            "review_status": event.review_status,
            "review_reason": event.review_reason,
            "note": "Ручное ревью не создавалось.",
        }
    return {
        "review_id": location_review.id,
        "status": location_review.status,
        "reason": location_review.reason,
        "original_location_text": location_review.original_location_text,
        "suggested_query": location_review.suggested_query,
        "operator_location_text": location_review.operator_location_text,
        "operator_formatted_address": location_review.operator_formatted_address,
        "operator_comment": location_review.operator_comment,
        "resolved_at": location_review.resolved_at,
    }


def _should_create_event(classification: models.Classification) -> bool:
    if classification.useful_for_alert:
        return True
    drone_event_types = {"possible_drone_sighting", "possible_drone_sound"}
    return (
        classification.is_drone_related
        and classification.event_type in drone_event_types
        and classification.confidence >= 0.5
    )


def _status(code: str, label: str, description: str, color: str) -> PipelineStatusRead:
    return PipelineStatusRead(code=code, label=label, description=description, color=color)


def _stage(
    key: str,
    label: str,
    status: str,
    description: str,
    details: dict[str, Any],
) -> PipelineStageRead:
    return PipelineStageRead(
        key=key,
        label=label,
        status=status,
        status_label=_stage_status_label(status),
        description=description,
        details=details,
    )


def _stage_status_label(status: str) -> str:
    return {
        "passed": "Пройден",
        "rejected": "Отсеяно",
        "stopped": "Остановлено",
        "needs_review": "Нужно ревью",
        "failed": "Ошибка",
        "missing": "Нет данных",
        "not_applicable": "Не применялось",
        "resolved": "Решено",
        "pending": "Ожидает",
    }.get(status, status)


def _parse_raw_json(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"_parse_error": "raw_json is not valid JSON", "value": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}
