import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.database import SessionLocal
from app.repositories import (
    get_comment_thread_state,
    get_enabled_comment_source_group_for_channel,
    get_next_comment_source_group_for_safety_scan,
    get_source_by_telegram_id,
    list_due_comment_thread_states,
    list_enabled_sources,
    touch_source_group_comments_checked,
    update_comment_thread_poll_state,
    upsert_comment_thread_state,
)
from app.services.comment_polling import decide_comment_poll, next_poll_at
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
        self._comments_semaphore = asyncio.Semaphore(settings.telegram_comments_max_concurrency)
        self.started_at: datetime | None = None
        self.account: str | None = None
        self.last_update_at: datetime | None = None
        self.last_message_at: datetime | None = None
        self.source_poll_last_at: datetime | None = None
        self.comments_poll_last_at: datetime | None = None
        self.flood_wait_until: datetime | None = None
        self.flood_wait_count = 0
        self.last_error: str | None = None

    async def start(self) -> None:
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            raise RuntimeError(
                "Telegram is enabled, but TELEGRAM_API_ID or TELEGRAM_API_HASH is missing"
            )

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
        self.started_at = models.utcnow()

        self.client.add_event_handler(self._handle_new_message, events.NewMessage())
        if settings.telegram_poll_enabled_sources:
            self._poll_task = asyncio.create_task(self._poll_enabled_sources_loop())
            self._comments_poll_task = asyncio.create_task(self._poll_channel_comments_loop())
        me = await self.client.get_me()
        self.account = getattr(me, "username", None) or str(getattr(me, "id", "unknown"))
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
        self.last_update_at = models.utcnow()
        message = event.message
        chat = await event.get_chat()
        telegram_chat_id = normalize_telegram_id(chat)

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
            message_date=message.date.astimezone(UTC) if message.date else None,
            raw={"telegram": True},
        )
        await self._discover_realtime_post_thread(
            telegram_chat_id=telegram_chat_id,
            channel_entity=chat,
            post=message,
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

        self.last_message_at = models.utcnow()

        db: Session = SessionLocal()
        try:
            source = get_source_by_telegram_id(db, telegram_chat_id)
            if not source or not source.enabled:
                logger.debug(
                    "Telegram message ignored chat_id=%s reason=%s",
                    telegram_chat_id,
                    "source_not_found" if not source else "source_disabled",
                )
                return
            logger.info(
                "TELEGRAM_IN chat_id=%s title=%s message_id=%s text=%s",
                telegram_chat_id,
                source.title,
                telegram_message_id,
                self._preview(text),
            )
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
                self.source_poll_last_at = models.utcnow()
                await self._wait_for_flood_window()
                await self._poll_enabled_sources_once()
                self.source_poll_last_at = models.utcnow()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(exc)
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

        await self._wait_for_flood_window()

        try:
            entity = await self.client.get_entity(int(source.telegram_id))
        except Exception as exc:
            self._record_telegram_error(exc)
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

        cursor_id = source.last_message_id or 0
        for _page in range(settings.telegram_poll_max_pages_per_source):
            messages = []
            try:
                async for message in self.client.iter_messages(
                    entity,
                    limit=settings.telegram_poll_limit_per_source,
                    min_id=cursor_id,
                    reverse=True,
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

            for message in messages:
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
                    message_date=message.date.astimezone(UTC) if message.date else None,
                    raw={"telegram": True, "poll": True},
                )
                cursor_id = max(cursor_id, message.id)

            # Persist the page cursor even when a Telegram message has no text.
            # Otherwise a media-only page would be fetched forever because the
            # message pipeline intentionally ignores empty text messages.
            self._set_source_last_message_id(source.id, cursor_id)

            if len(messages) < settings.telegram_poll_limit_per_source:
                return

        logger.warning(
            "Telegram source backlog capped source=%s (%s) last_processed_id=%s; continuing next cycle",
            source.title,
            source.telegram_id,
            cursor_id,
        )

    async def _poll_channel_comments_loop(self) -> None:
        while self._running:
            try:
                self.comments_poll_last_at = models.utcnow()
                await self._wait_for_flood_window()
                await self._poll_channel_comments_once()
                self.comments_poll_last_at = models.utcnow()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(exc)
                logger.warning("Telegram channel-comments polling failed", exc_info=True)
            await asyncio.sleep(1)

    async def _poll_channel_comments_once(self) -> None:
        if not self.client:
            return

        await self._poll_due_comment_threads()

        db: Session = SessionLocal()
        try:
            checked_before = models.utcnow() - timedelta(
                seconds=settings.telegram_comments_safety_scan_seconds
            )
            group = get_next_comment_source_group_for_safety_scan(
                db,
                checked_before=checked_before,
            )
        finally:
            db.close()

        if group:
            await self._discover_source_group_threads(group)

    async def _discover_source_group_threads(self, group: models.SourceGroup) -> None:
        if not self.client or not group.channel_source:
            return

        await self._wait_for_flood_window()

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
            await self._discover_post_thread(
                group=group,
                channel=channel,
                channel_entity=channel_entity,
                post=post,
            )

        self._touch_comments_checked(group.id)

    async def _discover_realtime_post_thread(
        self,
        *,
        telegram_chat_id: str,
        channel_entity,
        post,
    ) -> None:
        db: Session = SessionLocal()
        try:
            group = get_enabled_comment_source_group_for_channel(
                db,
                telegram_id=telegram_chat_id,
            )
        finally:
            db.close()
        if not group or not group.channel_source:
            return
        await self._discover_post_thread(
            group=group,
            channel=group.channel_source,
            channel_entity=channel_entity,
            post=post,
        )

    async def _discover_post_thread(
        self,
        *,
        group: models.SourceGroup,
        channel: models.Source,
        channel_entity,
        post,
    ) -> None:
        from telethon.tl.functions.messages import GetDiscussionMessageRequest

        if self._get_comment_thread_state(
            source_group_id=group.id,
            channel_source_id=channel.id,
            channel_post_id=post.id,
        ):
            return

        try:
            discussion = await self.client(
                GetDiscussionMessageRequest(peer=channel_entity, msg_id=post.id)
            )
        except Exception as exc:
            flood_wait = self._flood_wait_seconds(exc)
            if flood_wait is not None:
                self._set_flood_wait(flood_wait)
                logger.warning(
                    "Telegram FloodWait while discovering comments channel=%s post_id=%s wait=%ss",
                    channel.telegram_id,
                    post.id,
                    flood_wait,
                )
                return
            logger.info(
                "Telegram comment discovery skipped channel=%s post_id=%s reason=discussion_not_accessible",
                channel.telegram_id,
                post.id,
                exc_info=True,
            )
            return

        discussion_peer = self._find_discussion_peer(discussion=discussion, post=post)
        root_message = self._find_discussion_root_message(
            discussion=discussion, discussion_peer=discussion_peer
        )
        if not discussion_peer or not root_message:
            return

        post_date = post.date.astimezone(UTC) if post.date else models.utcnow()
        self._set_comment_thread_watermark(
            source_group_id=group.id,
            channel_source_id=channel.id,
            channel_post_id=post.id,
            discussion_chat_id=normalize_telegram_id(discussion_peer),
            discussion_root_msg_id=root_message.id,
            last_comment_id=0,
            post_date=post_date,
            next_poll_at=models.utcnow(),
        )
        logger.info(
            "Telegram comment thread registered channel=%s post_id=%s discussion=%s root_msg_id=%s",
            channel.telegram_id,
            post.id,
            normalize_telegram_id(discussion_peer),
            root_message.id,
        )

    async def _poll_due_comment_threads(self) -> None:
        db: Session = SessionLocal()
        try:
            states = list_due_comment_thread_states(
                db,
                reference_time=models.utcnow(),
                limit=settings.telegram_comments_due_batch_size,
            )
        finally:
            db.close()
        if not states:
            return
        results = await asyncio.gather(
            *(self._poll_tracked_comment_thread(state) for state in states),
            return_exceptions=True,
        )
        for state, result in zip(states, results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "Telegram tracked comment poll failed state_id=%s channel_post_id=%s",
                    state.id,
                    state.channel_post_id,
                    exc_info=(type(result), result, result.__traceback__),
                )

    async def _poll_tracked_comment_thread(self, state: models.CommentThreadState) -> None:
        async with self._comments_semaphore:
            await self._wait_for_flood_window()
            group = state.source_group
            channel = state.channel_source
            if (
                not self.client
                or not group.enabled
                or not group.monitor_channel_comments
                or group.comments_fetch_mode not in {"poll_replies", "hybrid"}
                or not state.discussion_chat_id
                or not state.discussion_root_msg_id
            ):
                self._update_comment_thread_state(
                    state=state,
                    next_poll_at_value=None,
                    status="disabled",
                )
                return

            now = models.utcnow()
            post_date = state.post_date or state.created_at
            decision = decide_comment_poll(
                post_date=post_date,
                last_activity_at=state.last_activity_at,
                now=now,
                minimum_interval_seconds=max(
                    settings.telegram_comments_min_interval_seconds,
                    group.comments_poll_interval_seconds,
                ),
            )
            if not decision.active:
                self._update_comment_thread_state(
                    state=state,
                    next_poll_at_value=None,
                    status="dormant",
                )
                return

            try:
                discussion_peer = await self.client.get_input_entity(int(state.discussion_chat_id))
            except Exception:
                try:
                    discussion_peer = await self.client.get_entity(int(state.discussion_chat_id))
                except Exception as exc:
                    self._handle_comment_poll_error(
                        state=state, exc=exc, reason="discussion_not_accessible"
                    )
                    return

            if state.last_comment_id <= 0 and (now - self._ensure_utc(post_date)) > timedelta(
                minutes=15
            ):
                await self._baseline_old_comment_thread(
                    state=state,
                    discussion_peer=discussion_peer,
                    now=now,
                    decision=decision,
                )
                return

            comments = []
            try:
                async for comment in self.client.iter_messages(
                    discussion_peer,
                    reply_to=state.discussion_root_msg_id,
                    min_id=max(state.last_comment_id, 0),
                    reverse=True,
                    limit=settings.telegram_comments_max_per_poll,
                ):
                    comments.append(comment)
            except Exception as exc:
                self._handle_comment_poll_error(
                    state=state, exc=exc, reason="replies_not_accessible"
                )
                return

            max_seen = state.last_comment_id
            processed_count = 0
            for comment in comments:
                max_seen = max(max_seen, comment.id)
                if (
                    comment.id == state.discussion_root_msg_id
                    or comment.id <= state.last_comment_id
                ):
                    continue
                text = getattr(comment, "message", None) or ""
                if not text.strip():
                    continue
                comment_message_id = self._comment_message_id(
                    discussion_chat_id=state.discussion_chat_id,
                    comment_id=comment.id,
                )
                await self._process_message(
                    telegram_chat_id=channel.telegram_id,
                    telegram_message_id=comment_message_id,
                    text=text,
                    sender_name=str(getattr(comment, "sender_id", "")) or None,
                    message_date=comment.date.astimezone(UTC) if comment.date else None,
                    raw={
                        "telegram": True,
                        "source_type": "telegram_channel_comment",
                        "poll": True,
                        "source_group_id": group.id,
                        "channel_id": channel.telegram_id,
                        "channel_post_id": state.channel_post_id,
                        "discussion_chat_id": state.discussion_chat_id,
                        "discussion_root_msg_id": state.discussion_root_msg_id,
                        "comment_id": comment.id,
                    },
                )
                processed_count += 1

            found_new_comments = processed_count > 0
            next_decision = decide_comment_poll(
                post_date=post_date,
                last_activity_at=now if found_new_comments else state.last_activity_at,
                now=now,
                minimum_interval_seconds=max(
                    settings.telegram_comments_min_interval_seconds,
                    group.comments_poll_interval_seconds,
                ),
                found_new_comments=found_new_comments,
            )
            self._update_comment_thread_state(
                state=state,
                last_comment_id=max_seen,
                last_activity_at=now if found_new_comments else state.last_activity_at,
                next_poll_at_value=next_poll_at(now=now, decision=next_decision),
                consecutive_idle_polls=0
                if found_new_comments
                else state.consecutive_idle_polls + 1,
                status="active" if next_decision.active else "dormant",
            )

    async def _baseline_old_comment_thread(
        self,
        *,
        state: models.CommentThreadState,
        discussion_peer,
        now,
        decision,
    ) -> None:
        latest_id = state.discussion_root_msg_id or 0
        try:
            async for comment in self.client.iter_messages(
                discussion_peer,
                reply_to=state.discussion_root_msg_id,
                limit=1,
            ):
                latest_id = max(latest_id, comment.id)
        except Exception as exc:
            self._handle_comment_poll_error(state=state, exc=exc, reason="baseline_not_accessible")
            return
        self._update_comment_thread_state(
            state=state,
            last_comment_id=latest_id,
            next_poll_at_value=next_poll_at(now=now, decision=decision),
            status="active",
        )
        logger.info(
            "Telegram comments baseline set channel=%s post_id=%s baseline_comment_id=%s",
            state.channel_source.telegram_id,
            state.channel_post_id,
            latest_id,
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
        digest = hashlib.sha1(f"{discussion_chat_id}:{comment_id}".encode()).hexdigest()
        return -int(digest[:15], 16)

    def _get_comment_thread_state(
        self,
        *,
        source_group_id: int,
        channel_source_id: int,
        channel_post_id: int,
    ) -> models.CommentThreadState | None:
        db: Session = SessionLocal()
        try:
            return get_comment_thread_state(
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
        post_date=None,
        next_poll_at=None,
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
                post_date=post_date,
                next_poll_at=next_poll_at,
            )
        finally:
            db.close()

    def _update_comment_thread_state(
        self,
        *,
        state: models.CommentThreadState,
        last_comment_id: int | None = None,
        last_activity_at=None,
        next_poll_at_value=None,
        consecutive_idle_polls: int | None = None,
        status: str | None = None,
    ) -> None:
        db: Session = SessionLocal()
        try:
            update_comment_thread_poll_state(
                db,
                state_id=state.id,
                last_comment_id=(
                    state.last_comment_id if last_comment_id is None else last_comment_id
                ),
                last_activity_at=(
                    state.last_activity_at if last_activity_at is None else last_activity_at
                ),
                next_poll_at=next_poll_at_value,
                consecutive_idle_polls=(
                    state.consecutive_idle_polls
                    if consecutive_idle_polls is None
                    else consecutive_idle_polls
                ),
                status=status or state.status,
            )
        finally:
            db.close()

    def _handle_comment_poll_error(
        self,
        *,
        state: models.CommentThreadState,
        exc: Exception,
        reason: str,
    ) -> None:
        flood_wait = self._flood_wait_seconds(exc)
        if flood_wait is not None:
            self._set_flood_wait(flood_wait)
        retry_after = max(flood_wait or 60, 1)
        logger.warning(
            "Telegram comment poll deferred state_id=%s channel_post_id=%s reason=%s retry_after=%ss",
            state.id,
            state.channel_post_id,
            "flood_wait" if flood_wait is not None else reason,
            retry_after,
            exc_info=flood_wait is None,
        )
        self._update_comment_thread_state(
            state=state,
            next_poll_at_value=models.utcnow() + timedelta(seconds=retry_after),
            consecutive_idle_polls=state.consecutive_idle_polls + 1,
            status="active",
        )

    def _flood_wait_seconds(self, exc: Exception) -> int | None:
        if exc.__class__.__name__ not in {"FloodWaitError", "FloodPremiumWaitError"}:
            return None
        try:
            return max(int(getattr(exc, "seconds", 0)), 1)
        except (TypeError, ValueError):
            return 60

    def _ensure_utc(self, value):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _set_flood_wait(self, seconds: int) -> None:
        until = models.utcnow() + timedelta(seconds=max(seconds, 1))
        if self.flood_wait_until is None or until > self.flood_wait_until:
            self.flood_wait_until = until
        self.flood_wait_count += 1

    async def _wait_for_flood_window(self) -> None:
        if self.flood_wait_until is None:
            return
        remaining = (self._ensure_utc(self.flood_wait_until) - models.utcnow()).total_seconds()
        if remaining > 0:
            await asyncio.sleep(remaining)
        self.flood_wait_until = None

    def _record_error(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"

    def _record_telegram_error(self, exc: Exception) -> None:
        flood_wait = self._flood_wait_seconds(exc)
        if flood_wait is not None:
            self._set_flood_wait(flood_wait)
        else:
            self._record_error(exc)

    def status_snapshot(self) -> dict:
        now = models.utcnow()
        rate_limited = bool(
            self.flood_wait_until
            and self._ensure_utc(self.flood_wait_until) > now
        )
        connected = bool(self.client and self.client.is_connected())
        source_poll_alive = bool(
            not settings.telegram_poll_enabled_sources
            or (self._poll_task and not self._poll_task.done())
        )
        comments_poll_alive = bool(
            not settings.telegram_poll_enabled_sources
            or (self._comments_poll_task and not self._comments_poll_task.done())
        )
        return {
            "enabled": settings.telegram_enabled,
            "running": self._running and source_poll_alive and comments_poll_alive,
            "connected": connected,
            "account": self.account,
            "started_at": self.started_at,
            "last_update_at": self.last_update_at,
            "last_message_at": self.last_message_at,
            "source_poll_last_at": self.source_poll_last_at,
            "comments_poll_last_at": self.comments_poll_last_at,
            "flood_wait_until": self.flood_wait_until if rate_limited else None,
            "flood_wait_count": self.flood_wait_count,
            "rate_limited": rate_limited,
            "last_error": self.last_error,
        }

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
