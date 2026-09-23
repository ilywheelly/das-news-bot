#!/usr/bin/env python3
"""
sridhar_guru.py — модуль «Шлока дня» для DAS.

Что делает:
1) Получает страницу лекции sridhar.guru.
2) Извлекает сноски со стихами (BG / SB и др.).
3) Находит место цитирования сноски в транскрипции и ближайший таймкод.
4) Берёт законченный фрагмент комментария после цитирования.
5) Для BG/SB добавляет деванагари из локального devanagari.json.
6) Формирует Telegram HTML-пост.
7) Хранит историю публикаций, чтобы не повторять одну и ту же
   комбинацию «стих + лекция».

Важно:
- Смысловой источник, перевод и комментарий: sridhar.guru.
- Деванагари: только локальный devanagari.json.
- Для источников, не являющихся BG/SB, деванагари не добавляется.
- Модуль ничего не «додумывает» и не генерирует от имени Махараджа.
"""

from __future__ import annotations

import hashlib
import html
import json
import random
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

BASE_URL = "https://sridhar.guru"
USER_AGENT = "bot_DAS/1.0"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ru,en;q=0.8",
}

DEVANAGARI_FILE = Path(__file__).with_name("devanagari.json")
HISTORY_FILE = Path(__file__).with_name("shloka_history.json")

TIMEOUT = 30

# На первом этапе поддерживаем только те источники, для которых
# у нас есть локальная база деванагари.
SCRIPTURE_PATTERNS = [
    (
        "BG",
        re.compile(
            r"(?:Бхагавад[-‑–— ]?гита|Бхагавадгита)\s*[,.:]?\s*(\d+)\.(\d+)",
            re.I,
        ),
    ),
    (
        "SB",
        re.compile(
            r"(?:Шримад[-‑–— ]?Бхагаватам|Шримад Бхагаватам)\s*[,.:]?\s*"
            r"(\d+)\.(\d+)\.(\d+)",
            re.I,
        ),
    ),
]

FOOTNOTE_RE = re.compile(r"^\[(\d+)\]$")
SPEAKER_LABEL_RE = re.compile(r"^\s*[^:\n]{1,120}:\s*$")
SERVICE_LINE_RE = re.compile(r"^\s*\[[^\]\n]{1,160}\]\s*$")
MIN_COMMENTARY_CHARS = 100


@dataclass
class ShlokaCandidate:
    scripture_code: str          # BG / SB
    reference: str               # 18.65 / 10.14.8
    scripture_title: str         # Бхагавад-гита / Шримад-Бхагаватам
    transliteration: str
    translation: str
    devanagari: str
    lecture_title: str
    lecture_url: str
    lecture_date: str
    timestamp: str
    commentary: str
    footnote_number: int

    @property
    def unique_id(self) -> str:
        raw = f"{self.scripture_code}:{self.reference}:{self.lecture_url}:{self.footnote_number}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _get(url: str) -> requests.Response:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response


