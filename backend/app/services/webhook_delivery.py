import asyncio

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories import create_delivery_log

_WEBHOOK_SEMAPHORE = asyncio.Semaphore(settings.webhook_max_concurrency)
_RETRY_DELAYS_SECONDS = (1, 3, 10)


class WebhookDelivery:
    async def send(self, db: Session, *, event_id: int, payload: dict) -> None:
        if not settings.webhook_urls:
            return

        async with (
            _WEBHOOK_SEMAPHORE,
            httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client,
        ):
            for url in settings.webhook_urls:
                await self._send_with_retries(
                    client,
                    db=db,
                    event_id=event_id,
                    url=url,
                    payload=payload,
                )

    async def _send_with_retries(
        self,
        client: httpx.AsyncClient,
        *,
        db: Session,
        event_id: int,
        url: str,
        payload: dict,
    ) -> None:
        headers = {"Idempotency-Key": f"event-{event_id}"}
        attempts = len(_RETRY_DELAYS_SECONDS) + 1
        for attempt in range(attempts):
            try:
                response = await client.post(url, json=payload, headers=headers)
                success = 200 <= response.status_code < 300
                create_delivery_log(
                    db,
                    event_id=event_id,
                    destination=url,
                    success=success,
                    status_code=response.status_code,
                )
                if success or not self._should_retry_status(response.status_code):
                    return
            except Exception as exc:
                create_delivery_log(
                    db,
                    event_id=event_id,
                    destination=url,
                    success=False,
                    error=str(exc),
                )

            if attempt < len(_RETRY_DELAYS_SECONDS):
                await asyncio.sleep(_RETRY_DELAYS_SECONDS[attempt])

    def _should_retry_status(self, status_code: int) -> bool:
        return status_code in {408, 425, 429} or status_code >= 500
