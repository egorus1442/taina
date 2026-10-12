from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.models import Source
from app.repositories import (
    create_source,
    get_source,
    get_source_by_telegram_id,
    update_source,
    upsert_source_group_for_sources,
)
from app.schemas import (
    SourceCreate,
    SourceUpdate,
    TelegramBulkConnectItemRead,
    TelegramBulkConnectRead,
    TelegramBulkConnectRequest,
    TelegramConnectRequest,
    TelegramDialogRead,
    TelegramLinkedChatRead,
)
from app.services.telegram_ids import normalize_telegram_id


def _require_telegram_settings() -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")


def _ensure_session_dir() -> None:
    session_parent = Path(settings.telegram_session_name).parent
    if str(session_parent) != ".":
        session_parent.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def temporary_telegram_client() -> AsyncIterator:
    from telethon import TelegramClient

    _require_telegram_settings()
    _ensure_session_dir()
    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized. Run telegram_check login first.")
        yield client
    finally:
        await client.disconnect()


def classify_dialog_type(entity) -> str:
    from telethon.tl.types import Channel, Chat, User

    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Chat):
        return "chat"
    if isinstance(entity, Channel):
        if getattr(entity, "megagroup", False):
            return "chat"
        return "channel"
    return "dialog"


def normalize_channel_reference(value: str) -> str | None:
    normalized = value.strip().rstrip("/")
    if not normalized:
        return None
    normalized = normalized.removeprefix("https://").removeprefix("http://")
    normalized = normalized.removeprefix("www.").removeprefix("t.me/")
    normalized = normalized.removeprefix("telegram.me/").lstrip("@")
    normalized = normalized.split("?", 1)[0].strip("/")
    if not normalized or "/" in normalized or normalized.startswith("+"):
        return None
    if not all(character.isalnum() or character == "_" for character in normalized):
        return None
    return normalized


async def bulk_connect_channels(
    *,
    client,
    db: Session,
    payload: TelegramBulkConnectRequest,
) -> TelegramBulkConnectRead:
    requested: dict[str, str] = {}
    invalid: list[str] = []
    for raw_value in payload.channels:
        username = normalize_channel_reference(raw_value)
        if username is None:
            if raw_value.strip():
                invalid.append(raw_value.strip())
            continue
        requested.setdefault(username.casefold(), username)

    dialogs_by_username = {}
    async for dialog in client.iter_dialogs(limit=None):
        entity = dialog.entity
        username = getattr(entity, "username", None)
        if username and username.casefold() in requested:
            dialogs_by_username[username.casefold()] = dialog

    items: list[TelegramBulkConnectItemRead] = [
        TelegramBulkConnectItemRead(
            channel=value,
            status="failed",
            detail="Некорректная ссылка или приватное приглашение без @username",
        )
        for value in invalid
    ]
    connected = 0
    updated = 0

    for key, username in requested.items():
        dialog = dialogs_by_username.get(key)
        if dialog is None:
            items.append(
                TelegramBulkConnectItemRead(
                    channel=f"@{username}",
                    status="failed",
                    detail="Канал не найден среди диалогов авторизованного аккаунта",
                )
            )
            continue

        entity = dialog.entity
        if classify_dialog_type(entity) != "channel":
            items.append(
                TelegramBulkConnectItemRead(
                    channel=f"@{username}",
                    status="failed",
                    detail="Ссылка ведёт не на канал",
                )
            )
            continue

        telegram_id = normalize_telegram_id(entity)
        existing = get_source_by_telegram_id(db, telegram_id)
        title = dialog.name or getattr(entity, "title", None) or username
        source = connect_dialog(
            db,
            TelegramConnectRequest(
                telegram_id=telegram_id,
                title=title,
                username=getattr(entity, "username", None),
                type="channel",
                city=payload.city,
                region=payload.region,
                enabled=True,
                monitor_channel_posts=True,
                monitor_chat_messages=False,
                monitor_channel_comments=True,
                comments_fetch_mode="poll_replies",
                comments_poll_interval_seconds=15,
                comments_last_posts_limit=5,
            ),
        )
        latest_id = getattr(getattr(dialog, "message", None), "id", None)
        # Set a baseline only for a new source. Reconnecting an existing one must
        # not advance its cursor past messages the collector has not read yet.
        if not existing and latest_id is not None:
            source.last_message_id = max(source.last_message_id or 0, int(latest_id))
            source.updated_at = models.utcnow()
            db.add(source)
            db.commit()
            db.refresh(source)

        upsert_source_group_for_sources(
            db,
            title=title,
            channel_source=source,
            chat_source=None,
            city=payload.city,
            region=payload.region,
            enabled=True,
            monitor_channel_posts=True,
            monitor_chat_messages=False,
            monitor_channel_comments=True,
            comments_fetch_mode="poll_replies",
            comments_poll_interval_seconds=15,
            comments_last_posts_limit=5,
        )
        status = "updated" if existing else "connected"
        if existing:
            updated += 1
        else:
            connected += 1
        items.append(
            TelegramBulkConnectItemRead(
                channel=f"@{username}",
                status=status,
                source_id=source.id,
                telegram_id=telegram_id,
                title=title,
            )
        )

    return TelegramBulkConnectRead(
        total=len(items),
        connected=connected,
        updated=updated,
        failed=sum(item.status == "failed" for item in items),
        items=items,
    )


