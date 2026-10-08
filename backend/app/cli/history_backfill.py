"""Historical backfill of Telegram channels (posts + comments) into a separate SQLite DB.

Usage (from repo root):
    PYTHONPATH=backend backend/.venv-local/bin/python -m app.cli.history_backfill run --channels privolze.MD
    PYTHONPATH=backend backend/.venv-local/bin/python -m app.cli.history_backfill stats
    PYTHONPATH=backend backend/.venv-local/bin/python -m app.cli.history_backfill export --out data/privolze_relevant.csv
"""

import argparse
import asyncio
import csv
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.services.admin_settings import DEFAULT_KEYWORD_PATTERNS
from app.services.keyword_filter import KeywordFilter


logger = logging.getLogger("history_backfill")

DEFAULT_DB_PATH = "data/history_privolze.db"

NOISY_DEFAULT_PATTERNS = {r"\bнад\s+\w+", r"\bпролет\w*\b"}

HISTORY_KEYWORD_PATTERNS = [p for p in DEFAULT_KEYWORD_PATTERNS if p not in NOISY_DEFAULT_PATTERNS] + [
    r"\bпролет(?:ел|ела|ели|ает|ают|ал|али)\w*\b",
    r"\bатак\w*\b",
    r"\bракет\w*\b",
    r"\bобстрел\w*\b",
    r"\bприл[её]т\w*\b",
    r"\bсбит\w*\b",
    r"\bсбил\w*\b",
    r"\bсбива\w*\b",
    r"\bперехва\w*\b",
    r"\bподавлен\w*\b",
    r"\bуничтож\w*\b",
    r"\bопасност\w*\b",
    r"\bков[её]р\w*\b",
    r"\bаэропорт\w*\b",
    r"\bмобильн\w*\s+интернет\w*\b",
    r"\bнпз\b",
    r"\bдиверс\w*\b",
    r"\bтеракт\w*\b",
    r"\bукраинск\w*\s+(?:дрон|бпла|беспилот)\w*\b",
    r"\bвсу\b",
    r"\bудар\w*\s+(?:по|дрон|бпла|беспилот)\w*\b",
]

CATEGORIES = [
    "drone_attack",
    "drone_sighting",
    "drone_shot_down",
    "explosion",
    "air_defense",
    "missile",
    "alert_regime",
    "airport_restrictions",
    "connectivity_restrictions",
    "aftermath",
    "other_security",
    "unrelated",
]

