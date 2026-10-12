import shutil
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.database import get_db
from app.schemas import SystemStatusRead, TelegramRuntimeStatusRead

router = APIRouter()


@router.get("/status", response_model=SystemStatusRead)
def get_system_status(
    request: Request,
    db: Session = Depends(get_db),
) -> SystemStatusRead:
    now = models.utcnow()
    collector = getattr(request.app.state, "collector", None)
    if collector:
        telegram = TelegramRuntimeStatusRead(**collector.status_snapshot())
    else:
        telegram = TelegramRuntimeStatusRead(
            enabled=settings.telegram_enabled,
            running=False,
            connected=False,
        )

    one_hour_ago = now - timedelta(hours=1)
    one_day_ago = now - timedelta(days=1)
    enabled_sources = _count(
        db,
        select(func.count(models.Source.id)).where(models.Source.enabled.is_(True)),
    )
    enabled_comment_sources = _count(
        db,
        select(func.count(models.SourceGroup.id))
        .where(models.SourceGroup.enabled.is_(True))
        .where(models.SourceGroup.monitor_channel_comments.is_(True))
        .where(models.SourceGroup.comments_fetch_mode.in_(("poll_replies", "hybrid"))),
    )
    active_comment_threads = _count(
        db,
        select(func.count(models.CommentThreadState.id)).where(
            models.CommentThreadState.status == "active"
        ),
    )
    due_comment_threads = _count(
        db,
        select(func.count(models.CommentThreadState.id))
        .where(models.CommentThreadState.status == "active")
        .where(
            or_(
                models.CommentThreadState.next_poll_at.is_(None),
                models.CommentThreadState.next_poll_at <= now,
            )
        ),
    )
    messages_last_hour = _count(
        db,
        select(func.count(models.Message.id)).where(models.Message.created_at >= one_hour_ago),
    )
    events_last_hour = _count(
        db,
        select(func.count(models.Event.id)).where(models.Event.created_at >= one_hour_ago),
    )
    failed_webhooks_last_24h = _count(
        db,
        select(func.count(models.DeliveryLog.id))
        .where(models.DeliveryLog.success.is_(False))
        .where(models.DeliveryLog.created_at >= one_day_ago),
    )
    last_stored_message_at = db.scalar(select(func.max(models.Message.created_at)))

    status = "ok"
    if not telegram.enabled:
        status = "disabled"
    elif not telegram.running or not telegram.connected:
        status = "error"
    elif telegram.rate_limited:
        status = "warning"
    elif telegram.comments_poll_last_at:
        last_comment_poll = telegram.comments_poll_last_at
        if last_comment_poll.tzinfo is None:
            last_comment_poll = last_comment_poll.replace(tzinfo=now.tzinfo)
        if now - last_comment_poll > timedelta(seconds=30):
            status = "warning"
    elif telegram.started_at:
        started_at = telegram.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=now.tzinfo)
        if now - started_at > timedelta(seconds=30):
            status = "warning"

    disk_root = settings.data_dir if settings.data_dir.exists() else settings.data_dir.parent
    disk_free_gb = round(shutil.disk_usage(disk_root).free / (1024 ** 3), 1)
    if status == "ok" and (failed_webhooks_last_24h > 0 or disk_free_gb < 1):
        status = "warning"

    return SystemStatusRead(
        status=status,
        checked_at=now,
        telegram=telegram,
        enabled_sources=enabled_sources,
        enabled_comment_sources=enabled_comment_sources,
        active_comment_threads=active_comment_threads,
        due_comment_threads=due_comment_threads,
        messages_last_hour=messages_last_hour,
        events_last_hour=events_last_hour,
        failed_webhooks_last_24h=failed_webhooks_last_24h,
        disk_free_gb=disk_free_gb,
        last_stored_message_at=last_stored_message_at,
    )


def _count(db: Session, statement) -> int:
    return int(db.scalar(statement) or 0)
