import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.config import settings
from app.services.telegram_collector import TelegramCollector


class FakeTelegramClient:
    def __init__(self) -> None:
        self.messages = [
            SimpleNamespace(id=value, message=f"message-{value}", date=datetime.now(UTC))
            for value in range(1, 6)
        ]

    async def get_entity(self, _telegram_id: int):
        return object()

    async def iter_messages(self, _entity, *, limit: int, min_id: int, reverse: bool):
        assert reverse
        candidates = [item for item in self.messages if item.id > min_id]
        for item in candidates[:limit]:
            yield item


def test_source_polling_reads_oldest_pages_without_gaps(monkeypatch) -> None:
    collector = TelegramCollector(event_broker=object())
    collector.client = FakeTelegramClient()
    processed_ids: list[int] = []
    saved_cursors: list[int] = []

    async def fake_process_message(**values) -> None:
        processed_ids.append(values["telegram_message_id"])

    collector._process_message = fake_process_message  # type: ignore[method-assign]
    collector._set_source_last_message_id = (  # type: ignore[method-assign]
        lambda _source_id, last_message_id: saved_cursors.append(last_message_id)
    )
    monkeypatch.setattr(settings, "telegram_poll_limit_per_source", 2)
    monkeypatch.setattr(settings, "telegram_poll_max_pages_per_source", 3)
    source = SimpleNamespace(
        id=1,
        telegram_id="-1001",
        title="test",
        last_message_id=0,
    )

    asyncio.run(collector._poll_source(source))

    assert processed_ids == [1, 2, 3, 4, 5]
    assert saved_cursors == [2, 4, 5]


def test_runtime_status_detects_stopped_poll_task(monkeypatch) -> None:
    collector = TelegramCollector(event_broker=object())
    collector.client = SimpleNamespace(is_connected=lambda: True)
    collector._running = True
    collector._poll_task = SimpleNamespace(done=lambda: True)
    collector._comments_poll_task = SimpleNamespace(done=lambda: False)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_poll_enabled_sources", True)

    status = collector.status_snapshot()

    assert status["connected"] is True
    assert status["running"] is False
