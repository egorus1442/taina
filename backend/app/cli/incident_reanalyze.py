"""Re-analysis of collected posts/comments for a wide set of emergencies and incidents.

Works on the DB produced by app.cli.history_backfill (default data/history_privolze.db) and writes to new tables,
leaving the original drone-focused `classifications` intact.

Usage (from repo root):
    PYTHONPATH=backend backend/.venv-local/bin/python -m app.cli.incident_reanalyze tag
    PYTHONPATH=backend backend/.venv-local/bin/python -m app.cli.incident_reanalyze estimate --model google/gemini-2.5-flash-lite
    PYTHONPATH=backend backend/.venv-local/bin/python -m app.cli.incident_reanalyze classify --model google/gemini-2.5-flash-lite --max-cost 3
    PYTHONPATH=backend backend/.venv-local/bin/python -m app.cli.incident_reanalyze stats
    PYTHONPATH=backend backend/.venv-local/bin/python -m app.cli.incident_reanalyze export --out data/privolze_incidents.csv
"""

import argparse
import asyncio
import csv
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.services.incident_taxonomy import CATEGORY_BY_CODE, LLM_CATEGORIES, IncidentKeywordTagger


logger = logging.getLogger("incident_reanalyze")

DEFAULT_DB_PATH = "data/history_privolze.db"
POST_TEXT_LIMIT = 1200
COMMENT_TEXT_LIMIT = 600
PARENT_TEXT_LIMIT = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS incident_kw (
    item_id INTEGER PRIMARY KEY REFERENCES items(id),
    categories TEXT NOT NULL,
    terms TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_kw_categories (
    item_id INTEGER NOT NULL REFERENCES items(id),
    category TEXT NOT NULL,
    PRIMARY KEY (item_id, category)
);
CREATE INDEX IF NOT EXISTS ix_incident_kw_categories_category ON incident_kw_categories (category);
CREATE TABLE IF NOT EXISTS incident_classifications (
    item_id INTEGER PRIMARY KEY REFERENCES items(id),
    model TEXT,
    is_incident INTEGER NOT NULL,
    primary_category TEXT,
    categories TEXT,
    confidence REAL,
    severity TEXT,
    in_source_region INTEGER,
    location_text TEXT,
    event_date TEXT,
    dead INTEGER,
    injured INTEGER,
    summary TEXT,
    raw TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_item_categories (
    item_id INTEGER NOT NULL REFERENCES items(id),
    category TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, category)
);
CREATE INDEX IF NOT EXISTS ix_incident_item_categories_category ON incident_item_categories (category);
CREATE TABLE IF NOT EXISTS incident_category_labels (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    group_name TEXT NOT NULL
);
DROP VIEW IF EXISTS incident_items;
CREATE VIEW incident_items AS
SELECT
    i.id AS item_id,
    ch.region,
    ch.city,
    ch.username AS channel,
    i.kind,
    i.date,
    i.url,
    i.text,
    pl.group_name AS primary_group,
    ic.primary_category,
    pl.label AS primary_label,
    (SELECT group_concat(l.label, '; ') FROM incident_item_categories c
        JOIN incident_category_labels l ON l.code = c.category WHERE c.item_id = i.id) AS all_labels,
    ic.categories,
    ic.confidence,
    ic.severity,
    ic.in_source_region,
    ic.location_text,
    ic.event_date,
    ic.dead,
    ic.injured,
    ic.summary,
    kw.categories AS keyword_categories,
    p.text AS parent_post_text
FROM items i
JOIN incident_classifications ic ON ic.item_id = i.id
JOIN channels ch ON ch.id = i.channel_id
LEFT JOIN incident_category_labels pl ON pl.code = ic.primary_category
LEFT JOIN incident_kw kw ON kw.item_id = i.id
LEFT JOIN items p ON i.kind = 'comment' AND p.kind = 'post' AND p.channel_id = i.channel_id AND p.message_id = i.post_id
WHERE ic.is_incident = 1;
"""


def build_system_prompt() -> str:
    categories = "\n".join(f"- {code}: {label} [{group}]" for code, (label, group) in LLM_CATEGORIES.items())
    return f"""
Ты аналитик гражданского мониторинга ЧС и происшествий в регионах России. Тебе дают пачку сообщений из
Telegram-каналов одного региона: посты и комментарии под ними. Для каждого сообщения реши, сообщает ли оно о
конкретном происшествии, ЧС, угрозе или официальном предупреждении о них, и отнеси его к категориям.

Категории (код: описание [группа]):
{categories}

Правила:
- r=1, если в сообщении есть информация о конкретном событии/угрозе: новость, официальное сообщение, предупреждение
  МЧС/властей, рассказ очевидца, комментарий с подтверждением или деталями («у нас тоже горит», «видел ДТП на ...»).
- r=0: реклама, розыгрыши, поздравления, спорт, культура, политика и мнения без конкретного происшествия, общие
  обсуждения, чистые эмоции без фактов («ужас», «жесть», «соболезную»), шутки, вопросы без информации,
  плановые объявления без опасности (ремонт дорог, обычные новости ЖКХ без аварии допускаются как utility_accident
  только если есть отключение/авария).
- Одно сообщение может относиться к нескольким категориям (например, ДТП с погибшими: road_accident + death_incident;
  пожар после атаки БПЛА: drone_attack + attack_aftermath + fire). p — главная категория.
- Для комментария учитывай контекст родительского поста (parent или parent_item), но оценивай сам комментарий.
- События в других регионах тоже размечай, но ставь reg=false.
- Никаких тактических или военных рекомендаций — только классификация.
- loc — самое конкретное место (населённый пункт, улица, объект) в именительном падеже или null.
- dt — дата события YYYY-MM-DD, только если явно указана в тексте и отличается от даты сообщения, иначе null.
- dead / inj — число погибших / пострадавших, если указано, иначе null.
- s — суть по-русски, не длиннее 15 слов.

Ответ — только JSON без Markdown:
{{"results": [
  {{"id": 1, "r": 1, "c": ["fire", "death_incident"], "p": "fire", "conf": 0.9, "sev": "low|medium|high",
    "reg": true, "loc": "Уфа, улица Ленина", "dt": null, "dead": 1, "inj": null, "s": "..."}},
  {{"id": 2, "r": 0}}
]}}
Для r=0 возвращай только id и r. Ровно один результат на каждое входное сообщение.
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_db(path: str) -> sqlite3.Connection:
    if not Path(path).exists():
        raise SystemExit(f"DB not found: {path}")
    conn = sqlite3.connect(path, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO incident_category_labels (code, label, group_name) VALUES (?, ?, ?) "
        "ON CONFLICT(code) DO UPDATE SET label=excluded.label, group_name=excluded.group_name",
        [(code, label, group) for code, (label, group) in LLM_CATEGORIES.items()],
    )
    conn.commit()
    return conn


def tag_command(args: argparse.Namespace) -> None:
    conn = open_db(args.db)
    tagger = IncidentKeywordTagger()
    conn.execute("DELETE FROM incident_kw")
    conn.execute("DELETE FROM incident_kw_categories")
    rows = conn.execute("SELECT id, text FROM items").fetchall()
    tagged = 0
    for index, row in enumerate(rows, start=1):
        hits = tagger.analyze(row["text"])
        if hits:
            tagged += 1
            conn.execute(
                "INSERT INTO incident_kw (item_id, categories, terms) VALUES (?, ?, ?)",
                (row["id"], json.dumps(sorted(hits), ensure_ascii=False), json.dumps(hits, ensure_ascii=False)),
            )
            conn.executemany(
                "INSERT INTO incident_kw_categories (item_id, category) VALUES (?, ?)",
                [(row["id"], code) for code in hits],
            )
        if index % 20000 == 0:
            conn.commit()
            logger.info("tagged %s/%s items, %s matched", index, len(rows), tagged)
    conn.commit()
    logger.info("keyword tagging done: %s of %s items matched", tagged, len(rows))
    print_kw_stats(conn)


def print_kw_stats(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT c.category, SUM(i.kind='post') AS posts, SUM(i.kind='comment') AS comments "
        "FROM incident_kw_categories c JOIN items i ON i.id = c.item_id GROUP BY c.category ORDER BY posts + comments DESC"
    ).fetchall()
    print(f"\n{'keyword category':<22} {'label':<52} {'posts':>7} {'comments':>9}")
    for row in rows:
        label = CATEGORY_BY_CODE[row["category"]].label if row["category"] in CATEGORY_BY_CODE else ""
        print(f"{row['category']:<22} {label[:52]:<52} {row['posts']:>7} {row['comments']:>9}")


def pending_rows(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    query = (
        "SELECT i.id, i.kind, i.date, i.text, i.post_id, i.channel_id, ch.region, ch.city, ch.username, "
        "(SELECT p.id FROM items p WHERE p.kind='post' AND p.channel_id=i.channel_id AND p.message_id=i.post_id) "
        "  AS parent_item_id, "
        "(SELECT p.text FROM items p WHERE p.kind='post' AND p.channel_id=i.channel_id AND p.message_id=i.post_id) "
        "  AS parent_text "
        "FROM incident_kw kw JOIN items i ON i.id = kw.item_id JOIN channels ch ON ch.id = i.channel_id "
        "LEFT JOIN incident_classifications ic ON ic.item_id = i.id WHERE ic.item_id IS NULL"
    )
    params: list[Any] = []
    if getattr(args, "kind", None):
        query += " AND i.kind = ?"
        params.append(args.kind)
    if getattr(args, "region", None):
        query += " AND (" + " OR ".join("ch.region LIKE ?" for _ in args.region) + ")"
        params.extend(f"%{value}%" for value in args.region)
    if getattr(args, "only", None):
        query += f" AND ch.username IN ({','.join('?' for _ in args.only)})"
        params.extend(name.lstrip("@").lower() for name in args.only)
    if getattr(args, "ids_file", None):
        ids = [int(line) for line in Path(args.ids_file).read_text().split() if line.strip()]
        query += f" AND i.id IN ({','.join(str(item_id) for item_id in ids)})"
    query += " ORDER BY i.channel_id, i.post_id DESC, i.kind DESC, i.id"
    if getattr(args, "limit", None):
        query += " LIMIT ?"
        params.append(args.limit)
    return conn.execute(query, params).fetchall()


def make_batches(rows: list[sqlite3.Row], batch_size: int) -> list[list[sqlite3.Row]]:
    batches: list[list[sqlite3.Row]] = []
    current: list[sqlite3.Row] = []
    for row in rows:
        if current and (len(current) >= batch_size or current[-1]["channel_id"] != row["channel_id"]):
            batches.append(current)
            current = []
        current.append(row)
    if current:
        batches.append(current)
    return batches


def build_user_payload(rows: list[sqlite3.Row]) -> str:
    ids_in_batch = {row["id"] for row in rows}
    parents: dict[str, str] = {}
    items = []
    for row in rows:
        limit = POST_TEXT_LIMIT if row["kind"] == "post" else COMMENT_TEXT_LIMIT
        entry: dict[str, Any] = {"id": row["id"], "kind": row["kind"], "date": (row["date"] or "")[:10], "text": row["text"][:limit]}
        if row["kind"] == "comment":
            if row["parent_item_id"] in ids_in_batch:
                entry["parent_item"] = row["parent_item_id"]
            elif row["parent_text"]:
                key = f"p{row['post_id']}"
                parents.setdefault(key, row["parent_text"][:PARENT_TEXT_LIMIT])
                entry["parent"] = key
        items.append(entry)
    payload: dict[str, Any] = {"region": rows[0]["region"], "city": rows[0]["city"], "items": items}
    if parents:
        payload["parents"] = parents
    return json.dumps(payload, ensure_ascii=False)


async def fetch_model_pricing(model: str) -> tuple[float, float] | None:
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.get(f"{settings.openrouter_base_url.rstrip('/')}/models")
            response.raise_for_status()
    except Exception:
        return None
    for entry in response.json().get("data", []):
        if entry.get("id") == model:
            pricing = entry.get("pricing") or {}
            return float(pricing.get("prompt") or 0), float(pricing.get("completion") or 0)
    return None


def estimate_command(args: argparse.Namespace) -> None:
    conn = open_db(args.db)
    rows = pending_rows(conn, args)
    batches = make_batches(rows, args.batch_size)
    system_chars = len(build_system_prompt())
    payload_chars = sum(len(build_user_payload(batch)) for batch in batches)
    chars_per_token = 2.6
    input_tokens = (payload_chars + system_chars * len(batches)) / chars_per_token
    output_tokens = len(rows) * args.output_tokens_per_item
    print(f"pending items: {len(rows)} ({sum(r['kind'] == 'post' for r in rows)} posts), batches: {len(batches)}")
    print(f"estimated tokens: input ~{input_tokens / 1e6:.1f}M, output ~{output_tokens / 1e6:.1f}M")
    models = args.model or [settings.openrouter_model]
    for model in models:
        pricing = asyncio.run(fetch_model_pricing(model))
        if not pricing:
            print(f"{model}: pricing unavailable")
            continue
        cost = input_tokens * pricing[0] + output_tokens * pricing[1]
        print(f"{model}: ~${cost:.2f} (in ${pricing[0] * 1e6:.2f}/M, out ${pricing[1] * 1e6:.2f}/M)")


class IncidentClassifier:
    def __init__(self, *, conn: sqlite3.Connection, model: str, concurrency: int, max_cost: float | None) -> None:
        self.conn = conn
        self.model = model
        self.semaphore = asyncio.Semaphore(concurrency)
        self.max_cost = max_cost
        self.system_prompt = build_system_prompt()
        self.calls = 0
        self.failed_batches = 0
        self.cost = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.stopped = False

    async def run(self, batches: list[list[sqlite3.Row]]) -> None:
        if not settings.openrouter_api_key:
            raise SystemExit("OPENROUTER_API_KEY is empty in .env")
        async with httpx.AsyncClient(timeout=180) as http:
            tasks = [self._process(http, batch) for batch in batches]
            done = 0
            for future in asyncio.as_completed(tasks):
                await future
                done += 1
                if done % 50 == 0 or done == len(tasks):
                    logger.info(
                        "progress %s/%s batches, calls=%s, failed=%s, cost=$%.3f",
                        done,
                        len(tasks),
                        self.calls,
                        self.failed_batches,
                        self.cost,
                    )

    async def _process(self, http: httpx.AsyncClient, batch: list[sqlite3.Row]) -> None:
        async with self.semaphore:
            if self.stopped:
                return
            if self.max_cost is not None and self.cost >= self.max_cost:
                if not self.stopped:
                    logger.warning("max cost $%.2f reached, stopping", self.max_cost)
                self.stopped = True
                return
            results = await self._request(http, batch)
            if results is None and len(batch) > 1:
                middle = len(batch) // 2
                for half in (batch[:middle], batch[middle:]):
                    half_results = await self._request(http, half)
                    if half_results is not None:
                        self._store(half_results, half)
                    else:
                        self.failed_batches += 1
                return
            if results is None:
                self.failed_batches += 1
                return
            self._store(results, batch)

    async def _request(self, http: httpx.AsyncClient, batch: list[sqlite3.Row]) -> list[dict] | None:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": build_user_payload(batch)},
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
                    logger.error("OpenRouter: insufficient credits (402), stopping")
                    self.stopped = True
                    return None
                if response.status_code in (408, 429, 500, 502, 503, 504):
                    await asyncio.sleep(2 ** attempt * 3)
                    continue
                response.raise_for_status()
                data = response.json()
                usage = data.get("usage") or {}
                self.prompt_tokens += usage.get("prompt_tokens") or 0
                self.completion_tokens += usage.get("completion_tokens") or 0
                self.cost += float(usage.get("cost") or 0)
                content = data["choices"][0]["message"]["content"]
                results = self._parse(content).get("results")
                if isinstance(results, list):
                    return results
                logger.warning("unexpected payload shape: %s", str(content)[:200])
                return None
            except Exception as exc:
                logger.warning("request failed (attempt %s): %s %s", attempt + 1, type(exc).__name__, exc)
                await asyncio.sleep(2 ** attempt * 2)
        return None

    def _parse(self, content: Any) -> dict:
        if isinstance(content, dict):
            return content
        text = str(content or "").strip()
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

    def _store(self, results: list[dict], batch: list[sqlite3.Row]) -> None:
        valid_ids = {row["id"] for row in batch}
        now = utcnow_iso()
        for result in results:
            if not isinstance(result, dict):
                continue
            try:
                item_id = int(result.get("id"))
            except (TypeError, ValueError):
                continue
            if item_id not in valid_ids:
                continue
            categories = [code for code in (result.get("c") or []) if code in LLM_CATEGORIES]
            primary = result.get("p") if result.get("p") in LLM_CATEGORIES else (categories[0] if categories else None)
            if primary and primary not in categories:
                categories.insert(0, primary)
            is_incident = bool(result.get("r")) and bool(categories)
            self.conn.execute(
                "INSERT OR REPLACE INTO incident_classifications (item_id, model, is_incident, primary_category, "
                "categories, confidence, severity, in_source_region, location_text, event_date, dead, injured, "
                "summary, raw, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    self.model,
                    int(is_incident),
                    primary if is_incident else None,
                    json.dumps(categories, ensure_ascii=False) if is_incident else None,
                    self._float(result.get("conf")),
                    result.get("sev") if is_incident else None,
                    int(bool(result.get("reg", True))) if is_incident else None,
                    result.get("loc") if is_incident else None,
                    result.get("dt") if is_incident else None,
                    self._int(result.get("dead")),
                    self._int(result.get("inj")),
                    result.get("s") if is_incident else None,
                    json.dumps(result, ensure_ascii=False),
                    now,
                ),
            )
            self.conn.execute("DELETE FROM incident_item_categories WHERE item_id = ?", (item_id,))
            if is_incident:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO incident_item_categories (item_id, category, is_primary) VALUES (?, ?, ?)",
                    [(item_id, code, int(code == primary)) for code in categories],
                )
        self.conn.commit()

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return min(max(float(value), 0.0), 1.0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def classify_command(args: argparse.Namespace) -> None:
    conn = open_db(args.db)
    if not conn.execute("SELECT 1 FROM incident_kw LIMIT 1").fetchone():
        raise SystemExit("Run `tag` first")
    rows = pending_rows(conn, args)
    batches = make_batches(rows, args.batch_size)
    model = args.model or settings.openrouter_model
    logger.info("classify: %s pending items, %s batches, model=%s, max_cost=%s", len(rows), len(batches), model, args.max_cost)
    classifier = IncidentClassifier(conn=conn, model=model, concurrency=args.concurrency, max_cost=args.max_cost)
    asyncio.run(classifier.run(batches))
    logger.info(
        "classification finished: calls=%s, failed_batches=%s, prompt_tokens=%s, completion_tokens=%s, cost=$%.3f",
        classifier.calls,
        classifier.failed_batches,
        classifier.prompt_tokens,
        classifier.completion_tokens,
        classifier.cost,
    )
    print_stats(conn)


def reset_command(args: argparse.Namespace) -> None:
    conn = open_db(args.db)
    conn.execute("DELETE FROM incident_item_categories")
    conn.execute("DELETE FROM incident_classifications")
    conn.commit()
    print("incident classifications cleared")


def print_stats(conn: sqlite3.Connection) -> None:
    totals = conn.execute(
        "SELECT (SELECT COUNT(*) FROM incident_kw) AS tagged, COUNT(*) AS classified, SUM(is_incident) AS incidents "
        "FROM incident_classifications"
    ).fetchone()
    print(
        f"\nkeyword-matched: {totals['tagged']}, classified: {totals['classified']}, incidents: {totals['incidents'] or 0}"
    )
    rows = conn.execute(
        "SELECT l.group_name, l.label, c.category, COUNT(*) AS n, SUM(c.is_primary) AS primary_n "
        "FROM incident_item_categories c JOIN incident_category_labels l ON l.code = c.category "
        "GROUP BY c.category ORDER BY l.group_name, n DESC"
    ).fetchall()
    group = None
    for row in rows:
        if row["group_name"] != group:
            group = row["group_name"]
            print(f"\n{group}")
        print(f"  {row['label'][:60]:<60} {row['n']:>7} (главная: {row['primary_n']})")
    regions = conn.execute(
        "SELECT ch.region, COUNT(*) AS n FROM incident_classifications ic JOIN items i ON i.id = ic.item_id "
        "JOIN channels ch ON ch.id = i.channel_id WHERE ic.is_incident = 1 GROUP BY ch.region ORDER BY n DESC"
    ).fetchall()
    if regions:
        print("\nпо регионам: " + ", ".join(f"{r['region']}={r['n']}" for r in regions))


def stats_command(args: argparse.Namespace) -> None:
    print_stats(open_db(args.db))


REGION_UTC_OFFSET_HOURS = {
    "Республика Башкортостан": 5,
    "Оренбургская область": 5,
    "Пермский край": 5,
    "Удмуртская Республика": 4,
    "Самарская область": 4,
    "Саратовская область": 4,
    "Ульяновская область": 4,
}


def local_time(utc_iso: str | None, region: str | None) -> str | None:
    if not utc_iso:
        return None
    try:
        moment = datetime.fromisoformat(utc_iso)
    except ValueError:
        return None
    offset = timezone(timedelta(hours=REGION_UTC_OFFSET_HOURS.get(region or "", 3)))
    return moment.astimezone(offset).isoformat()


def export_command(args: argparse.Namespace) -> None:
    conn = open_db(args.db)
    rows = conn.execute("SELECT * FROM incident_items ORDER BY region, primary_group, date").fetchall()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        if rows:
            columns = list(rows[0].keys())
            date_index = columns.index("date") + 1
            writer.writerow(columns[:date_index] + ["date_local"] + columns[date_index:])
            for row in rows:
                values = list(row)
                writer.writerow(values[:date_index] + [local_time(row["date"], row["region"])] + values[date_index:])
    print(f"exported {len(rows)} incident items to {args.out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Re-analyze collected Telegram items for emergencies/incidents.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    tag = subparsers.add_parser("tag", help="Keyword-tag all stored items (local, free).")
    tag.set_defaults(func=tag_command)

    def add_filters(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--region", nargs="*")
        sub.add_argument("--only", nargs="*", help="Channel usernames")
        sub.add_argument("--kind", choices=["post", "comment"])
        sub.add_argument("--limit", type=int)
        sub.add_argument("--ids-file", help="File with item ids (whitespace-separated) to restrict processing.")
        sub.add_argument("--batch-size", type=int, default=20)

    estimate = subparsers.add_parser("estimate", help="Estimate LLM cost for pending items.")
    add_filters(estimate)
    estimate.add_argument("--model", nargs="*")
    estimate.add_argument("--output-tokens-per-item", type=int, default=45)
    estimate.set_defaults(func=estimate_command)

    classify = subparsers.add_parser("classify", help="LLM classification of keyword-matched items.")
    add_filters(classify)
    classify.add_argument("--model")
    classify.add_argument("--concurrency", type=int, default=8)
    classify.add_argument("--max-cost", type=float, help="Stop after spending this many USD.")
    classify.set_defaults(func=classify_command)

    reset = subparsers.add_parser("reset", help="Delete incident classifications (keyword tags are kept).")
    reset.set_defaults(func=reset_command)

    stats = subparsers.add_parser("stats")
    stats.set_defaults(func=stats_command)

    export = subparsers.add_parser("export")
    export.add_argument("--out", default="data/privolze_incidents.csv")
    export.set_defaults(func=export_command)
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
