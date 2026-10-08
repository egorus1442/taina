"""Package incident results for sharing: a standalone SQLite database and an Excel workbook.

Usage (from the project root):
    PYTHONPATH=backend backend/.venv-local/bin/python -m app.cli.incident_deliver --out-dir data/deliverable
"""

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

import xlsxwriter

from app.cli.incident_reanalyze import DEFAULT_DB_PATH, local_time, open_db

EXCEL_CELL_LIMIT = 32_000
KIND_RU = {"post": "пост", "comment": "комментарий"}
SEVERITY_RU = {"low": "низкая", "medium": "средняя", "high": "высокая", "critical": "критическая"}

DELIVERY_SCHEMA = """
CREATE TABLE channels (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    title TEXT,
    region TEXT,
    city TEXT,
    url TEXT,
    members INTEGER,
    posts_collected INTEGER,
    comments_collected INTEGER,
    incidents INTEGER
);
CREATE TABLE categories (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    group_name TEXT NOT NULL
);
CREATE TABLE incidents (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    channel TEXT NOT NULL,
    region TEXT,
    city TEXT,
    kind TEXT NOT NULL,
    date_utc TEXT,
    date_local TEXT,
    url TEXT,
    text TEXT,
    parent_post_text TEXT,
    primary_group TEXT,
    primary_category TEXT REFERENCES categories(code),
    primary_label TEXT,
    all_labels TEXT,
    severity TEXT,
    confidence REAL,
    in_source_region INTEGER,
    location_text TEXT,
    event_date TEXT,
    dead INTEGER,
    injured INTEGER,
    summary TEXT
);
CREATE TABLE incident_categories (
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    category TEXT NOT NULL REFERENCES categories(code),
    is_primary INTEGER NOT NULL,
    PRIMARY KEY (incident_id, category)
);
CREATE INDEX ix_incidents_region ON incidents (region);
CREATE INDEX ix_incidents_primary_category ON incidents (primary_category);
CREATE INDEX ix_incidents_date_utc ON incidents (date_utc);
CREATE INDEX ix_incident_categories_category ON incident_categories (category);
CREATE VIEW summary_by_region_category AS
SELECT i.region, c.group_name, c.label, COUNT(*) AS incidents, SUM(ic.is_primary) AS as_primary
FROM incident_categories ic
JOIN incidents i ON i.id = ic.incident_id
JOIN categories c ON c.code = ic.category
GROUP BY i.region, c.code
ORDER BY i.region, c.group_name, incidents DESC;
"""


def load_incidents(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT ii.*, i.channel_id FROM incident_items ii JOIN items i ON i.id = ii.item_id "
        "ORDER BY ii.region, ii.date"
    ).fetchall()
    incidents = []
    for row in rows:
        record = dict(row)
        record["date_local"] = local_time(record["date"], record["region"])
        incidents.append(record)
    return incidents


def build_database(conn: sqlite3.Connection, incidents: list[dict], path: Path) -> None:
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    out.executescript(DELIVERY_SCHEMA)
    incidents_per_channel: dict[int, int] = defaultdict(int)
    for record in incidents:
        incidents_per_channel[record["channel_id"]] += 1
    out.executemany(
        "INSERT INTO channels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                row["id"], row["username"], row["title"], row["region"], row["city"], f"https://t.me/{row['username']}",
                row["members"], row["posts"], row["comments"], incidents_per_channel.get(row["id"], 0),
            )
            for row in conn.execute(
                "SELECT ch.*, (SELECT COUNT(*) FROM items i WHERE i.channel_id = ch.id AND i.kind = 'post') AS posts, "
                "(SELECT COUNT(*) FROM items i WHERE i.channel_id = ch.id AND i.kind = 'comment') AS comments "
                "FROM channels ch ORDER BY ch.region, ch.username"
            )
        ],
    )
    out.executemany(
        "INSERT INTO categories VALUES (?, ?, ?)",
        [tuple(row) for row in conn.execute("SELECT code, label, group_name FROM incident_category_labels")],
    )
    out.executemany(
        "INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                r["item_id"], r["channel_id"], r["channel"], r["region"], r["city"], r["kind"], r["date"], r["date_local"],
                r["url"], r["text"], r["parent_post_text"], r["primary_group"], r["primary_category"], r["primary_label"],
                r["all_labels"], r["severity"], r["confidence"], r["in_source_region"], r["location_text"],
                r["event_date"], r["dead"], r["injured"], r["summary"],
            )
            for r in incidents
        ],
    )
    out.executemany(
        "INSERT INTO incident_categories VALUES (?, ?, ?)",
        [
            tuple(row)
            for row in conn.execute(
                "SELECT c.item_id, c.category, c.is_primary FROM incident_item_categories c "
                "JOIN incident_classifications ic ON ic.item_id = c.item_id WHERE ic.is_incident = 1"
            )
        ],
    )
    out.commit()
    out.execute("VACUUM")
    out.close()


