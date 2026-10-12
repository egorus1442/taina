#!/usr/bin/env python3
"""Connect the project's Telegram channel list to the running backend."""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHANNELS_FILE = PROJECT_ROOT / "telegram_channels.txt"
REGION = "Удмуртская Республика"
CITY_OVERRIDES = {
    "gaisarapul18": "Сарапул",
    "sarapulmuseum": "Сарапул",
    "glazovnew": "Глазов",
    "glazovopen": "Глазов",
    "gorodglazov": "Глазов",
    "zloyglazov": "Глазов",
    "mozhga_search": "Можга",
    "votkinsk_18": "Воткинск",
}
USERNAME = re.compile(r"^[A-Za-z0-9_]+$")


def load_channels(path: Path) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        value = value.removeprefix("https://").removeprefix("http://")
        value = value.removeprefix("www.").removeprefix("t.me/").lstrip("@")
        if not USERNAME.fullmatch(value):
            raise ValueError(f"{path}:{line_number}: invalid channel: {line!r}")
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        groups[CITY_OVERRIDES.get(key, "Ижевск")].append(f"@{value}")
    if not seen:
        raise ValueError(f"No channels found in {path}")
    return groups


def connect(api_base: str, city: str, channels: list[str]) -> dict:
    body = json.dumps(
        {"channels": channels, "city": city, "region": REGION},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"{api_base.rstrip('/')}/telegram/bulk-connect",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=300) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=CHANNELS_FILE)
    parser.add_argument("--api", default="http://127.0.0.1:8000/api")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        groups = load_channels(args.file)
        total = sum(map(len, groups.values()))
        print(f"Channels: {total}")
        for city, channels in groups.items():
            print(f"  {city}: {len(channels)}")
        if args.dry_run:
            return 0

        connected = updated = failed = 0
        for city, channels in groups.items():
            result = connect(args.api, city, channels)
            connected += result["connected"]
            updated += result["updated"]
            failed += result["failed"]
            for item in result["items"]:
                if item["status"] == "failed":
                    print(f"  FAILED {item['channel']}: {item.get('detail') or 'unknown error'}")
        print(f"Connected: {connected}, updated: {updated}, failed: {failed}")
        return 1 if failed else 0
    except (OSError, ValueError, KeyError, HTTPError, URLError) as exc:
        if isinstance(exc, HTTPError):
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"API error {exc.code}: {detail}", file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
