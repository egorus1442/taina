import argparse
import asyncio
from pathlib import Path

from app.core.config import settings
from app.services.telegram_ids import normalize_telegram_id


def require_telegram_settings() -> None:
    missing = []
    if not settings.telegram_api_id:
        missing.append("TELEGRAM_API_ID")
    if not settings.telegram_api_hash:
        missing.append("TELEGRAM_API_HASH")
    if missing:
        names = ", ".join(missing)
        raise SystemExit(f"Missing Telegram settings: {names}. Put them into .env first.")


def ensure_session_dir() -> None:
    session_parent = Path(settings.telegram_session_name).parent
    if str(session_parent) != ".":
        session_parent.mkdir(parents=True, exist_ok=True)


async def make_client():
    from telethon import TelegramClient

    require_telegram_settings()
    ensure_session_dir()
    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.start()
    return client


async def login_command(_: argparse.Namespace) -> None:
    client = await make_client()
    try:
        me = await client.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else me.id
        print(f"Authorized as {username}", flush=True)
        print(f"Session: {settings.telegram_session_name}.session", flush=True)
    finally:
        await client.disconnect()


async def dialogs_command(args: argparse.Namespace) -> None:
    client = await make_client()
    try:
        print("Accessible Telegram dialogs:", flush=True)
        async for dialog in client.iter_dialogs(limit=args.limit):
            entity = dialog.entity
            username = getattr(entity, "username", None)
            username_text = f" @{username}" if username else ""
            print(
                f"{normalize_telegram_id(entity)} | {dialog.name} | "
                f"{type(entity).__name__}{username_text}",
                flush=True,
            )
    finally:
        await client.disconnect()


async def linked_chat_command(args: argparse.Namespace) -> None:
    from telethon import utils
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.types import Channel

    client = await make_client()
    try:
        channel = await client.get_entity(args.channel)
        if not isinstance(channel, Channel) or getattr(channel, "megagroup", False):
            raise SystemExit("Target must be a Telegram channel, not a chat/user.")

        full = await client(GetFullChannelRequest(channel))
        linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
        print(
            f"Channel: {normalize_telegram_id(channel)} | "
            f"{getattr(channel, 'title', None) or getattr(channel, 'username', None)}",
            flush=True,
        )
        if not linked_chat_id:
            print("Linked discussion chat: not exposed for this account/channel.", flush=True)
            return

        linked_entity = None
        for chat in getattr(full, "chats", []):
            if getattr(chat, "id", None) == linked_chat_id:
                linked_entity = chat
                break

        if linked_entity is None:
            print(f"Linked discussion chat internal id: {linked_chat_id}", flush=True)
            print("Chat entity is not accessible to this account.", flush=True)
            return

        username = getattr(linked_entity, "username", None)
        username_text = f" @{username}" if username else ""
        print(
            f"Linked discussion chat: {utils.get_peer_id(linked_entity)} | "
            f"{getattr(linked_entity, 'title', None) or 'Untitled'} | "
            f"{type(linked_entity).__name__}{username_text}",
            flush=True,
        )
    finally:
        await client.disconnect()


async def listen_command(args: argparse.Namespace) -> None:
    from telethon import events

    client = await make_client()
    stop_event = asyncio.Event()

    @client.on(events.NewMessage())
    async def handler(event):
        chat = await event.get_chat()
        text = event.raw_text or ""
        preview = " ".join(text.split())[:160]
        print(
            f"NEW | {normalize_telegram_id(chat)} | "
            f"{getattr(chat, 'title', None) or getattr(chat, 'username', None) or 'dialog'} | "
            f"message_id={event.message.id} | {preview}",
            flush=True,
        )
        if args.once:
            stop_event.set()

    try:
        print("Listening for new Telegram messages only.", flush=True)
        print("Send a new message to an accessible channel/chat, or press Ctrl+C to stop.", flush=True)
        if args.seconds:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=args.seconds)
            except asyncio.TimeoutError:
                pass
        else:
            await stop_event.wait()
    finally:
        await client.disconnect()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram authorization and realtime check tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="Authorize account and create Telethon session.")
    login.set_defaults(func=login_command)

    dialogs = subparsers.add_parser("dialogs", help="List accessible channels/chats with Telegram IDs.")
    dialogs.add_argument("--limit", type=int, default=50)
    dialogs.set_defaults(func=dialogs_command)

    linked_chat = subparsers.add_parser("linked-chat", help="Show linked discussion chat for a channel.")
    linked_chat.add_argument("channel", help="Channel username, invite, or Telegram id, for example @tipkhimki.")
    linked_chat.set_defaults(func=linked_chat_command)

    listen = subparsers.add_parser("listen", help="Listen for new messages without reading history.")
    listen.add_argument("--seconds", type=int, default=120)
    listen.add_argument("--once", action="store_true")
    listen.set_defaults(func=listen_command)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