def cell_text(value: str | None) -> str | None:
    if value and len(value) > EXCEL_CELL_LIMIT:
        return value[:EXCEL_CELL_LIMIT] + " …"
    return value


def build_workbook(conn: sqlite3.Connection, incidents: list[dict], path: Path) -> None:
    workbook = xlsxwriter.Workbook(str(path), {"strings_to_urls": False})
    header = workbook.add_format({"bold": True, "bg_color": "#DDEBF7", "border": 1, "text_wrap": True, "valign": "top"})
    total = workbook.add_format({"bold": True, "bg_color": "#F2F2F2", "border": 1})
    number = workbook.add_format({"border": 1})
    plain = workbook.add_format({"valign": "top"})
    labels = {row["code"]: (row["label"], row["group_name"]) for row in conn.execute("SELECT * FROM incident_category_labels")}

    def write_table(sheet, columns: list[tuple[str, int]], rows: list[list]) -> None:
        for col, (title, width) in enumerate(columns):
            sheet.write(0, col, title, header)
            sheet.set_column(col, col, width)
        for row_index, values in enumerate(rows, start=1):
            for col, value in enumerate(values):
                sheet.write(row_index, col, value, plain)
        sheet.freeze_panes(1, 0)
        sheet.autofilter(0, 0, max(len(rows), 1), len(columns) - 1)

    regions = sorted({r["region"] for r in incidents})
    groups = sorted({group for _, group in labels.values()})
    by_region_group: dict[tuple[str, str], int] = defaultdict(int)
    for r in incidents:
        by_region_group[(r["region"], r["primary_group"])] += 1

    summary = workbook.add_worksheet("Сводка")
    summary.write(0, 0, "Регион \\ группа (по главной категории)", header)
    summary.set_column(0, 0, 30)
    for col, group in enumerate(groups, start=1):
        summary.write(0, col, group, header)
        summary.set_column(col, col, 16)
    summary.write(0, len(groups) + 1, "Всего", header)
    summary.set_row(0, 45)
    for row_index, region in enumerate(regions, start=1):
        summary.write(row_index, 0, region, number)
        for col, group in enumerate(groups, start=1):
            summary.write(row_index, col, by_region_group.get((region, group), 0), number)
        summary.write(row_index, len(groups) + 1, sum(by_region_group.get((region, g), 0) for g in groups), total)
    last = len(regions) + 1
    summary.write(last, 0, "Всего", total)
    for col, group in enumerate(groups, start=1):
        summary.write(last, col, sum(by_region_group.get((r, group), 0) for r in regions), total)
    summary.write(last, len(groups) + 1, len(incidents), total)
    summary.freeze_panes(1, 1)

    category_rows = conn.execute(
        "SELECT c.category, COUNT(*) AS n, SUM(c.is_primary) AS primary_n, "
        "SUM(COALESCE(ic.dead, 0)) AS dead, SUM(COALESCE(ic.injured, 0)) AS injured "
        "FROM incident_item_categories c JOIN incident_classifications ic ON ic.item_id = c.item_id "
        "WHERE ic.is_incident = 1 GROUP BY c.category"
    ).fetchall()
    write_table(
        workbook.add_worksheet("Категории"),
        [("Группа", 30), ("Категория", 55), ("Код", 22), ("Сообщений (любая из категорий)", 16),
         ("Из них главная категория", 16), ("Погибших (по тексту)", 14), ("Пострадавших (по тексту)", 14)],
        sorted(
            ([labels[r["category"]][1], labels[r["category"]][0], r["category"], r["n"], r["primary_n"], r["dead"], r["injured"]]
             for r in category_rows if r["category"] in labels),
            key=lambda values: (values[0], -values[3]),
        ),
    )

    by_region_category: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    region_by_id = {r["item_id"]: r["region"] for r in incidents}
    for row in conn.execute(
        "SELECT c.item_id, c.category, c.is_primary FROM incident_item_categories c "
        "JOIN incident_classifications ic ON ic.item_id = c.item_id WHERE ic.is_incident = 1"
    ):
        counts = by_region_category[(region_by_id.get(row["item_id"]), row["category"])]
        counts[0] += 1
        counts[1] += row["is_primary"]
    write_table(
        workbook.add_worksheet("Регион × категория"),
        [("Регион", 28), ("Группа", 30), ("Категория", 55), ("Сообщений", 12), ("Из них главная", 14)],
        sorted(
            ([region, labels[code][1], labels[code][0], counts[0], counts[1]]
             for (region, code), counts in by_region_category.items() if code in labels and region),
            key=lambda values: (values[0], values[1], -values[3]),
        ),
    )

    write_table(
        workbook.add_worksheet("Происшествия"),
        [("ID", 9), ("Регион", 24), ("Город", 16), ("Канал", 18), ("Тип", 12), ("Дата (местное время)", 20),
         ("Дата (UTC)", 20), ("Ссылка", 30), ("Группа", 24), ("Главная категория", 34), ("Все категории", 40),
         ("Важность", 11), ("Уверенность", 11), ("В регионе канала", 10), ("Место", 28), ("Дата события", 13),
         ("Погибших", 9), ("Пострадавших", 11), ("Краткое описание", 60), ("Текст сообщения", 80), ("Текст поста (для комментария)", 60)],
        [
            [
                r["item_id"], r["region"], r["city"], r["channel"], KIND_RU.get(r["kind"], r["kind"]),
                (r["date_local"] or "")[:19].replace("T", " "), (r["date"] or "")[:19].replace("T", " "), r["url"],
                r["primary_group"], r["primary_label"], r["all_labels"], SEVERITY_RU.get(r["severity"], r["severity"]),
                r["confidence"], {1: "да", 0: "нет"}.get(r["in_source_region"]), r["location_text"], r["event_date"],
                r["dead"], r["injured"], r["summary"], cell_text(r["text"]), cell_text(r["parent_post_text"]),
            ]
            for r in incidents
        ],
    )

    incidents_per_channel: dict[str, int] = defaultdict(int)
    for r in incidents:
        incidents_per_channel[r["channel"]] += 1
    write_table(
        workbook.add_worksheet("Каналы"),
        [("Регион", 28), ("Город", 18), ("Канал", 22), ("Название", 45), ("Ссылка", 30), ("Подписчиков", 12),
         ("Постов собрано", 12), ("Комментариев собрано", 14), ("Происшествий найдено", 14), ("Ошибка сбора", 40)],
        [
            [row["region"], row["city"], row["username"], row["title"], f"https://t.me/{row['username']}", row["members"],
             row["posts"], row["comments"], incidents_per_channel.get(row["username"], 0), row["error"]]
            for row in conn.execute(
                "SELECT ch.*, (SELECT COUNT(*) FROM items i WHERE i.channel_id = ch.id AND i.kind = 'post') AS posts, "
                "(SELECT COUNT(*) FROM items i WHERE i.channel_id = ch.id AND i.kind = 'comment') AS comments "
                "FROM channels ch ORDER BY ch.region, ch.username"
            )
        ],
    )
    workbook.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build shareable SQLite + Excel deliverables from incident results.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--out-dir", default="data/deliverable")
    args = parser.parse_args()

    conn = open_db(args.db)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    incidents = load_incidents(conn)
    build_database(conn, incidents, out_dir / "privolze_incidents.db")
    build_workbook(conn, incidents, out_dir / "privolze_incidents.xlsx")
    print(f"{len(incidents)} incidents -> {out_dir}/privolze_incidents.db, {out_dir}/privolze_incidents.xlsx")


if __name__ == "__main__":
    main()