HISTORY_SYSTEM_PROMPT = f"""
Ты аналитик гражданского OSINT-мониторинга. Тебе дают пачку сообщений (посты каналов и комментарии под ними)
из Telegram-каналов конкретного региона России. Для каждого сообщения определи, относится ли оно к инцидентам
безопасности: атаки/налёты БПЛА (дронов), наблюдение или звук дронов, сбитие/перехват БПЛА, взрывы и хлопки,
работа ПВО, ракеты, режимы беспилотной/ракетной опасности и сирены, ограничения аэропортов (план «Ковёр»),
ограничения мобильного интернета/связи из-за угрозы БПЛА, последствия атак (пожары, разрушения, пострадавшие),
диверсии и теракты.

Правила:
- Не давай тактических, военных или прицельных рекомендаций. Только классификация и извлечение фактов.
- is_relevant=true, если сообщение содержит информацию о таком инциденте или угрозе (официальное сообщение,
  новость, очевидец, комментарий «у нас тоже слышно» и т.п.). Новости о событиях в других регионах тоже
  релевантны, но укажи это в in_source_region=false.
- is_relevant=false для рекламы, шуток без фактов, общих политических рассуждений, новостей не о безопасности
  (например «атака» в спорте, «ракета» как название, «взрыв эмоций», плановые учения без инцидента — учения
  отмечай other_security только если упомянута тревога/сирены).
- Для комментария учитывай parent_post как контекст, но оценивай сам комментарий.
- location_text — самое конкретное место инцидента (населённый пункт, район, объект), в именительном падеже, или null.
- event_date — дата инцидента, если она явно указана в тексте и отличается от даты сообщения (YYYY-MM-DD), иначе null.
- Верни только валидный JSON без Markdown.

Формат ответа:
{{"results": [
  {{
    "id": <id сообщения из входа>,
    "is_relevant": boolean,
    "category": {" | ".join(f'"{c}"' for c in CATEGORIES)},
    "confidence": number 0..1,
    "risk_level": "low" | "medium" | "high",
    "in_source_region": boolean,
    "source_kind": "official" | "media" | "eyewitness" | "rumor" | "other",
    "location_text": string | null,
    "event_date": string | null,
    "summary": string (1 предложение по-русски)
  }}
]}}
Верни ровно по одному результату на каждое входное сообщение.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    telegram_id TEXT,
    title TEXT,
    region TEXT,
    city TEXT,
    linked_chat_id TEXT,
    members INTEGER,
    posts_fetched INTEGER DEFAULT 0,
    comments_fetched INTEGER DEFAULT 0,
    fetched_at TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    kind TEXT NOT NULL CHECK (kind IN ('post', 'comment')),
    telegram_chat_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    date TEXT,
    sender_id TEXT,
    text TEXT NOT NULL,
    views INTEGER,
    replies_count INTEGER,
    url TEXT,
    keyword_matched INTEGER NOT NULL DEFAULT 0,
    keyword_terms TEXT,
    UNIQUE (telegram_chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_items_channel_post ON items (channel_id, post_id);
CREATE INDEX IF NOT EXISTS ix_items_keyword ON items (keyword_matched);
CREATE TABLE IF NOT EXISTS post_comment_state (
    channel_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    replies_count INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (channel_id, post_id)
);
CREATE TABLE IF NOT EXISTS classifications (
    item_id INTEGER PRIMARY KEY REFERENCES items(id),
    model TEXT,
    is_relevant INTEGER NOT NULL,
    category TEXT,
    confidence REAL,
    risk_level TEXT,
    in_source_region INTEGER,
    source_kind TEXT,
    location_text TEXT,
    event_date TEXT,
    summary TEXT,
    raw TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_classifications_relevant ON classifications (is_relevant, category);
CREATE VIEW IF NOT EXISTS relevant_items AS
SELECT
    i.id AS item_id,
    ch.region,
    ch.city,
    ch.username AS channel,
    ch.title AS channel_title,
    i.kind,
    i.date,
    i.url,
    i.text,
    c.category,
    c.confidence,
    c.risk_level,
    c.in_source_region,
    c.source_kind,
    c.location_text,
    c.event_date,
    c.summary,
    i.keyword_terms,
    p.text AS parent_post_text
FROM items i
JOIN classifications c ON c.item_id = i.id
JOIN channels ch ON ch.id = i.channel_id
LEFT JOIN items p ON p.kind = 'post' AND p.channel_id = i.channel_id
    AND p.message_id = i.post_id AND i.kind = 'comment'
WHERE c.is_relevant = 1;
"""


@dataclass
class ChannelSpec:
    username: str
    region: str
    city: str | None


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_channels_file(path: Path) -> list[ChannelSpec]:
    specs: list[ChannelSpec] = []
    region = "Unknown"
    city: str | None = None
    link_re = re.compile(r"(?:t\.me/|telegram\.me/|@)([A-Za-z0-9_]{4,})")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = link_re.search(line)
        if match:
            specs.append(ChannelSpec(username=match.group(1).lower(), region=region, city=city))
            continue
        if line.startswith(("•", "-", "*")):
            heading = line.lstrip("•-* ").strip()
            city_match = re.search(r"\(([^)]+)\)\s*$", heading)
            city = city_match.group(1).strip() if city_match else None
            region = re.sub(r"\s*\([^)]*\)\s*$", "", heading).strip()
    seen: set[str] = set()
    unique: list[ChannelSpec] = []
    for spec in specs:
        if spec.username in seen:
            continue
        seen.add(spec.username)
        unique.append(spec)
    return unique