def load_devanagari(path: Path = DEVANAGARI_FILE) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Не найден {path}. Сначала запусти build_devanagari.py."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_history(path: Path = HISTORY_FILE) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("published", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_history(ids: set[str], path: Path = HISTORY_FILE) -> None:
    path.write_text(
        json.dumps({"published": sorted(ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_scripture_reference(title: str) -> Optional[tuple[str, str]]:
    clean = " ".join(title.split())
    for code, pattern in SCRIPTURE_PATTERNS:
        m = pattern.search(clean)
        if not m:
            continue
        if code == "BG":
            return code, f"{int(m.group(1))}.{int(m.group(2))}"
        return code, f"{int(m.group(1))}.{int(m.group(2))}.{int(m.group(3))}"
    return None


def split_verse_and_translation(text: str) -> tuple[str, str]:
    """
    sridhar.guru обычно оформляет сноску так:
    транслитерация — «русский перевод» (Источник...)
    """
    text = " ".join(text.split())

    # Отрезаем повторную библиографическую приписку в конце.
    text = re.sub(r"\s*\([^()]*\)\s*$", "", text).strip()

    # Предпочитаем разделитель перед русской кавычкой.
    m = re.search(r"\s+[—–-]\s+[«\"]", text)
    if m:
        translit = text[:m.start()].strip(" —–-")
        translation = text[m.end()-1:].strip()
        translation = translation.strip("«»\" ")
        return translit, translation

    # Некоторые сноски содержат только перевод.
    if text.startswith(("«", '"')):
        return "", text.strip("«»\" ")

    return text, ""


def _nearest_timestamp_before(node: Tag) -> str:
    """
    Идём назад от ссылки-сноски до ближайшего #HH:MM:SS#.
    """
    for prev in node.find_all_previous(string=True):
        s = " ".join(str(prev).split())
        m = re.search(r"#?(\d{1,2}:\d{2}(?::\d{2})?)#?", s)
        if m:
            value = m.group(1)
            if value.count(":") == 1:
                value = "00:" + value
            return value
    return ""


def _commentary_after_footnote(anchor: Tag, max_paragraphs: int = 3) -> str:
    """
    Собирает несколько следующих смысловых абзацев до следующего таймкода
    или следующей крупной цитаты. Это не пересказ: текст берётся дословно
    из транскрипции.
    """
    parts: list[str] = []
    seen = set()
    transcript = anchor.find_parent(class_="PostPage__text")
    current_verse = anchor.find_parent(class_="Article__verse-wrapper")
    if transcript is None:
        return ""

    for el in anchor.find_all_next():
        if not isinstance(el, Tag):
            continue
        if transcript not in el.parents:
            break
        if (
            "Article__verse-wrapper" in el.get("class", [])
            and el is not current_verse
        ):
            break
        if el.name == "a" and "Article__foot-link" in el.get("class", []):
            if parts:
                break
            continue

        # Остановиться на следующем заголовке сноски / справочном блоке.
        if el.name in {"h2", "h3"} and parts:
            break

        text = " ".join(el.get_text(" ", strip=True).split())
        if not text:
            continue

        # Новый таймкод — естественная граница комментария.
        if re.fullmatch(r"#?\d{1,2}:\d{2}(?::\d{2})?#?", text):
            if parts:
                break
            continue

        # Не берём сами номера сносок.
        if FOOTNOTE_RE.fullmatch(text):
            continue

        # Избегаем вложенных дублей BeautifulSoup.
        if text in seen:
            continue
        seen.add(text)

        # Предпочитаем обычные текстовые контейнеры.
        if el.name not in {"p", "div"}:
            continue

        # Справочный блок внизу страницы.
        if re.match(r"^\[\d+\]", text):
            if parts:
                break
            continue

        parts.append(text)
        if len(parts) >= max_paragraphs:
            break

    return "\n\n".join(parts).strip()


def _has_substantive_commentary(commentary: str) -> bool:
    lines = [line.strip() for line in commentary.splitlines() if line.strip()]
    while lines and (
        SPEAKER_LABEL_RE.fullmatch(lines[0])
        or SERVICE_LINE_RE.fullmatch(lines[0])
    ):
        lines.pop(0)
    meaningful = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return len(meaningful) >= MIN_COMMENTARY_CHARS


def _lecture_title(soup: BeautifulSoup) -> str:
    h = soup.find(["h2", "h1"])
    if h:
        return " ".join(h.get_text(" ", strip=True).split())
    return ""


def _lecture_date(title: str) -> str:
    m = re.search(r"\b(\d{4})\.(\d{2})\.(\d{2})\b", title)
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else ""


def extract_candidates_from_post(
    url: str,
    devanagari_db: Optional[dict] = None,
) -> list[ShlokaCandidate]:
    """
    Разбирает одну страницу лекции.
    """
    if devanagari_db is None:
        devanagari_db = load_devanagari()

    soup = BeautifulSoup(_get(url).text, "html.parser")
    title = _lecture_title(soup)
    date = _lecture_date(title)

    candidates: list[ShlokaCandidate] = []

    # Ссылки вида [4], [5] внутри транскрипции.
    for anchor in soup.find_all("a"):
        label = anchor.get_text(strip=True)
        m_num = FOOTNOTE_RE.match(label)
        if not m_num:
            continue

        number = int(m_num.group(1))
        href = anchor.get("href", "")

        # Находим цель сноски через href/id, если сайт её предоставляет.
        footnote_title = ""
        footnote_text = ""

        target = None
        if href.startswith("#"):
            target = soup.find(id=href[1:])

        if target:
            heading = target.find_next(["h3", "h4"])
            if heading:
                footnote_title = " ".join(heading.get_text(" ", strip=True).split())
                body = heading.find_next(["p", "div"])
                if body:
                    footnote_text = " ".join(body.get_text(" ", strip=True).split())

        # Fallback: ищем нижнюю ссылку [N], после которой идёт h3.
        if not footnote_title:
            matches = [
                a for a in soup.find_all("a")
                if a.get_text(strip=True) == f"[{number}]"
            ]
            for a2 in reversed(matches):
                heading = a2.find_next(["h3", "h4"])
                if heading:
                    candidate_title = " ".join(
                        heading.get_text(" ", strip=True).split()
                    )
                    if parse_scripture_reference(candidate_title):
                        footnote_title = candidate_title
                        body = heading.find_next(["p", "div"])
                        if body:
                            footnote_text = " ".join(
                                body.get_text(" ", strip=True).split()
                            )
                        break

        parsed = parse_scripture_reference(footnote_title)
        if not parsed:
            continue

        code, reference = parsed
        devanagari = (
            devanagari_db.get(code, {})
            .get(reference, {})
            .get("devanagari", "")
            .strip()
        )

        # Если BG/SB отсутствует в локальной базе, лучше пропустить,
        # чем публиковать неполную карточку.
        if not devanagari:
            continue

        translit, translation = split_verse_and_translation(footnote_text)
        timestamp = _nearest_timestamp_before(anchor)
        commentary = _commentary_after_footnote(anchor)

        if not _has_substantive_commentary(commentary):
            continue

        candidates.append(
            ShlokaCandidate(
                scripture_code=code,
                reference=reference,
                scripture_title=(
                    "Бхагавад-гита" if code == "BG"
                    else "Шримад-Бхагаватам"
                ),
                transliteration=translit,
                translation=translation,
                devanagari=devanagari,
                lecture_title=title,
                lecture_url=url,
                lecture_date=date,
                timestamp=timestamp,
                commentary=commentary,
                footnote_number=number,
            )
        )

    # Удаляем дубли.
    unique = {}
    for c in candidates:
        unique[c.unique_id] = c
    return list(unique.values())


def choose_unpublished(
    candidates: list[ShlokaCandidate],
    history_file: Path = HISTORY_FILE,
) -> Optional[ShlokaCandidate]:
    history = load_history(history_file)
    available = [c for c in candidates if c.unique_id not in history]
    return random.choice(available) if available else None


def mark_published(
    candidate: ShlokaCandidate,
    history_file: Path = HISTORY_FILE,
) -> None:
    history = load_history(history_file)
    history.add(candidate.unique_id)
    save_history(history, history_file)


def telegram_html(candidate: ShlokaCandidate) -> str:
    """
    Готовый HTML-текст для Telegram.
    Комментарий слегка ограничиваем по длине, но не перефразируем.
    """
    def esc(s: str) -> str:
        return html.escape(s or "", quote=True)

    commentary = candidate.commentary.strip()
    if len(commentary) > 1800:
        cut = commentary[:1800]
        pos = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        commentary = cut[: pos + 1] if pos > 900 else cut.rstrip() + "…"

    lines = [
        "📜 <b>Шлока из бесед Шридхара Махараджа</b>",
        "",
        f"<b>{esc(candidate.scripture_title)} {esc(candidate.reference)}</b>",
        "",
        esc(candidate.devanagari),
    ]

    if candidate.transliteration:
        lines += ["", f"<i>{esc(candidate.transliteration)}</i>"]

    if candidate.translation:
        lines += ["", "<b>Перевод</b>", esc(candidate.translation)]

    lines += [
        "",
        "<b>Комментарий Шрилы Б. Р. Шридхара Дев-Госвами Махараджа</b>",
        "",
        esc(commentary),
    ]

    meta = []
    if candidate.timestamp:
        meta.append(f"⏱ {esc(candidate.timestamp)}")
    if candidate.lecture_date:
        meta.append(f"📅 {esc(candidate.lecture_date)}")

    if meta:
        lines += ["", "   ".join(meta)]

    lines += [
        "",
        f'🎙 <a href="{esc(candidate.lecture_url)}">{esc(candidate.lecture_title)}</a>',
        "🔗 sridhar.guru",
    ]

    return "\n".join(lines)


def debug_post(url: str) -> None:
    """
    Локальный тест:
        python3 sridhar_guru.py URL
    """
    candidates = extract_candidates_from_post(url)
    print(f"Найдено подходящих санскритских шлок: {len(candidates)}")
    for i, c in enumerate(candidates, 1):
        print("\n" + "=" * 72)
        print(f"{i}. {c.scripture_title} {c.reference} / {c.timestamp}")
        print(telegram_html(c))


if __name__ == "__main__":
    import sys

    test_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "https://sridhar.guru/posts/"
             "1983-11-04-c2_skorb_o_nesposobnosti_predatsya_krishne"
    )
    debug_post(test_url)
