from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

HOT_WINDOW = timedelta(minutes=5)
WARM_WINDOW = timedelta(hours=1)
COOL_WINDOW = timedelta(hours=6)
OLD_WINDOW = timedelta(hours=24)
STOP_AFTER = timedelta(hours=48)


@dataclass(frozen=True)
class CommentPollDecision:
    active: bool
    interval_seconds: int | None


def decide_comment_poll(
    *,
    post_date: datetime,
    last_activity_at: datetime | None,
    now: datetime,
    minimum_interval_seconds: int = 15,
    found_new_comments: bool = False,
) -> CommentPollDecision:
    """Return the next adaptive polling interval for one comment thread."""
    post_date = _ensure_utc(post_date)
    now = _ensure_utc(now)
    if now - post_date >= STOP_AFTER:
        return CommentPollDecision(active=False, interval_seconds=None)

    if found_new_comments:
        return CommentPollDecision(
            active=True,
            interval_seconds=max(15, minimum_interval_seconds),
        )

    activity_reference = _ensure_utc(last_activity_at) if last_activity_at else post_date
    inactivity = max(now - activity_reference, timedelta(0))
    if inactivity < HOT_WINDOW:
        interval = 15
    elif inactivity < WARM_WINDOW:
        interval = 60
    elif inactivity < COOL_WINDOW:
        interval = 300
    elif inactivity < OLD_WINDOW:
        interval = 900
    else:
        interval = 3600

    return CommentPollDecision(
        active=True,
        interval_seconds=max(interval, minimum_interval_seconds),
    )


def next_poll_at(*, now: datetime, decision: CommentPollDecision) -> datetime | None:
    if not decision.active or decision.interval_seconds is None:
        return None
    return _ensure_utc(now) + timedelta(seconds=decision.interval_seconds)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
