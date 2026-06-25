import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories import create_delivery_log


class WebhookDelivery:
    async def send(self, db: Session, *, event_id: int, payload: dict) -> None:
        if not settings.webhook_urls:
            return

        async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
            for url in settings.webhook_urls:
                try:
                    response = await client.post(url, json=payload)
                    create_delivery_log(
                        db,
                        event_id=event_id,
                        destination=url,
                        success=200 <= response.status_code < 300,
                        status_code=response.status_code,
                    )
                except Exception as exc:
                    create_delivery_log(
                        db,
                        event_id=event_id,
                        destination=url,
                        success=False,
                        error=str(exc),
                    )

