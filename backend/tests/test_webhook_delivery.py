import asyncio
from types import SimpleNamespace

from app.services import webhook_delivery
from app.services.webhook_delivery import WebhookDelivery


class FakeClient:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = iter(statuses)
        self.headers: list[dict[str, str]] = []

    async def post(self, _url: str, *, json: dict, headers: dict[str, str]):
        assert json == {"id": 7}
        self.headers.append(headers)
        return SimpleNamespace(status_code=next(self.statuses))


def test_webhook_retries_transient_errors_and_sends_idempotency_key(monkeypatch) -> None:
    logs: list[dict] = []
    sleeps: list[int] = []

    def fake_log(_db, **values):
        logs.append(values)

    async def fake_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(webhook_delivery, "create_delivery_log", fake_log)
    monkeypatch.setattr(webhook_delivery.asyncio, "sleep", fake_sleep)
    client = FakeClient([500, 429, 204])

    asyncio.run(
        WebhookDelivery()._send_with_retries(
            client,
            db=object(),
            event_id=7,
            url="https://example.test/events",
            payload={"id": 7},
        )
    )

    assert [item["status_code"] for item in logs] == [500, 429, 204]
    assert sleeps == [1, 3]
    assert client.headers == [{"Idempotency-Key": "event-7"}] * 3


def test_webhook_does_not_retry_permanent_client_error(monkeypatch) -> None:
    logs: list[dict] = []

    monkeypatch.setattr(
        webhook_delivery,
        "create_delivery_log",
        lambda _db, **values: logs.append(values),
    )
    client = FakeClient([400])

    asyncio.run(
        WebhookDelivery()._send_with_retries(
            client,
            db=object(),
            event_id=7,
            url="https://example.test/events",
            payload={"id": 7},
        )
    )

    assert len(logs) == 1
    assert logs[0]["status_code"] == 400
