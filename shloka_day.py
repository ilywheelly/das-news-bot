#!/usr/bin/env python3
"""Выбор и публикация «Шлоки дня» из локального индекса."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from sridhar_guru import (
    HISTORY_FILE,
    ShlokaCandidate,
    load_history,
    mark_published,
    telegram_html,
)

INDEX_FILE = Path(__file__).with_name("sridhar_verse_index.json")


def load_index_entries(path: Path = INDEX_FILE) -> list[dict[str, Any]]:
    """Загружает entries без обращения к sridhar.guru."""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"Некорректный индекс: отсутствует список entries в {path}")
    return entries


def entry_to_candidate(entry: dict[str, Any]) -> ShlokaCandidate:
    candidate = ShlokaCandidate(
        scripture_code=entry["scripture_code"],
        reference=entry["reference"],
        scripture_title=entry["scripture_title"],
        transliteration=entry.get("transliteration", ""),
        translation=entry.get("translation", ""),
        devanagari=entry["devanagari"],
        lecture_title=entry["lecture_title"],
        lecture_url=entry["lecture_url"],
        lecture_date=entry.get("lecture_date", ""),
        timestamp=entry.get("timestamp", ""),
        commentary=entry["commentary"],
        footnote_number=entry["footnote_number"],
    )
    if entry.get("unique_id") != candidate.unique_id:
        raise ValueError(f"unique_id не совпадает с данными записи: {candidate.unique_id}")
    return candidate


def choose_unpublished_from_index(
    index_file: Path = INDEX_FILE,
    history_file: Path = HISTORY_FILE,
) -> Optional[ShlokaCandidate]:
    """Случайно выбирает запись, отсутствующую в локальной истории."""
    history = load_history(history_file)
    candidates = [
        entry_to_candidate(entry)
        for entry in load_index_entries(index_file)
        if entry.get("unique_id") not in history
    ]
    return random.choice(candidates) if candidates else None


async def publish_from_index(
    send_message: Callable[..., Awaitable[Any]],
    chat_id: str,
    index_file: Path = INDEX_FILE,
    history_file: Path = HISTORY_FILE,
) -> Optional[ShlokaCandidate]:
    """Отправляет выбранную запись и отмечает её после успешной отправки."""
    candidate = choose_unpublished_from_index(index_file, history_file)
    if candidate is None:
        return None

    await send_message(
        chat_id=chat_id,
        text=telegram_html(candidate),
        parse_mode="HTML",
    )
    mark_published(candidate, history_file)
    return candidate