def open_db(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


class Fetcher:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        client,
        keyword_filter: KeywordFilter,
        posts_limit: int,
        refresh: bool = False,
    ) -> None:
        self.conn = conn
        self.client = client
        self.keyword_filter = keyword_filter
        self.posts_limit = posts_limit
        self.refresh = refresh

    def _upsert_channel(self, spec: ChannelSpec, **fields: Any) -> int:
        self.conn.execute(
            "INSERT INTO channels (username, region, city) VALUES (?, ?, ?) "
            "ON CONFLICT(username) DO UPDATE SET region=excluded.region, city=excluded.city",
            (spec.username, spec.region, spec.city),
        )
        if fields:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            self.conn.execute(
                f"UPDATE channels SET {assignments} WHERE username = ?",
                (*fields.values(), spec.username),
            )
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM channels WHERE username = ?", (spec.username,)).fetchone()
        return int(row["id"])

    def _insert_item(
        self,
        *,
        channel_id: int,
        kind: str,
        telegram_chat_id: str,
        message,
        post_id: int,
        url: str,
    ) -> bool:
        text = (getattr(message, "message", None) or "").strip()
        if not text:
            return False
        keyword = self.keyword_filter.analyze(text)
        replies = getattr(message, "replies", None)
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO items (channel_id, kind, telegram_chat_id, message_id, post_id, date, "
            "sender_id, text, views, replies_count, url, keyword_matched, keyword_terms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                channel_id,
                kind,
                telegram_chat_id,
                message.id,
                post_id,
                message.date.astimezone(timezone.utc).isoformat() if message.date else None,
                str(message.sender_id) if getattr(message, "sender_id", None) else None,
                text,
                getattr(message, "views", None),
                getattr(replies, "replies", None) if replies else None,
                url,
                int(keyword.matched),
                json.dumps(keyword.terms, ensure_ascii=False) if keyword.terms else None,
            ),
        )
        return cursor.rowcount > 0

    async def fetch_channel(self, spec: ChannelSpec) -> None:
        from telethon import utils
        from telethon.tl.functions.channels import GetFullChannelRequest

        existing = self.conn.execute(
            "SELECT fetched_at FROM channels WHERE username = ?", (spec.username,)
        ).fetchone()
        if existing and existing["fetched_at"] and not self.refresh:
            logger.info("[%s] @%s already fetched at %s, skipping", spec.region, spec.username, existing["fetched_at"])
            return

        try:
            entity = await self.client.get_entity(spec.username)
            full = await self.client(GetFullChannelRequest(entity))
        except Exception as exc:
            logger.error("channel @%s not accessible: %s", spec.username, exc)
            self._upsert_channel(spec, error=f"{type(exc).__name__}: {exc}")
            return

        linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
        channel_tg_id = str(utils.get_peer_id(entity))
        channel_id = self._upsert_channel(
            spec,
            telegram_id=channel_tg_id,
            title=getattr(entity, "title", None),
            linked_chat_id=f"-100{linked_chat_id}" if linked_chat_id else None,
            members=getattr(full.full_chat, "participants_count", None),
            error=None,
        )
        logger.info(
            "[%s] @%s %r: fetching last %s posts (comments=%s)",
            spec.region,
            spec.username,
            getattr(entity, "title", ""),
            self.posts_limit,
            bool(linked_chat_id),
        )

        posts = []
        async for message in self.client.iter_messages(entity, limit=self.posts_limit):
            posts.append(message)
        new_posts = 0
        for post in posts:
            if self._insert_item(
                channel_id=channel_id,
                kind="post",
                telegram_chat_id=channel_tg_id,
                message=post,
                post_id=post.id,
                url=f"https://t.me/{spec.username}/{post.id}",
            ):
                new_posts += 1
        self.conn.commit()
        logger.info("[%s] @%s: %s posts read, %s new with text", spec.region, spec.username, len(posts), new_posts)

        new_comments = 0
        with_comments = [p for p in posts if getattr(p, "replies", None) and p.replies.replies]
        if linked_chat_id and with_comments:
            history_result = await self._fetch_comments_from_discussion(
                spec=spec, channel_id=channel_id, entity=entity, full=full, posts=with_comments
            )
            if history_result is not None:
                new_comments = history_result
                with_comments = []
            for index, post in enumerate(with_comments, start=1):
                new_comments += await self._fetch_post_comments(
                    spec=spec, channel_id=channel_id, entity=entity, post=post
                )
                if index % 50 == 0:
                    logger.info(
                        "[%s] @%s: comments progress %s/%s posts, %s new comments",
                        spec.region,
                        spec.username,
                        index,
                        len(with_comments),
                        new_comments,
                    )

        totals = self.conn.execute(
            "SELECT SUM(kind='post') AS posts, SUM(kind='comment') AS comments FROM items WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        self._upsert_channel(
            spec,
            posts_fetched=totals["posts"] or 0,
            comments_fetched=totals["comments"] or 0,
            fetched_at=utcnow_iso(),
        )
        logger.info(
            "[%s] @%s done: %s posts, %s comments in DB (+%s new comments)",
            spec.region,
            spec.username,
            totals["posts"] or 0,
            totals["comments"] or 0,
            new_comments,
        )

    async def _fetch_comments_from_discussion(
        self, *, spec: ChannelSpec, channel_id: int, entity, full, posts
    ) -> int | None:
        """Read the linked discussion group history in pages of 100 instead of one GetReplies call per post.

        Returns None when the group history is not readable, so the caller falls back to per-post requests.
        """
        from datetime import timedelta

        from telethon import utils

        linked_chat_id = full.full_chat.linked_chat_id
        group = next((chat for chat in full.chats if getattr(chat, "id", None) == linked_chat_id), None)
        if group is None:
            return None
        post_ids = {post.id for post in posts}
        oldest = min(post.date for post in posts if post.date) - timedelta(hours=1)
        discussion_chat_id = str(utils.get_peer_id(group))

        root_to_post: dict[int, int] = {}
        candidates = []
        scanned = 0
        logger.info("[%s] @%s: reading discussion group %s history", spec.region, spec.username, discussion_chat_id)
        try:
            async for message in self.client.iter_messages(group, wait_time=0.5):
                scanned += 1
                if scanned % 5000 == 0:
                    logger.info(
                        "[%s] @%s: discussion scanned %s messages (at %s)",
                        spec.region,
                        spec.username,
                        scanned,
                        message.date.date() if message.date else "?",
                    )
                if message.date and message.date < oldest:
                    break
                forward = message.fwd_from
                if forward and forward.channel_post and getattr(forward.from_id, "channel_id", None) == entity.id:
                    if forward.channel_post in post_ids:
                        root_to_post[message.id] = forward.channel_post
                    continue
                reply = message.reply_to
                if reply is None:
                    continue
                root_id = getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None)
                if root_id:
                    candidates.append((root_id, message))
        except Exception as exc:
            logger.warning(
                "@%s: discussion history not readable (%s: %s), falling back to per-post comments",
                spec.username,
                type(exc).__name__,
                exc,
            )
            return None
        if not root_to_post:
            logger.warning(
                "@%s: discussion history returned no post threads (%s messages), falling back to per-post comments",
                spec.username,
                scanned,
            )
            return None

        inserted = 0
        for root_id, message in candidates:
            post_id = root_to_post.get(root_id)
            if post_id is None:
                continue
            if self._insert_item(
                channel_id=channel_id,
                kind="comment",
                telegram_chat_id=discussion_chat_id,
                message=message,
                post_id=post_id,
                url=f"https://t.me/{spec.username}/{post_id}?comment={message.id}",
            ):
                inserted += 1
        now = utcnow_iso()
        for post in posts:
            self.conn.execute(
                "INSERT INTO post_comment_state (channel_id, post_id, replies_count, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(channel_id, post_id) DO UPDATE SET replies_count=excluded.replies_count, "
                "fetched_at=excluded.fetched_at",
                (channel_id, post.id, post.replies.replies, now),
            )
        self.conn.commit()
        logger.info(
            "[%s] @%s: discussion scanned %s messages, matched %s roots, +%s new comments",
            spec.region,
            spec.username,
            scanned,
            len(root_to_post),
            inserted,
        )
        return inserted

    async def _fetch_post_comments(self, *, spec: ChannelSpec, channel_id: int, entity, post) -> int:
        from telethon import utils

        replies_count = post.replies.replies
        state = self.conn.execute(
            "SELECT replies_count FROM post_comment_state WHERE channel_id = ? AND post_id = ?",
            (channel_id, post.id),
        ).fetchone()
        if state and state["replies_count"] >= replies_count:
            return 0

        inserted = 0
        try:
            async for comment in self.client.iter_messages(entity, reply_to=post.id, wait_time=0.4):
                discussion_chat_id = str(utils.get_peer_id(comment.peer_id))
                if self._insert_item(
                    channel_id=channel_id,
                    kind="comment",
                    telegram_chat_id=discussion_chat_id,
                    message=comment,
                    post_id=post.id,
                    url=f"https://t.me/{spec.username}/{post.id}?comment={comment.id}",
                ):
                    inserted += 1
        except Exception as exc:
            logger.warning("@%s post %s: comments not accessible: %s", spec.username, post.id, exc)
            return inserted

        self.conn.execute(
            "INSERT INTO post_comment_state (channel_id, post_id, replies_count, fetched_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(channel_id, post_id) DO UPDATE SET replies_count=excluded.replies_count, "
            "fetched_at=excluded.fetched_at",
            (channel_id, post.id, replies_count, utcnow_iso()),
        )
        self.conn.commit()
        return inserted