async def list_dialogs_from_client(
    *,
    client,
    db: Session,
    limit: int,
    query: str | None = None,
    include_users: bool = False,
) -> list[TelegramDialogRead]:
    query_normalized = query.lower().strip() if query else None
    result: list[TelegramDialogRead] = []

    async for dialog in client.iter_dialogs(limit=limit):
        entity = dialog.entity
        dialog_type = classify_dialog_type(entity)
        if dialog_type == "user" and not include_users:
            continue

        title = dialog.name or getattr(entity, "title", None) or getattr(entity, "username", None) or "Untitled"
        username = getattr(entity, "username", None)
        telegram_id = normalize_telegram_id(entity)

        if query_normalized:
            haystack = f"{title} {username or ''} {telegram_id}".lower()
            if query_normalized not in haystack:
                continue

        source = get_source_by_telegram_id(db, telegram_id)
        if source and source.type != dialog_type:
            source = update_source(db, source, SourceUpdate(type=dialog_type))
        result.append(_serialize_dialog(telegram_id, title, dialog_type, username, source))

    return result


def connect_dialog(db: Session, payload: TelegramConnectRequest) -> Source:
    existing = get_source_by_telegram_id(db, payload.telegram_id)
    if existing:
        return update_source(
            db,
            existing,
            SourceUpdate(
                title=payload.title,
                username=payload.username,
                type=payload.type,
                city=payload.city,
                region=payload.region,
                enabled=payload.enabled,
            ),
        )

    return create_source(
        db,
        SourceCreate(
            telegram_id=payload.telegram_id,
            title=payload.title,
            username=payload.username,
            type=payload.type,
            city=payload.city,
            region=payload.region,
            enabled=payload.enabled,
        ),
    )


async def connect_dialog_with_linked_chat(
    *,
    client,
    db: Session,
    payload: TelegramConnectRequest,
) -> list[Source]:
    source = connect_dialog(db, payload)
    await initialize_source_baseline(client=client, db=db, source=source)
    connected = [source]

    if payload.type != "channel":
        upsert_source_group_for_sources(
            db,
            title=payload.title,
            channel_source=None,
            chat_source=source if source.type == "chat" else None,
            city=payload.city,
            region=payload.region,
            enabled=payload.enabled,
            monitor_channel_posts=False,
            monitor_chat_messages=payload.type == "chat" and payload.monitor_chat_messages,
            monitor_channel_comments=False,
            comments_fetch_mode="off",
            comments_poll_interval_seconds=payload.comments_poll_interval_seconds,
            comments_last_posts_limit=payload.comments_last_posts_limit,
        )
        return connected

    paired_chat_source = None
    if payload.comments_fetch_mode == "linked_chat_realtime" and not (
        payload.paired_chat_source_id or payload.paired_chat_telegram_id
    ):
        raise RuntimeError("Paired chat is required for linked_chat_realtime mode")

    if payload.paired_chat_source_id:
        paired_chat_source = get_source(db, payload.paired_chat_source_id)
        if not paired_chat_source or paired_chat_source.type != "chat":
            raise RuntimeError("Paired chat source must reference a connected Telegram chat")
        connected.append(paired_chat_source)
    elif payload.paired_chat_telegram_id:
        if not payload.paired_chat_title:
            raise RuntimeError("Paired chat title is required")
        paired_chat_source = connect_dialog(
            db,
            TelegramConnectRequest(
                telegram_id=payload.paired_chat_telegram_id,
                title=payload.paired_chat_title,
                username=payload.paired_chat_username,
                type="chat",
                city=payload.city,
                region=payload.region,
                enabled=payload.enabled,
                monitor_channel_posts=False,
                monitor_chat_messages=True,
                monitor_channel_comments=False,
                comments_fetch_mode="off",
                comments_poll_interval_seconds=payload.comments_poll_interval_seconds,
                comments_last_posts_limit=payload.comments_last_posts_limit,
            ),
        )
        await initialize_source_baseline(client=client, db=db, source=paired_chat_source)
        connected.append(paired_chat_source)

    upsert_source_group_for_sources(
        db,
        title=payload.title,
        channel_source=source,
        chat_source=paired_chat_source,
        city=payload.city,
        region=payload.region,
        enabled=payload.enabled,
        monitor_channel_posts=payload.monitor_channel_posts,
        monitor_chat_messages=bool(paired_chat_source) and payload.monitor_chat_messages,
        monitor_channel_comments=payload.monitor_channel_comments,
        comments_fetch_mode=payload.comments_fetch_mode,
        comments_poll_interval_seconds=payload.comments_poll_interval_seconds,
        comments_last_posts_limit=payload.comments_last_posts_limit,
    )
    return connected


