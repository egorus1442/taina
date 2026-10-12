from datetime import UTC, datetime, timedelta

from app.services.comment_polling import decide_comment_poll, next_poll_at

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def test_new_thread_is_polled_every_15_seconds() -> None:
    decision = decide_comment_poll(
        post_date=NOW - timedelta(minutes=1),
        last_activity_at=None,
        now=NOW,
    )

    assert decision.active
    assert decision.interval_seconds == 15
    assert next_poll_at(now=NOW, decision=decision) == NOW + timedelta(seconds=15)


def test_polling_slows_down_as_thread_becomes_idle() -> None:
    expected = [
        (timedelta(minutes=30), 60),
        (timedelta(hours=2), 300),
        (timedelta(hours=12), 900),
        (timedelta(hours=30), 3600),
    ]

    for age, interval in expected:
        decision = decide_comment_poll(
            post_date=NOW - age,
            last_activity_at=None,
            now=NOW,
        )
        assert decision.active
        assert decision.interval_seconds == interval


def test_new_activity_returns_thread_to_hot_interval() -> None:
    decision = decide_comment_poll(
        post_date=NOW - timedelta(hours=12),
        last_activity_at=NOW - timedelta(hours=2),
        now=NOW,
        found_new_comments=True,
    )

    assert decision.active
    assert decision.interval_seconds == 15


def test_thread_becomes_dormant_after_48_hours() -> None:
    decision = decide_comment_poll(
        post_date=NOW - timedelta(hours=48),
        last_activity_at=NOW - timedelta(minutes=1),
        now=NOW,
        found_new_comments=True,
    )

    assert not decision.active
    assert decision.interval_seconds is None
    assert next_poll_at(now=NOW, decision=decision) is None


def test_group_minimum_interval_is_respected() -> None:
    decision = decide_comment_poll(
        post_date=NOW - timedelta(minutes=1),
        last_activity_at=None,
        now=NOW,
        minimum_interval_seconds=30,
    )

    assert decision.interval_seconds == 30