class HistoryClassifier:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        model: str,
        batch_size: int,
        concurrency: int,
        max_calls: int | None,
    ) -> None:
        self.conn = conn
        self.model = model
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(concurrency)
        self.max_calls = max_calls
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost = 0.0

    def pending_items(self, usernames: list[str] | None) -> list[sqlite3.Row]:
        query = (
            "SELECT i.id, i.kind, i.date, i.text, ch.region, ch.city, ch.username, "
            "(SELECT p.text FROM items p WHERE p.kind='post' AND p.channel_id=i.channel_id "
            " AND p.message_id=i.post_id) AS parent_text "
            "FROM items i JOIN channels ch ON ch.id = i.channel_id "
            "LEFT JOIN classifications c ON c.item_id = i.id "
            "WHERE i.keyword_matched = 1 AND c.item_id IS NULL"
        )
        params: list[Any] = []
        if usernames:
            query += f" AND ch.username IN ({','.join('?' for _ in usernames)})"
            params.extend(usernames)
        query += " ORDER BY ch.username, i.id"
        return self.conn.execute(query, params).fetchall()

    async def run(self, usernames: list[str] | None) -> None:
        if not settings.openrouter_api_key:
            raise SystemExit("OPENROUTER_API_KEY is empty in .env")
        items = self.pending_items(usernames)
        groups: dict[tuple[str, str | None, str], list[sqlite3.Row]] = {}
        for item in items:
            groups.setdefault((item["region"], item["city"], item["username"]), []).append(item)
        batches = []
        for (region, city, _), rows in groups.items():
            for start in range(0, len(rows), self.batch_size):
                batches.append((region, city, rows[start : start + self.batch_size]))
        logger.info(
            "classification: %s keyword-matched items pending, %s batches, model=%s",
            len(items),
            len(batches),
            self.model,
        )
        async with httpx.AsyncClient(timeout=settings.openrouter_timeout_seconds * 3) as http:
            tasks = [self._classify_batch(http, region, city, rows) for region, city, rows in batches]
            done = 0
            for future in asyncio.as_completed(tasks):
                await future
                done += 1
                if done % 20 == 0 or done == len(tasks):
                    logger.info(
                        "classification progress %s/%s batches, calls=%s, cost=$%.3f",
                        done,
                        len(tasks),
                        self.calls,
                        self.cost,
                    )

    async def _classify_batch(self, http: httpx.AsyncClient, region: str, city: str | None, rows) -> None:
        async with self.semaphore:
            if self.max_calls is not None and self.calls >= self.max_calls:
                return
            results = await self._request(http, region, city, rows)
            if results is None and len(rows) > 1:
                for row in rows:
                    single = await self._request(http, region, city, [row])
                    if single:
                        self._store(single, [row])
                return
            if results:
                self._store(results, rows)

    async def _request(self, http: httpx.AsyncClient, region: str, city: str | None, rows) -> list[dict] | None:
        payload_items = []
        for row in rows:
            entry = {"id": row["id"], "kind": row["kind"], "date": (row["date"] or "")[:10], "text": row["text"][:1500]}
            if row["kind"] == "comment" and row["parent_text"]:
                entry["parent_post"] = row["parent_text"][:400]
            payload_items.append(entry)
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
            "messages": [
                {"role": "system", "content": HISTORY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"region": region, "city": city, "items": payload_items}, ensure_ascii=False
                    ),
                },
            ],
        }
        headers = {"Authorization": f"Bearer {settings.openrouter_api_key}"}
        for attempt in range(4):
            try:
                self.calls += 1
                response = await http.post(
                    f"{settings.openrouter_base_url.rstrip('/')}/chat/completions", json=payload, headers=headers
                )
                if response.status_code == 402:
                    raise SystemExit("OpenRouter: insufficient credits (402). Stopping.")
                if response.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(2 ** attempt * 3)
                    continue
                response.raise_for_status()
                data = response.json()
                usage = data.get("usage") or {}
                self.prompt_tokens += usage.get("prompt_tokens") or 0
                self.completion_tokens += usage.get("completion_tokens") or 0
                self.cost += float(usage.get("cost") or 0)
                content = data["choices"][0]["message"]["content"]
                parsed = self._parse(content)
                results = parsed.get("results")
                if isinstance(results, list):
                    return results
                logger.warning("unexpected LLM payload shape: %s", str(content)[:200])
                return None
            except SystemExit:
                raise
            except Exception as exc:
                logger.warning("LLM request failed (attempt %s): %s %s", attempt + 1, type(exc).__name__, exc)
                await asyncio.sleep(2 ** attempt * 2)
        return None

    def _parse(self, content: Any) -> dict:
        if isinstance(content, dict):
            return content
        text = str(content).strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                raise
            return json.loads(text[start : end + 1])

    def _store(self, results: list[dict], rows) -> None:
        valid_ids = {row["id"] for row in rows}
        now = utcnow_iso()
        for result in results:
            try:
                item_id = int(result.get("id"))
            except (TypeError, ValueError):
                continue
            if item_id not in valid_ids:
                continue
            category = result.get("category") if result.get("category") in CATEGORIES else "other_security"
            is_relevant = bool(result.get("is_relevant")) and category != "unrelated"
            try:
                confidence = min(max(float(result.get("confidence") or 0), 0.0), 1.0)
            except (TypeError, ValueError):
                confidence = 0.0
            self.conn.execute(
                "INSERT OR REPLACE INTO classifications (item_id, model, is_relevant, category, confidence, "
                "risk_level, in_source_region, source_kind, location_text, event_date, summary, raw, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    self.model,
                    int(is_relevant),
                    category if is_relevant else "unrelated",
                    confidence,
                    result.get("risk_level"),
                    int(bool(result.get("in_source_region", True))),
                    result.get("source_kind"),
                    result.get("location_text"),
                    result.get("event_date"),
                    result.get("summary"),
                    json.dumps(result, ensure_ascii=False),
                    now,
                ),
            )
        self.conn.commit()