async def initialize_source_baseline(*, client, db: Session, source: Source) -> None:
    try:
        entity = await client.get_entity(int(source.telegram_id))
        message = await client.get_messages(entity, limit=1)
    except Exception:
        return

    latest = None
    if isinstance(message, list):
        latest = message[0] if message else None
    else:
        latest = message[0] if len(message) else None
    latest_id = getattr(latest, "id", None)
    if latest_id is None:
        return
    source.last_message_id = max(source.last_message_id or 0, int(latest_id))
    source.updated_at = models.utcnow()
    db.add(source)
    db.commit()
    db.refresh(source)


async def get_linked_chat_from_client(
    *,
    client,
    channel_ref,
    db: Session,
) -> TelegramLinkedChatRead | None:
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.types import Channel

    try:
        channel = await client.get_entity(channel_ref)
        if not isinstance(channel, Channel) or getattr(channel, "megagroup", False):
            return None
        full = await client(GetFullChannelRequest(channel))
    except Exception:
        return None

    linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
    if not linked_chat_id:
        return None

    linked_entity = None
    for chat in getattr(full, "chats", []):
        if getattr(chat, "id", None) == linked_chat_id:
            linked_entity = chat
            break
    if linked_entity is None:
        return None

    telegram_id = normalize_telegram_id(linked_entity)
    source = get_source_by_telegram_id(db, telegram_id)
    return _serialize_linked_chat(
        telegram_id=telegram_id,
        title=getattr(linked_entity, "title", None) or getattr(linked_entity, "username", None) or "Discussion chat",
        username=getattr(linked_entity, "username", None),
        source=source,
    )


def _serialize_dialog(
    telegram_id: str,
    title: str,
    dialog_type: str,
    username: str | None,
    source: Source | None,
    linked_chat: TelegramLinkedChatRead | None = None,
) -> TelegramDialogRead:
    return TelegramDialogRead(
        telegram_id=telegram_id,
        title=title,
        type=dialog_type,
        username=username,
        is_connected=source is not None,
        source_id=source.id if source else None,
        enabled=source.enabled if source else False,
        city=source.city if source else None,
        region=source.region if source else None,
        processed_count=source.processed_count if source else 0,
        event_count=source.event_count if source else 0,
        linked_chat=linked_chat,
    )


def _serialize_linked_chat(
    *,
    telegram_id: str,
    title: str,
    username: str | None,
    source: Source | None,
) -> TelegramLinkedChatRead:
    return TelegramLinkedChatRead(
        telegram_id=telegram_id,
        title=title,
        type=source.type if source else "chat",
        username=username,
        is_connected=source is not None,
        source_id=source.id if source else None,
        enabled=source.enabled if source else False,
        city=source.city if source else None,
        region=source.region if source else None,
        processed_count=source.processed_count if source else 0,
        event_count=source.event_count if source else 0,
    )
