from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import models


DUPLICATE_WINDOW = timedelta(seconds=30)


class EventDeduplicator:
    def find_recent_duplicate_message(
        self,
        db: Session,
        *,
        message: models.Message,
    ) -> models.Message | None:
        normalized = normalize_message_text(message.text)
        if not normalized:
            return None

        reference_time = self._ensure_utc(message.created_at)
        window_start = reference_time - DUPLICATE_WINDOW

        stmt = (
            select(models.Message)
            .where(models.Message.id < message.id)
            .where(models.Message.created_at >= window_start)
            .order_by(models.Message.created_at.desc())
            .limit(50)
        )

        for candidate in db.scalars(stmt):
            if normalize_message_text(candidate.text) == normalized:
                return candidate
        return None

    def find_recent_duplicate(
        self,
        db: Session,
        *,
        source: models.Source,
        text: str,
        reference_time: datetime,
    ) -> models.Event | None:
        normalized = normalize_message_text(text)
        if not normalized:
            return None

        reference_time = self._ensure_utc(reference_time)
        window_start = reference_time - DUPLICATE_WINDOW
        window_end = reference_time + DUPLICATE_WINDOW

        stmt = (
            select(models.Event)
            .join(models.Event.message)
            .options(
                joinedload(models.Event.source),
                joinedload(models.Event.message),
                joinedload(models.Event.classification),
                joinedload(models.Event.cluster),
            )
            .where(models.Event.created_at >= window_start)
            .where(models.Event.created_at <= window_end)
            .order_by(models.Event.created_at.desc())
            .limit(25)
        )

        for event in db.scalars(stmt):
            if normalize_message_text(event.message.text) == normalized:
                return event
        return None

    def _ensure_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def normalize_message_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()