async def make_client(session_name: str | None = None):
    from telethon import TelegramClient

    client = TelegramClient(
        session_name or settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        flood_sleep_threshold=900,
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("Telegram session is not authorized. Run: python -m app.cli.telegram_check login")
    return client


def select_specs(args: argparse.Namespace) -> list[ChannelSpec]:
    specs = parse_channels_file(Path(args.channels))
    if args.region:
        needles = [value.lower() for value in args.region]
        specs = [
            s
            for s in specs
            if any(needle in s.region.lower() or needle in (s.city or "").lower() for needle in needles)
        ]
    if args.only:
        wanted = {name.lstrip("@").lower() for name in args.only}
        specs = [s for s in specs if s.username in wanted]
    return specs


async def run_command(args: argparse.Namespace) -> None:
    conn = open_db(args.db)
    specs = select_specs(args)
    if not specs:
        raise SystemExit(f"No channels found in {args.channels} for given filters")
    logger.info("channels to process: %s", ", ".join(f"@{s.username} ({s.region})" for s in specs))

    if not args.skip_fetch:
        client = await make_client(args.session)
        try:
            fetcher = Fetcher(
                conn=conn,
                client=client,
                keyword_filter=KeywordFilter(HISTORY_KEYWORD_PATTERNS),
                posts_limit=args.posts,
                refresh=args.refresh,
            )
            for spec in specs:
                try:
                    await fetcher.fetch_channel(spec)
                except Exception:
                    logger.exception("fetch failed for @%s", spec.username)
        finally:
            await client.disconnect()

    if not args.skip_classify:
        classifier = HistoryClassifier(
            conn=conn,
            model=args.model or settings.openrouter_model,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            max_calls=args.max_calls,
        )
        await classifier.run([s.username for s in specs])
        logger.info(
            "classification finished: calls=%s, prompt_tokens=%s, completion_tokens=%s, cost=$%.3f",
            classifier.calls,
            classifier.prompt_tokens,
            classifier.completion_tokens,
            classifier.cost,
        )

    print_stats(conn)


def retag_command(args: argparse.Namespace) -> None:
    conn = open_db(args.db)
    keyword_filter = KeywordFilter(HISTORY_KEYWORD_PATTERNS)
    rows = conn.execute("SELECT id, text FROM items").fetchall()
    for row in rows:
        match = keyword_filter.analyze(row["text"])
        conn.execute(
            "UPDATE items SET keyword_matched = ?, keyword_terms = ? WHERE id = ?",
            (int(match.matched), json.dumps(match.terms, ensure_ascii=False) if match.terms else None, row["id"]),
        )
    conn.commit()
    print_stats(conn)


def print_stats(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT ch.region, ch.username, ch.title, ch.error,
               SUM(i.kind='post') AS posts,
               SUM(i.kind='comment') AS comments,
               SUM(i.keyword_matched) AS kw,
               SUM(c.item_id IS NOT NULL) AS classified,
               SUM(COALESCE(c.is_relevant, 0)) AS relevant
        FROM channels ch
        LEFT JOIN items i ON i.channel_id = ch.id
        LEFT JOIN classifications c ON c.item_id = i.id
        GROUP BY ch.id
        ORDER BY ch.region, ch.username
        """
    ).fetchall()
    print(f"\n{'region':<28} {'channel':<24} {'posts':>6} {'comments':>9} {'kw':>6} {'classif':>8} {'relevant':>9}")
    for row in rows:
        print(
            f"{(row['region'] or '')[:28]:<28} @{row['username'][:23]:<23} {row['posts'] or 0:>6} "
            f"{row['comments'] or 0:>9} {row['kw'] or 0:>6} {row['classified'] or 0:>8} {row['relevant'] or 0:>9}"
            + (f"  ERROR: {row['error']}" if row["error"] else "")
        )
    categories = conn.execute(
        "SELECT category, COUNT(*) AS n FROM classifications WHERE is_relevant = 1 GROUP BY category ORDER BY n DESC"
    ).fetchall()
    if categories:
        print("\nrelevant by category: " + ", ".join(f"{r['category']}={r['n']}" for r in categories))


def stats_command(args: argparse.Namespace) -> None:
    print_stats(open_db(args.db))


def export_command(args: argparse.Namespace) -> None:
    conn = open_db(args.db)
    rows = conn.execute("SELECT * FROM relevant_items ORDER BY region, channel, date").fetchall()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        if rows:
            writer.writerow(rows[0].keys())
        for row in rows:
            writer.writerow(list(row))
    print(f"exported {len(rows)} relevant items to {args.out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Historical backfill of Telegram channels with LLM filtering.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Fetch posts+comments and classify keyword matches.")
    run.add_argument("--channels", default="privolze.MD", help="File with region headings and t.me links.")
    run.add_argument("--region", nargs="*", help="Only regions/cities containing any of these substrings.")
    run.add_argument("--session", help="Telethon session path (default TELEGRAM_SESSION_NAME from .env).")
    run.add_argument("--only", nargs="*", help="Only these channel usernames.")
    run.add_argument("--posts", type=int, default=900)
    run.add_argument("--model", help="OpenRouter model (default OPENROUTER_MODEL from .env).")
    run.add_argument("--batch-size", type=int, default=10)
    run.add_argument("--concurrency", type=int, default=6)
    run.add_argument("--max-calls", type=int, help="Stop LLM classification after this many requests.")
    run.add_argument("--refresh", action="store_true", help="Re-fetch channels that were already fetched.")
    run.add_argument("--skip-fetch", action="store_true")
    run.add_argument("--skip-classify", action="store_true")
    run.set_defaults(func=run_command, is_async=True)

    stats = subparsers.add_parser("stats", help="Show per-channel counters.")
    stats.set_defaults(func=stats_command, is_async=False)

    retag = subparsers.add_parser("retag", help="Re-run keyword filter over stored items.")
    retag.set_defaults(func=retag_command, is_async=False)

    export = subparsers.add_parser("export", help="Export relevant items to CSV.")
    export.add_argument("--out", default="data/privolze_relevant.csv")
    export.set_defaults(func=export_command, is_async=False)
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("telethon.client.users").setLevel(logging.INFO)
    args = build_parser().parse_args()
    if args.is_async:
        asyncio.run(args.func(args))
    else:
        args.func(args)


if __name__ == "__main__":
    main()
