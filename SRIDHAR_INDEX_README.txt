ИНДЕКС АРХИВА SRIDHAR.GURU — DAS

Положить в одну папку:
  build_sridhar_index.py
  sridhar_guru.py
  devanagari.json

Установить зависимости:
  python3 -m pip install requests beautifulsoup4

Сначала безопасный тест на 5 страницах:
  python3 build_sridhar_index.py --limit 5

Полный индекс:
  python3 build_sridhar_index.py

Результат:
  sridhar_verse_index.json
  sridhar_index_state.json

state-файл сохраняется после КАЖДОЙ обработанной страницы.
Если процесс прервётся, повторный запуск продолжит работу и не должен
заново обходить уже обработанные лекции.

Принудительное обновление:
  python3 build_sridhar_index.py --force

Структура индекса:
  meta
  entries[]     — каждая конкретная пара «шлока + комментарий в лекции»
  by_verse      — быстрый обратный индекс, например BG.18.65 -> entries

Индекс намеренно включает только BG и SB:
- для них есть локальная проверенная деванагари;
- бенгальские произведения пока не включаем в «Шлоку дня».

Скрипт сначала пытается получить список /posts/ через sitemap.
Если sitemap не предоставляет записи, использует страницы архива.
Все запросы идут с User-Agent bot_DAS/1.0 и паузой 1 секунда.
