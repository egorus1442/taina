import asyncio
from datetime import timezone
import hashlib
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app import models
from app.repositories import (
    get_comment_thread_state,
    get_recorded_comment_thread_max_id,
    get_source_by_telegram_id,
    list_enabled_comment_source_groups,
    list_enabled_sources,
    message_exists,
    touch_source_group_comments_checked,
    upsert_comment_thread_state,
)
from app.services.event_broker import EventBroker
from app.services.pipeline import MessagePipeline
from app.services.telegram_ids import normalize_telegram_id


logger = logging.getLogger("uvicorn.error")


class TelegramCollector:
    def __init__(self, *, event_broker: EventBroker) -> None:
        self.event_broker = event_broker
        self.client = None
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._comments_poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            raise RuntimeError("Telegram is enabled, but TELEGRAM_API_ID or TELEGRAM_API_HASH is missing")

        from telethon import TelegramClient, events

        session_parent = Path(settings.telegram_session_name).parent
        if str(session_parent) != ".":
            session_parent.mkdir(parents=True, exist_ok=True)

        self.client = TelegramClient(
            settings.telegram_session_name,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        await self.client.start()
        self._running = True

        self.client.add_event_handler(self._handle_new_message, events.NewMessage())
        if settings.telegram_poll_enabled_sources:
            self._poll_task = asyncio.create_task(self._poll_enabled_sources_loop())
            self._comments_poll_task = asyncio.create_task(self._poll_channel_comments_loop())
        me = await self.client.get_me()
        logger.info(
            "Telegram collector started as %s with realtime NewMessage handler, source polling=%s, comments polling=%s",
            getattr(me, "username", None) or getattr(me, "id", "unknown"),
            settings.telegram_poll_enabled_sources,
            settings.telegram_poll_enabled_sources,
        )

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._comments_poll_task:
            self._comments_poll_task.cancel()
            try:
                await self._comments_poll_task
            except asyncio.CancelledError:
                pass
        if self.client:
            await self.client.disconnect()

    async def _handle_new_message(self, event) -> None:
        message = event.message
        chat = await event.get_chat()
        telegram_chat_id = normalize_telegram_id(chat)
        chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "dialog"
        logger.info(
            "TELEGRAM_IN chat_id=%s title=%s message_id=%s text=%s",
            telegram_chat_id,
            chat_title,
            message.id,
            self._preview(message.message or ""),
        )

        sender_name = None
        try:
            sender = await event.get_sender()
            sender_name = getattr(sender, "username", None) or getattr(sender, "first_name", None)
        except Exception:
            sender_name = None

        await self._process_message(
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=message.id,
            text=message.message or "",
            sender_name=sender_name,
            message_date=message.date.astimezone(timezone.utc) if message.date else None,
            raw={"telegram": True},
        )

    async def _process_message(
        self,
        *,
        telegram_chat_id: str,
        telegram_message_id: int,
        text: str,
        sender_name: str | None,
        message_date,
        raw: dict,
    ) -> None:
        if not text.strip():
            return

        db: Session = SessionLocal()
        try:
            source = get_source_by_telegram_id(db, telegram_chat_id)
            if not source or not source.enabled:
                logger.info(
                    "Telegram message ignored chat_id=%s reason=%s",
                    telegram_chat_id,
                    "source_not_found" if not source else "source_disabled",
                )
                return
            pipeline = MessagePipeline(event_broker=self.event_broker)
            event = await pipeline.process(
                db,
                source=source,
                telegram_message_id=telegram_message_id,
                text=text,
                sender_name=sender_name,
                message_date=message_date,
                raw=raw,
            )
            logger.info(
                "TELEGRAM_DONE chat_id=%s message_id=%s event_id=%s",
                telegram_chat_id,
                telegram_message_id,
                event.id if event else None,
            )
        finally:
            db.close()

    async def _poll_enabled_sources_loop(self) -> None:
        while self._running:
            try:
                await self._poll_enabled_sources_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Telegram enabled-source polling failed", exc_info=True)
            await asyncio.sleep(settings.telegram_poll_interval_seconds)

    async def _poll_enabled_sources_once(self) -> None:
        if not self.client:
            return

        db: Session = SessionLocal()
        try:
            sources = list_enabled_sources(db)
        finally:
            db.close()

        for source in sources:
            await self._poll_source(source)

    async def _poll_source(self, source: models.Source) -> None:
        if not self.client:
            return

        try:
            entity = await self.client.get_entity(int(source.telegram_id))
        except Exception:
            logger.info(
                "Telegram poll skipped source=%s (%s) reason=entity_not_accessible",
                source.title,
                source.telegram_id,
                exc_info=True,
            )
            return

        if source.last_message_id is None:
            latest_id = 0
            try:
                async for message in self.client.iter_messages(entity, limit=1):
                    latest_id = message.id
                    break
            except Exception:
                logger.info(
                    "Telegram poll baseline skipped source=%s (%s) reason=history_not_accessible",
                    source.title,
                    source.telegram_id,
                    exc_info=True,
                )
                return
            self._set_source_last_message_id(source.id, latest_id)
            logger.info(
                "Telegram poll baseline set source=%s (%s) last_message_id=%s",
                source.title,
                source.telegram_id,
                latest_id,
            )
            return

        messages = []
        try:
            async for message in self.client.iter_messages(
                entity,
                limit=settings.telegram_poll_limit_per_source,
                min_id=source.last_message_id or 0,
            ):
                messages.append(message)
        except Exception:
            logger.info(
                "Telegram poll skipped source=%s (%s) reason=history_not_accessible",
                source.title,
                source.telegram_id,
                exc_info=True,
            )
            return

        if not messages:
            return

        for message in reversed(messages):
            text = message.message or ""
            logger.info(
                "TELEGRAM_POLL_IN chat_id=%s title=%s message_id=%s text=%s",
                source.telegram_id,
                source.title,
                message.id,
                self._preview(text),
            )
            await self._process_message(
                telegram_chat_id=source.telegram_id,
                telegram_message_id=message.id,
                text=text,
                sender_name=None,
                message_date=message.date.astimezone(timezone.utc) if message.date else None,
                raw={"telegram": True, "poll": True},
            )

    async def _poll_channel_comments_loop(self) -> None:
        while self._running:
            try:
                await self._poll_channel_comments_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Telegram channel-comments polling failed", exc_info=True)
            await asyncio.sleep(1)

    async def _poll_channel_comments_once(self) -> None:
        if not self.client:
            return

        db: Session = SessionLocal()
        try:
            groups = list_enabled_comment_source_groups(db)
        finally:
            db.close()

        for group in groups:
            if not self._is_comment_group_due(group):
                continue
            await self._poll_source_group_comments(group)

    def _is_comment_group_due(self, group: models.SourceGroup) -> bool:
        if not group.comments_last_checked_at:
            return True
        checked_at = group.comments_last_checked_at
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        return (models.utcnow() - checked_at).total_seconds() >= group.comments_poll_interval_seconds

    async def _poll_source_group_comments(self, group: models.SourceGroup) -> None:
        if not self.client or not group.channel_source:
            return

        channel = group.channel_source
        try:
            channel_entity = await self.client.get_entity(int(channel.telegram_id))
        except Exception:
            logger.info(
                "Telegram comments poll skipped group=%s channel=%s reason=channel_not_accessible",
                group.title,
                channel.telegram_id,
                exc_info=True,
            )
            self._touch_comments_checked(group.id)
            return

        try:
            posts = []
            async for message in self.client.iter_messages(
                channel_entity,
                limit=group.comments_last_posts_limit,
            ):
                posts.append(message)
        except Exception:
            logger.info(
                "Telegram comments poll skipped group=%s channel=%s reason=posts_not_accessible",
                group.title,
                channel.telegram_id,
                exc_info=True,
            )
            self._touch_comments_checked(group.id)
            return

        for post in posts:
            if not getattr(post, "replies", None):
                continue
            await self._poll_post_comments(group=group, channel=channel, channel_entity=channel_entity, post=post)

        self._touch_comments_checked(group.id)

    async def _poll_post_comments(
        self,
        *,
        group: models.SourceGroup,
        channel: models.Source,
        channel_entity,
        post,
    ) -> None:
        from telethon.tl.functions.messages import GetDiscussionMessageRequest, GetRepliesRequest

        try:
            discussion = await self.client(GetDiscussionMessageRequest(peer=channel_entity, msg_id=post.id))
        except Exception:
            logger.info(
                "Telegram comments poll skipped channel=%s post_id=%s reason=discussion_not_accessible",
                channel.telegram_id,
                post.id,
                exc_info=True,
            )
            return

        discussion_peer = self._find_discussion_peer(discussion=discussion, post=post)
        root_message = self._find_discussion_root_message(discussion=discussion, discussion_peer=discussion_peer)
        if not discussion_peer or not root_message:
            return

        watermark = self._get_comment_thread_watermark(
            source_group_id=group.id,
            channel_source_id=channel.id,
            channel_post_id=post.id,
        )
        try:
            replies = await self.client(
                GetRepliesRequest(
                    peer=discussion_peer,
                    msg_id=root_message.id,
                    offset_id=0,
                    offset_date=None,
                    add_offset=0,
                    limit=50,
                    max_id=0,
                    min_id=0,
                    hash=0,
                )
            )
        except Exception:
            try:
                comments = []
                async for comment in self.client.iter_messages(
                    discussion_peer,
                    reply_to=root_message.id,
                    limit=50,
                ):
                    comments.append(comment)
            except Exception:
                logger.info(
                    "Telegram comments poll skipped channel=%s post_id=%s reason=replies_not_accessible",
                    channel.telegram_id,
                    post.id,
                    exc_info=True,
                )
                return
        else:
            comments = list(getattr(replies, "messages", []))

        comments = sorted(comments, key=lambda item: item.id)
        if watermark is None:
            recorded_max = self._get_recorded_comment_thread_max_id(
                source_group_id=group.id,
                channel_source_id=channel.id,
                channel_post_id=post.id,
            )
            if recorded_max <= 0:
                baseline_id = max((comment.id for comment in comments), default=root_message.id)
                self._set_comment_thread_watermark(
                    source_group_id=group.id,
                    channel_source_id=channel.id,
                    channel_post_id=post.id,
                    discussion_chat_id=normalize_telegram_id(discussion_peer),
                    discussion_root_msg_id=root_message.id,
                    last_comment_id=baseline_id,
                )
                logger.info(
                    "Telegram comments baseline set channel=%s post_id=%s root_msg_id=%s baseline_comment_id=%s",
                    channel.telegram_id,
                    post.id,
                    root_message.id,
                    baseline_id,
                )
                return
            watermark = recorded_max

        discussion_chat_id = normalize_telegram_id(discussion_peer)
        max_seen = watermark
        for comment in comments:
            if comment.id == root_message.id:
                continue
            comment_message_id = self._comment_message_id(
                discussion_chat_id=discussion_chat_id,
                comment_id=comment.id,
            )
            should_fill_gap = False
            if comment.id <= watermark:
                should_fill_gap = self._should_fill_comment_gap(
                    group=group,
                    channel_source_id=channel.id,
                    telegram_message_id=comment_message_id,
                    message_date=comment.date.astimezone(timezone.utc) if comment.date else None,
                )
            if comment.id <= watermark and not should_fill_gap:
                max_seen = max(max_seen, comment.id)
                continue
            text = getattr(comment, "message", None) or ""
            if not text.strip():
                max_seen = max(max_seen, comment.id)
                continue
            max_seen = max(max_seen, comment.id)
            await self._process_message(
                telegram_chat_id=channel.telegram_id,
                telegram_message_id=comment_message_id,
                text=text,
                sender_name=str(getattr(comment, "sender_id", "")) or None,
                message_date=comment.date.astimezone(timezone.utc) if comment.date else None,
                raw={
                    "telegram": True,
                    "source_type": "telegram_channel_comment",
                    "poll": True,
                    "source_group_id": group.id,
                    "channel_id": channel.telegram_id,
                    "channel_post_id": post.id,
                    "discussion_chat_id": discussion_chat_id,
                    "discussion_root_msg_id": root_message.id,
                    "comment_id": comment.id,
                },
            )
        self._set_comment_thread_watermark(
            source_group_id=group.id,
            channel_source_id=channel.id,
            channel_post_id=post.id,
            discussion_chat_id=discussion_chat_id,
            discussion_root_msg_id=root_message.id,
            last_comment_id=max_seen,
        )

    def _find_discussion_peer(self, *, discussion, post):
        expected_channel_id = getattr(getattr(post, "replies", None), "channel_id", None)
        chats = list(getattr(discussion, "chats", []) or [])
        if expected_channel_id:
            for chat in chats:
                if getattr(chat, "id", None) == expected_channel_id:
                    return chat
        return chats[0] if chats else None

    def _find_discussion_root_message(self, *, discussion, discussion_peer):
        if not discussion_peer:
            return None
        discussion_peer_id = normalize_telegram_id(discussion_peer)
        for message in getattr(discussion, "messages", []) or []:
            peer_id = getattr(message, "peer_id", None)
            channel_id = getattr(peer_id, "channel_id", None)
            chat_id = getattr(peer_id, "chat_id", None)
            if channel_id and f"-100{channel_id}" == discussion_peer_id:
                return message
            if chat_id and f"-{chat_id}" == discussion_peer_id:
                return message
        messages = list(getattr(discussion, "messages", []) or [])
        return messages[-1] if messages else None

    def _comment_message_id(self, *, discussion_chat_id: str, comment_id: int) -> int:
        digest = hashlib.sha1(f"{discussion_chat_id}:{comment_id}".encode("utf-8")).hexdigest()
        return -int(digest[:15], 16)

    def _should_fill_comment_gap(
        self,
        *,
        group: models.SourceGroup,
        channel_source_id: int,
        telegram_message_id: int,
        message_date,
    ) -> bool:
        if message_date is None:
            return False
        group_created_at = group.created_at
        if group_created_at.tzinfo is None:
            group_created_at = group_created_at.replace(tzinfo=timezone.utc)
        if message_date < group_created_at:
            return False
        db: Session = SessionLocal()
        try:
            return not message_exists(
                db,
                source_id=channel_source_id,
                telegram_message_id=telegram_message_id,
            )
        finally:
            db.close()

    def _get_comment_thread_watermark(
        self,
        *,
        source_group_id: int,
        channel_source_id: int,
        channel_post_id: int,
    ) -> int | None:
        db: Session = SessionLocal()
        try:
            state = get_comment_thread_state(
                db,
                source_group_id=source_group_id,
                channel_source_id=channel_source_id,
                channel_post_id=channel_post_id,
            )
            return state.last_comment_id if state else None
        finally:
            db.close()

    def _get_recorded_comment_thread_max_id(
        self,
        *,
        source_group_id: int,
        channel_source_id: int,
        channel_post_id: int,
    ) -> int:
        db: Session = SessionLocal()
        try:
            return get_recorded_comment_thread_max_id(
                db,
                source_group_id=source_group_id,
                channel_source_id=channel_source_id,
                channel_post_id=channel_post_id,
            )
        finally:
            db.close()

    def _set_comment_thread_watermark(
        self,
        *,
        source_group_id: int,
        channel_source_id: int,
        channel_post_id: int,
        discussion_chat_id: str | None,
        discussion_root_msg_id: int | None,
        last_comment_id: int,
    ) -> None:
        db: Session = SessionLocal()
        try:
            upsert_comment_thread_state(
                db,
                source_group_id=source_group_id,
                channel_source_id=channel_source_id,
                channel_post_id=channel_post_id,
                discussion_chat_id=discussion_chat_id,
                discussion_root_msg_id=discussion_root_msg_id,
                last_comment_id=last_comment_id,
            )
        finally:
            db.close()

    def _touch_comments_checked(self, group_id: int) -> None:
        db: Session = SessionLocal()
        try:
            group = db.get(models.SourceGroup, group_id)
            if group:
                touch_source_group_comments_checked(db, group)
        finally:
            db.close()

    def _set_source_last_message_id(self, source_id: int, last_message_id: int) -> None:
        db: Session = SessionLocal()
        try:
            source = db.get(models.Source, source_id)
            if not source:
                return
            source.last_message_id = last_message_id
            source.updated_at = models.utcnow()
            db.add(source)
            db.commit()
        finally:
            db.close()

    def _preview(self, value: str, limit: int = 180) -> str:
        preview = " ".join(value.split())
        if len(preview) <= limit:
            return preview
        return f"{preview[: limit - 1]}…"
