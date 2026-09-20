"""Simple local search over the indexed shlokas."""

from __future__ import annotations

import re
from typing import Any

from shloka_day import load_index_entries

_CODE_ALIASES = {
    "bg": "BG",
    "бг": "BG",
    "sb": "SB",
    "шб": "SB",
}
_SEARCH_FIELDS = (
    "scripture_code",
    "reference",
    "translation",
    "commentary",
    "lecture_title",
    "transliteration",
)
_REFERENCE_RE = re.compile(r"^(?:(BG|SB|БГ|ШБ)\s*)?(\d+(?:\.\d+){1,2})$", re.IGNORECASE)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _reference_query(query: str) -> tuple[str | None, str | None]:
    match = _REFERENCE_RE.fullmatch(_clean(query))
    if not match:
        return None, None
    code = _CODE_ALIASES.get(match.group(1).casefold()) if match.group(1) else None
    return code, match.group(2)


def _score_entry(entry: dict[str, Any], query: str) -> int:
    code_query, reference_query = _reference_query(query)
    fields = {name: _clean(entry.get(name)) for name in _SEARCH_FIELDS}
    score = 0

    if reference_query and fields["reference"] == reference_query:
        score += 80
        if code_query and fields["scripture_code"].upper() == code_query:
            score += 100

    phrase = _clean(query)
    if phrase and phrase in fields["translation"]:
        score += 50
    if phrase and phrase in fields["commentary"]:
        score += 40
    if phrase and phrase in fields["lecture_title"]:
        score += 30

    words = phrase.split()
    for word in words:
        if any(word in fields[name] for name in _SEARCH_FIELDS):
            score += 10
    return score


def search_shlokas(query: str, limit: int = 20) -> list[dict]:
    """Return indexed entries ranked by a small deterministic relevance score."""
    if not isinstance(query, str) or not query.strip() or limit <= 0:
        return []

    code_query, reference_query = _reference_query(query)
    results = []
    for entry in load_index_entries():
        if reference_query:
            if _clean(entry.get("reference")) != reference_query:
                continue
            if code_query and _clean(entry.get("scripture_code")).upper() != code_query:
                continue
        score = _score_entry(entry, query)
        if score > 0 and entry.get("unique_id"):
            result = dict(entry)
            result["score"] = score
            results.append(result)

    results.sort(key=lambda item: (-item["score"], item.get("unique_id", "")))
    return results[:limit]
