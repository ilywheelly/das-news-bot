#!/usr/bin/env python3
"""Локальный тест выбора «Шлоки дня» без Telegram и сетевых запросов."""

from shloka_day import choose_unpublished_from_index
from sridhar_guru import telegram_html


def main() -> None:
    candidate = choose_unpublished_from_index()
    if candidate is None:
        raise SystemExit("Нет неопубликованных записей в локальном индексе")

    output = telegram_html(candidate)
    assert candidate.unique_id
    assert output.startswith("📜 <b>Шлока из бесед Шридхара Махараджа</b>")
    print(f"Выбрано: {candidate.unique_id} ({candidate.scripture_code} {candidate.reference})")
    print("\n" + output)


if __name__ == "__main__":
    main()
