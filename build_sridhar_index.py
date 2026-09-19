#!/usr/bin/env python3
"""
build_sridhar_index.py
Создаёт локальный индекс архива sridhar.guru для рубрики «Шлока дня».

Выход:
  sridhar_verse_index.json
  sridhar_index_state.json

Индексируем только санскритские источники, для которых у нас есть
локальная деванагари: Bhagavad-gita (BG) и Srimad-Bhagavatam (SB).

Парсер страницы и извлечение комментария берутся из sridhar_guru.py.
Запросы открыто идентифицируются как bot_DAS/1.0.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from sridhar_guru import (
    BASE_URL,
    HEADERS,
    extract_candidates_from_post,
    load_devanagari,
)

INDEX_FILE = Path(__file__).with_name("sridhar_verse_index.json")
STATE_FILE = Path(__file__).with_name("sridhar_index_state.json")

TIMEOUT = 30
REQUEST_DELAY = 1.0
POST_RE = re.compile(r"^/posts/[a-zA-Z0-9_\-]+/?$")


def get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def same_site(url: str) -> bool:
    return urlparse(url).netloc in {"", "sridhar.guru", "www.sridhar.guru"}


def normalize_post_url(href: str) -> str | None:
    if not href:
        return None
    full = urljoin(BASE_URL, href)
    p = urlparse(full)
    if not same_site(full):
        return None
    if not POST_RE.match(p.path):
        return None
    return f"{p.scheme or 'https'}://{p.netloc or 'sridhar.guru'}{p.path.rstrip('/')}"


def extract_post_links(html_text: str) -> set[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        u = normalize_post_url(a["href"])
        if u:
            links.add(u)
    return links


def discover_from_sitemap() -> set[str]:
    """
    Пробуем стандартные sitemap endpoints. Если sitemap index ссылается
    на дочерние sitemap, читаем и их.
    """
    found = set()
    queue = [
        urljoin(BASE_URL, "/sitemap.xml"),
        urljoin(BASE_URL, "/sitemap_index.xml"),
    ]
    seen = set()

    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            r = get(url)
        except Exception:
            continue

        text = r.text
        soup = BeautifulSoup(text, "xml")
        locs = [x.get_text(strip=True) for x in soup.find_all("loc")]

        for loc in locs:
            post = normalize_post_url(loc)
            if post:
                found.add(post)
            elif "sitemap" in loc.lower() and same_site(loc):
                queue.append(loc)

    return found


def discover_from_archive(max_pages: int = 1000) -> set[str]:
    """
    Fallback: идём по внутренним страницам архива/списков и собираем /posts/.
    Не обходим весь сайт: только стартовые разделы и пагинацию.
    """
    starts = [
        urljoin(BASE_URL, "/"),
        urljoin(BASE_URL, "/posts"),
        urljoin(BASE_URL, "/verses"),
        urljoin(BASE_URL, "/topics"),
    ]
    queue = list(starts)
    seen_pages = set()
    posts = set()

    while queue and len(seen_pages) < max_pages:
        url = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)

        try:
            r = get(url)
        except Exception as e:
            print(f"[discover] skip {url}: {e}")
            continue

        posts |= extract_post_links(r.text)

        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = urljoin(BASE_URL, href)
            if not same_site(full):
                continue
            p = urlparse(full)

            # Разрешаем только навигационные страницы, а не сами posts.
            nav = (
                p.path in {"/", "/posts", "/verses", "/topics"}
                or p.path.startswith("/posts?")
                or p.path.startswith("/verses?")
                or p.path.startswith("/topics/")
                or "page=" in p.query
            )
            if nav and full not in seen_pages and full not in queue:
                queue.append(full)

        time.sleep(REQUEST_DELAY)

    return posts


def discover_posts() -> list[str]:
    posts = discover_from_sitemap()
    if posts:
        print(f"[discover] sitemap: {len(posts)} post URLs")
        return sorted(posts)

    print("[discover] sitemap не дал /posts/, использую архивные страницы")
    posts = discover_from_archive()
    print(f"[discover] archive: {len(posts)} post URLs")
    return sorted(posts)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"processed": {}, "errors": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"processed": {}, "errors": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_existing_index() -> dict:
    if not INDEX_FILE.exists():
        return {
            "meta": {},
            "entries": [],
            "by_verse": {},
        }
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"meta": {}, "entries": [], "by_verse": {}}


def rebuild_by_verse(entries: list[dict]) -> dict:
    by_verse = {}
    for i, entry in enumerate(entries):
        key = f"{entry['scripture_code']}.{entry['reference']}"
        by_verse.setdefault(key, []).append(i)
    return by_verse


def save_index(entries: list[dict], total_posts: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    index = {
        "meta": {
            "format_version": 1,
            "generated_at_utc": now,
            "source": BASE_URL,
            "user_agent": HEADERS.get("User-Agent"),
            "scope": ["BG", "SB"],
            "total_posts_discovered": total_posts,
            "total_commentary_entries": len(entries),
            "unique_verses": len({
                f"{e['scripture_code']}.{e['reference']}" for e in entries
            }),
        },
        "entries": entries,
        "by_verse": rebuild_by_verse(entries),
    }
    INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def index_archive(force: bool = False, limit: int | None = None) -> None:
    devanagari = load_devanagari()
    urls = discover_posts()
    if limit:
        urls = urls[:limit]

    state = load_state()
    old = load_existing_index()
    entries = old.get("entries", [])

    # Индексируем по unique_id, чтобы повторный запуск не создавал дубли.
    entry_map = {e["unique_id"]: e for e in entries if "unique_id" in e}

    total = len(urls)
    for n, url in enumerate(urls, 1):
        if not force and url in state.get("processed", {}):
            continue

        print(f"[{n}/{total}] {url}")
        try:
            candidates = extract_candidates_from_post(url, devanagari)
            for c in candidates:
                item = {
                    "unique_id": c.unique_id,
                    "scripture_code": c.scripture_code,
                    "reference": c.reference,
                    "scripture_title": c.scripture_title,
                    "transliteration": c.transliteration,
                    "translation": c.translation,
                    "devanagari": c.devanagari,
                    "lecture_title": c.lecture_title,
                    "lecture_url": c.lecture_url,
                    "lecture_date": c.lecture_date,
                    "timestamp": c.timestamp,
                    "commentary": c.commentary,
                    "footnote_number": c.footnote_number,
                }
                entry_map[c.unique_id] = item

            state.setdefault("processed", {})[url] = {
                "indexed_at_utc": datetime.now(timezone.utc).isoformat(),
                "entries": len(candidates),
            }
            state.setdefault("errors", {}).pop(url, None)

        except Exception as e:
            print(f"  ERROR: {e}")
            state.setdefault("errors", {})[url] = {
                "at_utc": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            }

        # Сохраняем после каждой страницы: можно безопасно прервать Ctrl+C.
        entries = list(entry_map.values())
        save_state(state)
        save_index(entries, total)
        time.sleep(REQUEST_DELAY)

    entries = list(entry_map.values())
    save_index(entries, total)

    unique = {
        f"{e['scripture_code']}.{e['reference']}"
        for e in entries
    }
    print()
    print("ГОТОВО")
    print(f"Страниц обнаружено: {total}")
    print(f"Комментариев в индексе: {len(entries)}")
    print(f"Уникальных шлок BG/SB: {len(unique)}")
    print(f"Индекс: {INDEX_FILE}")
    print(f"Состояние: {STATE_FILE}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="перепроверить уже обработанные страницы",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="тест: обработать только первые N страниц",
    )
    args = parser.parse_args()
    index_archive(force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
