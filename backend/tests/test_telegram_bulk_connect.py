import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.schemas import TelegramBulkConnectRequest
from app.services import telegram_dialogs
from app.services.telegram_dialogs import normalize_channel_reference


def test_normalize_channel_reference_accepts_supported_formats() -> None:
    assert normalize_channel_reference("@izhevsk_smi") == "izhevsk_smi"
    assert normalize_channel_reference("t.me/izhevsk_smi") == "izhevsk_smi"
    assert (
        normalize_channel_reference("https://www.t.me/izhevsk_smi/?single")
        == "izhevsk_smi"
    )


def test_normalize_channel_reference_rejects_invites_and_invalid_paths() -> None:
    assert normalize_channel_reference("https://t.me/+privateInvite") is None
    assert normalize_channel_reference("https://t.me/channel/123") is None
    assert normalize_channel_reference("channel-name") is None


def test_reconnecting_does_not_skip_unread_messages(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    dialog = SimpleNamespace(
        entity=SimpleNamespace(username="izhevsk_smi"),
        name="Ижевск СМИ",
        message=SimpleNamespace(id=100),
    )

    class Client:
        async def iter_dialogs(self, *, limit):
            yield dialog

    monkeypatch.setattr(telegram_dialogs, "classify_dialog_type", lambda _entity: "channel")
    monkeypatch.setattr(telegram_dialogs, "normalize_telegram_id", lambda _entity: "-1001")
    payload = TelegramBulkConnectRequest(
        channels=["@izhevsk_smi"],
        city="Ижевск",
        region="Удмуртская Республика",
    )

    try:
        first = asyncio.run(
            telegram_dialogs.bulk_connect_channels(client=Client(), db=db, payload=payload)
        )
        source = telegram_dialogs.get_source_by_telegram_id(db, "-1001")
        assert first.connected == 1
        assert source.last_message_id == 100

        source.last_message_id = 95
        db.commit()
        dialog.message.id = 110
        second = asyncio.run(
            telegram_dialogs.bulk_connect_channels(client=Client(), db=db, payload=payload)
        )
        db.refresh(source)
        assert second.updated == 1
        assert source.last_message_id == 95
    finally:
        db.close()
