import logging
import os
import json
import requests
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import random
import re
import pytz
from admin_storage import (
    add_admin,
    ensure_superadmin,
    get_admin_role,
    get_shloka_settings,
    add_schedule_slot,
    add_publication_target,
    delete_publication_target,
    delete_schedule_slot,
    get_schedule_slot,
    get_publication_target,
    init_db,
    is_admin,
    is_superadmin,
    list_schedule_slots,
    list_publication_targets,
    list_admins,
    remove_admin,
    set_setting,
    set_schedule_slot_enabled,
    set_publication_target_enabled,
    update_schedule_slot,
)
from shloka_day import choose_unpublished_from_index, entry_to_candidate, load_index_entries
from shloka_search import search_shlokas
from sridhar_guru import HISTORY_FILE, load_history, mark_published, telegram_html

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.environ["BOT_TOKEN"]
SUPERADMIN_ID = int(os.environ["SUPERADMIN_ID"])
CHANNEL_ID = "@t_svt4ok"
PUBLIC_CHANNEL = "@domik_giriraja"
SOURCE_NAME = "Шри Чайтанья Сарасват Матх"
SOURCE_URL = "https://harekrishna.ru/"


def _public_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 Случайная шлока", callback_data="public:random_shloka")],
        [InlineKeyboardButton("🔎 Найти шлоку", callback_data="public:search")],
        [InlineKeyboardButton("ℹ️ О боте", callback_data="public:about")],
        [InlineKeyboardButton("📢 Канал", callback_data="public:channel")],
    ])


def _public_home_text() -> str:
    return "🙏 <b>Светлячок</b>\n\nЦитаты и шлоки из лекций\nШрилы Б. Р. Шридхара Дев-Госвами Махараджа."


async def public_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("public_action", None)
    await update.message.reply_text(
        _public_home_text(), parse_mode="HTML", reply_markup=_public_home_keyboard()
    )


async def public_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await public_start(update, context)


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = f"Ваш Telegram ID: {user.id}"
    if user.username:
        text += f"\nUsername: @{user.username}"
    await update.message.reply_text(text)


async def public_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "Доступные команды:\n\n/start — главное меню\n/menu — главное меню\n/help — помощь\n/myid — показать ваш Telegram ID\n\nПоиск доступен через кнопку «🔎 Найти шлоку»."
    if is_admin(update.effective_user.id):
        text += "\n/admin — управление ботом"
    await update.message.reply_text(text)


def _search_keyboard(page: int, result_count: int) -> InlineKeyboardMarkup:
    start = page * 5
    end = min(start + 5, result_count)
    buttons = [
        InlineKeyboardButton(str(index + 1), callback_data=f"public:search_result:{index}")
        for index in range(start, end)
    ]
    rows = [buttons] if buttons else []
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️ Назад", callback_data="public:search_prev"))
    if end < result_count:
        navigation.append(InlineKeyboardButton("➡️ Ещё", callback_data="public:search_next"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton("⬅️ Меню", callback_data="public:home")])
    return InlineKeyboardMarkup(rows)


def _search_results_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    result_ids = context.user_data.get("public_search_results", [])
    page = context.user_data.get("public_search_page", 0)
    start = page * 5
    entries_by_id = {entry.get("unique_id"): entry for entry in load_index_entries()}
    lines = [f"Найдено: {len(result_ids)}", ""]
    for number, unique_id in enumerate(result_ids[start:start + 5], start=start + 1):
        entry = entries_by_id.get(unique_id, {})
        reference = f"{entry.get('scripture_code', '')} {entry.get('reference', '')}".strip()
        date = " ".join(str(entry.get("lecture_date", "")).split())
        title = " ".join(str(entry.get("lecture_title", "")).split())
        translation = " ".join(str(entry.get("translation", "")).split())
        if len(title) > 120:
            title = title[:117].rstrip() + "..."
        if len(translation) > 140:
            translation = translation[:137].rstrip() + "..."
        lines.append(f"{number}. {reference}")
        if date:
            lines.append(f"📅 {date}")
        if title:
            lines.append(title)
        if translation:
            lines.append(translation)
        lines.append("")
    return "\n".join(lines)


async def handle_public_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if context.user_data.get("public_action") != "awaiting_shloka_search":
        return False
    query = update.message.text.strip()
    if len(query) < 2:
        await update.message.reply_text("Запрос слишком короткий.")
        return True
    results = search_shlokas(query, limit=20)
    context.user_data["public_search_query"] = query
    context.user_data["public_search_results"] = [item["unique_id"] for item in results]
    context.user_data["public_search_page"] = 0
    if not results:
        await update.message.reply_text(
            f"По запросу «{query}» ничего не найдено.\n\nПопробуйте другое слово или номер шлоки.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔎 Новый поиск", callback_data="public:search")],
                [InlineKeyboardButton("🏠 Меню", callback_data="public:home")],
            ]),
        )
        return True
    context.user_data.pop("public_action", None)
    await update.message.reply_text(
        _search_results_text(context),
        reply_markup=_search_keyboard(0, len(results)),
    )
    return True


def _public_random_candidate():
    entries = load_index_entries()
    random.shuffle(entries)
    for entry in entries:
        try:
            return entry_to_candidate(entry)
        except (KeyError, ValueError):
            continue
    return None


async def public_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    try:
        if data == "public:home":
            context.user_data.pop("public_action", None)
            await query.edit_message_text(
                _public_home_text(), parse_mode="HTML", reply_markup=_public_home_keyboard()
            )
        elif data == "public:search":
            context.user_data["public_action"] = "awaiting_shloka_search"
            await query.edit_message_text(
                "Введите слово, тему или номер шлоки.\n\n"
                "Примеры:\nсмирение\nмилость\nГуру\nBG 10.42\nШБ 10.14.8",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Меню", callback_data="public:home")]
                ]),
            )
        elif data == "public:search_next":
            result_count = len(context.user_data.get("public_search_results", []))
            page = context.user_data.get("public_search_page", 0)
            if (page + 1) * 5 < result_count:
                context.user_data["public_search_page"] = page + 1
            await query.edit_message_text(
                _search_results_text(context),
                reply_markup=_search_keyboard(context.user_data.get("public_search_page", 0), result_count),
            )
        elif data == "public:search_prev":
            result_count = len(context.user_data.get("public_search_results", []))
            page = context.user_data.get("public_search_page", 0)
            context.user_data["public_search_page"] = max(page - 1, 0)
            await query.edit_message_text(
                _search_results_text(context),
                reply_markup=_search_keyboard(context.user_data.get("public_search_page", 0), result_count),
            )
        elif data.startswith("public:search_result:"):
            result_ids = context.user_data.get("public_search_results", [])
            try:
                result_index = int(data.rsplit(":", 1)[1])
            except ValueError:
                result_index = -1
            unique_id = result_ids[result_index] if 0 <= result_index < len(result_ids) else None
            candidate = _find_candidate(unique_id) if unique_id else None
            if candidate is None:
                await query.edit_message_text(
                    "Выбранная шлока больше не найдена.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ К результатам", callback_data="public:search_prev")],
                        [InlineKeyboardButton("🏠 Меню", callback_data="public:home")],
                    ]),
                )
                return
            await query.edit_message_text(
                telegram_html(candidate),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ К результатам", callback_data="public:search_prev")],
                    [InlineKeyboardButton("🎲 Случайная шлока", callback_data="public:random_shloka")],
                    [InlineKeyboardButton("🏠 Меню", callback_data="public:home")],
                ]),
            )
        elif data == "public:random_shloka":
            candidate = _public_random_candidate()
            if candidate is None:
                await query.edit_message_text(
                    "Не удалось найти шлоку в индексе.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Меню", callback_data="public:home")]
                    ]),
                )
                return
            await query.edit_message_text(
                telegram_html(candidate),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎲 Другая", callback_data="public:random_shloka")],
                    [InlineKeyboardButton("⬅️ Меню", callback_data="public:home")],
                ]),
            )
        elif data == "public:about":
            await query.edit_message_text(
                "«Светлячок» помогает знакомиться с наставлениями\n"
                "Шрилы Б. Р. Шридхара Дев-Госвами Махараджа.\n\n"
                "Материалы взяты из архива sridhar.guru.\n"
                "Для каждой цитаты сохраняются ссылка на лекцию,\n"
                "дата и таймкод, если они доступны.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Меню", callback_data="public:home")]
                ]),
            )
        elif data == "public:channel":
            keyboard = [[InlineKeyboardButton("⬅️ Меню", callback_data="public:home")]]
            channel_text = f"📢 Канал Домик Гирираджа: {PUBLIC_CHANNEL}"
            if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", PUBLIC_CHANNEL):
                keyboard.insert(0, [InlineKeyboardButton("📢 Открыть канал", url=f"https://t.me/{PUBLIC_CHANNEL[1:]}")])
            await query.edit_message_text(channel_text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        logger.exception("Ошибка callback публичной панели: %s", data)
        await query.edit_message_text("Произошла ошибка. Попробуйте ещё раз.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Меню", callback_data="public:home")]
        ]))

# Проверка на кириллицу
def is_russian(text):
    cyrillic = len(re.findall(r'[а-яА-Я]', text))
    return len(text) > 0 and (cyrillic / len(text)) > 0.6

# Парсинг статьи с harekrishna.ru
def get_full_article(link):
    headers = {"User-Agent": "bot_DAS/1.0"}
    try:
        response = requests.get(link, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            paragraphs = soup.find_all("p")
            best = []
            for para in paragraphs:
                text = para.get_text(separator=' ', strip=True)
                if text and 80 < len(text) < 1000 and is_russian(text):
                    best.append(text)
                if len(best) >= 2:
                    break
            description = "\n\n".join(best) if best else "🔍 Загрузка текста"
            for img_tag in soup.find_all("img"):
                src = img_tag.get("src")
                if not src:
                    continue
                if not src.startswith("http"):
                    src = "https://harekrishna.ru/" + src.lstrip("/")
                if any(x in src.lower() for x in ["logo", "1x1", "pixel", ".gif", ".svg"]):
                    continue
                try:
                    img_response = requests.get(src, headers=headers, timeout=5)
                    if img_response.status_code == 200 and len(img_response.content) > 5000:
                        logger.info(f"✅ Картинка найдена: {src}")
                        return description, src
                except Exception as e:
                    logger.warning(f"Ошибка при загрузке картинки {src}: {e}")
            return description, None
        else:
            logger.error(f"Ошибка загрузки HTML: {response.status_code}")
    except Exception as e:
        logger.error(f"Ошибка при получении статьи: {e}")
    return "Ошибка при получении статьи.", None

# Парсинг sridharmaharaj.ru
def get_sridhar_article():
    url = "https://sridharmaharaj.ru/feed/"
    headers = {"User-Agent": "bot_DAS/1.0"}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "xml")
            items = soup.find_all("item")
            if items:
                item = random.choice(items)
                title = item.find("title").text.strip()
                link = item.find("link").text.strip()
                description = item.find("description").text.strip()
                return title, description, link, None  # Без картинки
    except Exception as e:
        logger.error(f"Ошибка при получении статьи с sridharmaharaj.ru: {e}")
    return "Статья", "🔍 Загрузка текста", "https://sridharmaharaj.ru/", None

# Чередование источников
source_toggle = True
manual_publish_lock = asyncio.Lock()

async def send_alternating_article():
    global source_toggle
    if source_toggle:
        url = "https://harekrishna.ru/rss/read/"
        headers = {"User-Agent": "bot_DAS/1.0"}
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, "xml")
                items = soup.find_all("item")
                if items:
                    item = random.choice(items)
                    title = item.find("title").text.split('|')[0].strip()
                    link = item.find("link").text
                    description, image_url = get_full_article(link)
                    caption = f"""📜 *{title}*

🖋️ {description}

[📖 Читать статью]({link})

_Источник: [{SOURCE_NAME}]({SOURCE_URL})_"""
                    if image_url:
                        await application.bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=caption[:1024], parse_mode=ParseMode.MARKDOWN)
                    else:
                        await application.bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode=ParseMode.MARKDOWN)
                    logger.info(f"Автостатья отправлена: {title}")
        except Exception as e:
            logger.error(f"Ошибка авторассылки: {e}")
    else:
        title, description, link, image_url = get_sridhar_article()
        caption = f"""📜 *{title}*

🖋️ {description}

[📖 Читать статью]({link})

_Источник: [sridharmaharaj.ru](https://sridharmaharaj.ru/)_"""
        await application.bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode=ParseMode.MARKDOWN)
        logger.info(f"Автостатья (sridharmaharaj.ru) отправлена: {title}")

    source_toggle = not source_toggle


async def send_shloka_day():
    try:
        settings = get_shloka_settings()
        if settings["shloka_enabled"].lower() != "true":
            logger.info("Шлока дня отключена в настройках")
            return
        targets = [target for target in list_publication_targets() if target["enabled"]]
        if not targets:
            logger.info("Нет активных точек публикации")
            return
        candidate = choose_unpublished_from_index()
        if candidate is None:
            logger.info("Шлока дня: неопубликованных записей не осталось")
            return

        text = telegram_html(candidate)
        failed_targets = []
        for target in targets:
            try:
                await application.bot.send_message(
                    chat_id=target["chat_id"],
                    text=text,
                    parse_mode="HTML",
                )
                logger.info(
                    "Шлока отправлена в %s (%s)",
                    target["title"],
                    target["chat_id"],
                )
            except Exception as error:
                failed_targets.append(target)
                logger.error(
                    "Ошибка отправки шлоки в %s (%s): %s",
                    target["title"],
                    target["chat_id"],
                    error,
                )

        if failed_targets:
            logger.error(
                "Шлока не отмечена в истории: не удалось отправить в %s",
                ", ".join(target["chat_id"] for target in failed_targets),
            )
            return

        mark_published(candidate)
        logger.info("Шлока дня отмечена в истории: %s", candidate.unique_id)
        if candidate:
            logger.info(
                f"Шлока дня опубликована: {candidate.scripture_code} "
                f"{candidate.reference} ({candidate.unique_id})"
            )
    except Exception as e:
        logger.error(f"Ошибка публикации шлоки дня: {e}")


def _admin_keyboard(role: str, settings: dict[str, str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📜 Шлока", callback_data="admin:shloka")],
        [InlineKeyboardButton("📊 История", callback_data="admin:history")],
    ]
    if role == "superadmin":
        enabled = settings["shloka_enabled"].lower() == "true"
        rows = [
            [InlineKeyboardButton("📜 Шлока", callback_data="admin:shloka")],
            [InlineKeyboardButton("🕐 Расписание", callback_data="admin:schedule")],
            [InlineKeyboardButton("📊 История", callback_data="admin:history")],
            [InlineKeyboardButton("⏸ Автопубликация" if enabled else "▶️ Автопубликация", callback_data="admin:toggle_auto")],
            [InlineKeyboardButton("📡 Каналы публикации", callback_data="admin:targets")],
            [InlineKeyboardButton("👥 Администраторы", callback_data="admin:admins")],
        ]
    return InlineKeyboardMarkup(rows)


def _admin_home_text(role: str, settings: dict[str, str]) -> str:
    text = f"⚙️ <b>Управление Светлячком</b>\n\nРоль: {'Super Admin' if role == 'superadmin' else 'Admin'}"
    if role == "superadmin":
        enabled = "🟢" if settings["shloka_enabled"].lower() == "true" else "🔴"
        slots = list_schedule_slots()
        schedule = ", ".join(
            f"{int(slot['hour']):02d}:{int(slot['minute']):02d}"
            for slot in slots
        ) or "нет слотов"
        text += f"\nАвтопубликация: {enabled}\nШлока дня: {schedule}\nЧасовой пояс: {settings['timezone']}"
    return text


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin:home")]])


def _find_candidate(unique_id: str):
    for entry in load_index_entries():
        if entry.get("unique_id") == unique_id:
            return entry_to_candidate(entry)
    return None


async def _publish_candidate(candidate, send_message):
    if candidate is None:
        return None
    await send_message(chat_id=CHANNEL_ID, text=telegram_html(candidate), parse_mode="HTML")
    mark_published(candidate)
    return candidate


async def _show_admin_home(query, role: str) -> None:
    settings = get_shloka_settings()
    await query.edit_message_text(_admin_home_text(role, settings), parse_mode="HTML", reply_markup=_admin_keyboard(role, settings))


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    role = get_admin_role(update.effective_user.id)
    if role is None:
        await update.message.reply_text("Доступ запрещён.")
        return
    context.user_data.pop("admin_action", None)
    settings = get_shloka_settings()
    await update.message.reply_text(_admin_home_text(role, settings), parse_mode="HTML", reply_markup=_admin_keyboard(role, settings))


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    role = get_admin_role(query.from_user.id)
    if role is None:
        await query.edit_message_text("Доступ запрещён.")
        return
    data = query.data or ""
    try:
        if data == "admin:home":
            context.user_data.pop("admin_action", None)
            await _show_admin_home(query, role)
        elif data == "admin:shloka":
            await query.edit_message_text("Выберите действие:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎲 Случайная шлока", callback_data="admin:shloka_random")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="admin:home")],
            ]))
        elif data == "admin:shloka_random":
            candidate = choose_unpublished_from_index()
            if candidate is None:
                await query.edit_message_text("Нет неопубликованных шлок.", reply_markup=_back_keyboard())
                return
            context.user_data["admin_shloka_id"] = candidate.unique_id
            await query.edit_message_text(telegram_html(candidate), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Опубликовать", callback_data="admin:shloka_publish")],
                [InlineKeyboardButton("🎲 Другая", callback_data="admin:shloka_random")],
                [InlineKeyboardButton("❌ Отмена", callback_data="admin:shloka")],
            ]))
        elif data == "admin:shloka_publish":
            async with manual_publish_lock:
                unique_id = context.user_data.get("admin_shloka_id")
                candidate = _find_candidate(unique_id) if unique_id else None
                if candidate is None:
                    await query.edit_message_text("Выбранная шлока уже опубликована или не найдена.", reply_markup=_back_keyboard())
                    return
                if candidate.unique_id in load_history():
                    context.user_data.pop("admin_shloka_id", None)
                    await query.edit_message_text("Эта шлока уже опубликована.", reply_markup=_back_keyboard())
                    return
                await _publish_candidate(candidate, context.bot.send_message)
                context.user_data.pop("admin_shloka_id", None)
                await query.edit_message_text("Шлока опубликована.", reply_markup=_back_keyboard())
        elif data == "admin:history":
            history_data = {"published": []}
            try:
                history_data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            ids = history_data.get("published", [])[-10:]
            details = []
            for item in ids:
                candidate = _find_candidate(item)
                suffix = f" — {candidate.scripture_code} {candidate.reference}" if candidate else ""
                details.append(f"• {item}{suffix}")
            text = "📊 <b>Последние 10 записей</b>\n\n" + "\n".join(details) if details else "📊 <b>История</b>\n\nИстория пуста."
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=_back_keyboard())
        elif data == "admin:toggle_auto":
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            settings = get_shloka_settings()
            set_setting("shloka_enabled", "false" if settings["shloka_enabled"].lower() == "true" else "true")
            await _show_admin_home(query, role)
        elif data == "admin:targets":
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            await _show_publication_targets(query)
        elif data == "admin:target_add":
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            context.user_data["admin_action"] = "awaiting_target_chat_id"
            await query.edit_message_text("Отправьте chat_id или @username точки публикации.", reply_markup=_back_keyboard())
        elif data.startswith("admin:target:"):
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            target_id = int(data.rsplit(":", 1)[1])
            target = get_publication_target(target_id)
            if target is None:
                await query.edit_message_text("Точка публикации не найдена.", reply_markup=_back_keyboard())
                return
            await _show_publication_target(query, target)
        elif data.startswith("admin:target_toggle:"):
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            target_id = int(data.rsplit(":", 1)[1])
            target = get_publication_target(target_id)
            if target is None:
                await query.edit_message_text("Точка публикации не найдена.", reply_markup=_back_keyboard())
                return
            set_publication_target_enabled(target_id, not bool(target["enabled"]))
            await _show_publication_target(query, get_publication_target(target_id))
        elif data.startswith("admin:target_delete:"):
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            target_id = int(data.rsplit(":", 1)[1])
            target = get_publication_target(target_id)
            if target is None:
                await query.edit_message_text("Точка публикации не найдена.", reply_markup=_back_keyboard())
                return
            if target["chat_id"] in {CHANNEL_ID, PUBLIC_CHANNEL}:
                await query.edit_message_text("Seed-точки публикации удалять нельзя. Их можно выключить.", reply_markup=_back_keyboard())
                return
            delete_publication_target(target_id)
            await _show_publication_targets(query)
        elif data == "admin:schedule":
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            await _show_schedule(query)
        elif data == "admin:schedule_add":
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            context.user_data["schedule_add_hour"] = 8
            context.user_data["schedule_add_minute"] = 0
            await _show_schedule_add(query, context)
        elif data.startswith("admin:schedule_add_"):
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            hour = context.user_data.get("schedule_add_hour")
            minute = context.user_data.get("schedule_add_minute")
            if hour is None or minute is None:
                await _show_schedule(query)
                return
            if data == "admin:schedule_add_save":
                slot_id = add_schedule_slot(hour, minute)
                if slot_id is None:
                    await query.edit_message_text("Такое время уже есть.", reply_markup=_schedule_add_keyboard())
                    return
                _rebuild_shloka_jobs()
                context.user_data.pop("schedule_add_hour", None)
                context.user_data.pop("schedule_add_minute", None)
                await _show_schedule(query)
                return
            delta = {
                "admin:schedule_add_h_plus": 60,
                "admin:schedule_add_h_minus": -60,
                "admin:schedule_add_m_plus": 10,
                "admin:schedule_add_m_minus": -10,
            }.get(data)
            if delta is None:
                await query.edit_message_text("Действие устарело.", reply_markup=_back_keyboard())
                return
            total = (hour * 60 + minute + delta) % (24 * 60)
            context.user_data["schedule_add_hour"], context.user_data["schedule_add_minute"] = divmod(total, 60)
            await _show_schedule_add(query, context)
        elif data.startswith("admin:schedule_slot:"):
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            slot_id = int(data.rsplit(":", 1)[1])
            slot = get_schedule_slot(slot_id)
            if slot is None:
                await query.edit_message_text("Слот расписания не найден.", reply_markup=_back_keyboard())
                return
            context.user_data["schedule_slot_hour"] = slot["hour"]
            context.user_data["schedule_slot_minute"] = slot["minute"]
            await _show_schedule_slot(query, context, slot)
        elif data.startswith("admin:schedule_slot_"):
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            slot_id = int(data.rsplit(":", 1)[1])
            slot = get_schedule_slot(slot_id)
            if slot is None:
                await query.edit_message_text("Слот расписания не найден.", reply_markup=_back_keyboard())
                return
            if data == f"admin:schedule_slot_delete:{slot_id}":
                await query.edit_message_text(
                    f"Удалить время {slot['hour']:02d}:{slot['minute']:02d}?",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Удалить", callback_data=f"admin:schedule_slot_delete_confirm:{slot_id}")],
                        [InlineKeyboardButton("❌ Отмена", callback_data=f"admin:schedule_slot:{slot_id}")],
                    ]),
                )
                return
            if data == f"admin:schedule_slot_delete_confirm:{slot_id}":
                delete_schedule_slot(slot_id)
                _rebuild_shloka_jobs()
                await _show_schedule(query)
                return
            if data == f"admin:schedule_slot_toggle:{slot_id}":
                set_schedule_slot_enabled(slot_id, not bool(slot["enabled"]))
                _rebuild_shloka_jobs()
                slot = get_schedule_slot(slot_id)
                await _show_schedule_slot(query, context, slot)
                return
            hour = context.user_data.get("schedule_slot_hour")
            minute = context.user_data.get("schedule_slot_minute")
            if hour is None or minute is None:
                await _show_schedule_slot(query, context, slot)
                return
            if data == f"admin:schedule_slot_save:{slot_id}":
                if not update_schedule_slot(slot_id, hour, minute):
                    await query.edit_message_text("Такое время уже есть.", reply_markup=_schedule_slot_keyboard(slot_id, slot["enabled"]))
                    return
                _rebuild_shloka_jobs()
                slot = get_schedule_slot(slot_id)
                await _show_schedule(query)
                return
            delta = {
                f"admin:schedule_slot_h_plus:{slot_id}": 60,
                f"admin:schedule_slot_h_minus:{slot_id}": -60,
                f"admin:schedule_slot_m_plus:{slot_id}": 10,
                f"admin:schedule_slot_m_minus:{slot_id}": -10,
            }.get(data)
            if delta is None:
                await query.edit_message_text("Действие устарело.", reply_markup=_back_keyboard())
                return
            total = (hour * 60 + minute + delta) % (24 * 60)
            context.user_data["schedule_slot_hour"], context.user_data["schedule_slot_minute"] = divmod(total, 60)
            await _show_schedule_slot(query, context, slot)
        elif data == "admin:admins":
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            await _show_admins(query)
        elif data == "admin:add_admin":
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            context.user_data["admin_action"] = "awaiting_new_admin_id"
            await query.edit_message_text("Отправьте Telegram ID нового администратора.", reply_markup=_back_keyboard())
        elif data == "admin:remove_admin":
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            buttons = [[InlineKeyboardButton(str(item["telegram_id"]), callback_data=f"admin:remove_confirm:{item['telegram_id']}")] for item in list_admins() if item["role"] == "admin"]
            buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin:admins")])
            await query.edit_message_text("Выберите администратора:", reply_markup=InlineKeyboardMarkup(buttons))
        elif data.startswith("admin:remove_confirm:"):
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            target = data.rsplit(":", 1)[1]
            if not target.isdigit() or get_admin_role(int(target)) != "admin":
                await query.edit_message_text("Администратор уже отсутствует.", reply_markup=_back_keyboard())
                return
            await query.edit_message_text(f"Удалить администратора {target}?", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Удалить", callback_data=f"admin:remove_yes:{target}")],
                [InlineKeyboardButton("❌ Отмена", callback_data="admin:admins")],
            ]))
        elif data.startswith("admin:remove_yes:"):
            if role != "superadmin":
                await query.edit_message_text("Недостаточно прав.", reply_markup=_back_keyboard())
                return
            target = data.rsplit(":", 1)[1]
            removed = target.isdigit() and remove_admin(int(target))
            await query.edit_message_text(f"Администратор {target} удалён." if removed else "Администратор уже отсутствует.", reply_markup=_back_keyboard())
    except Exception:
        logger.exception("Ошибка callback админ-панели: %s", data)
        await query.edit_message_text("Произошла ошибка. Попробуйте ещё раз.", reply_markup=_back_keyboard())


def _schedule_add_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("-1 час", callback_data="admin:schedule_add_h_minus"), InlineKeyboardButton("+1 час", callback_data="admin:schedule_add_h_plus")],
        [InlineKeyboardButton("-10 мин", callback_data="admin:schedule_add_m_minus"), InlineKeyboardButton("+10 мин", callback_data="admin:schedule_add_m_plus")],
        [InlineKeyboardButton("✅ Добавить", callback_data="admin:schedule_add_save")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin:schedule")],
    ])


def _schedule_slot_keyboard(slot_id: int, enabled: int) -> InlineKeyboardMarkup:
    status_button = "⏸ Выключить" if enabled else "▶️ Включить"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("-1 час", callback_data=f"admin:schedule_slot_h_minus:{slot_id}"), InlineKeyboardButton("+1 час", callback_data=f"admin:schedule_slot_h_plus:{slot_id}")],
        [InlineKeyboardButton("-10 мин", callback_data=f"admin:schedule_slot_m_minus:{slot_id}"), InlineKeyboardButton("+10 мин", callback_data=f"admin:schedule_slot_m_plus:{slot_id}")],
        [InlineKeyboardButton("✅ Сохранить", callback_data=f"admin:schedule_slot_save:{slot_id}")],
        [InlineKeyboardButton(status_button, callback_data=f"admin:schedule_slot_toggle:{slot_id}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"admin:schedule_slot_delete:{slot_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin:schedule")],
    ])


async def _show_schedule(query) -> None:
    slots = list_schedule_slots()
    rows = [[InlineKeyboardButton("➕ Добавить время", callback_data="admin:schedule_add")]]
    lines = ["🕐 <b>Расписание шлок</b>", ""]
    for slot in slots:
        status = "🟢" if slot["enabled"] else "🔴"
        time_text = f"{slot['hour']:02d}:{slot['minute']:02d}"
        lines.append(f"{status} {time_text}")
        rows.append([InlineKeyboardButton(f"⚙️ {time_text}", callback_data=f"admin:schedule_slot:{slot['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin:home")])
    await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


async def _show_schedule_add(query, context) -> None:
    hour = context.user_data["schedule_add_hour"]
    minute = context.user_data["schedule_add_minute"]
    await query.edit_message_text(
        f"Время: {hour:02d}:{minute:02d}",
        reply_markup=_schedule_add_keyboard(),
    )


async def _show_schedule_slot(query, context, slot: dict) -> None:
    hour = context.user_data.get("schedule_slot_hour", slot["hour"])
    minute = context.user_data.get("schedule_slot_minute", slot["minute"])
    status = "🟢 Включено" if slot["enabled"] else "🔴 Выключено"
    await query.edit_message_text(
        f"Время: {hour:02d}:{minute:02d}\nСтатус: {status}",
        reply_markup=_schedule_slot_keyboard(slot["id"], slot["enabled"]),
    )


async def _show_publication_targets(query) -> None:
    targets = list_publication_targets()
    lines = ["📡 <b>Точки публикации</b>", ""]
    rows = []
    for target in targets:
        status = "🟢" if target["enabled"] else "🔴"
        lines.extend([f"{status} {target['title']}", target["chat_id"], ""])
        rows.append([
            InlineKeyboardButton(
                f"⚙️ {target['title']}",
                callback_data=f"admin:target:{target['id']}",
            )
        ])
    rows.extend([
        [InlineKeyboardButton("➕ Добавить", callback_data="admin:target_add")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin:home")],
    ])
    await query.edit_message_text(
        "\n".join(lines).rstrip(),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _show_publication_target(query, target: dict) -> None:
    status = "🟢 Включён" if target["enabled"] else "🔴 Выключен"
    toggle_text = "⏸ Выключить" if target["enabled"] else "▶️ Включить"
    rows = [[InlineKeyboardButton(
        toggle_text,
        callback_data=f"admin:target_toggle:{target['id']}",
    )]]
    if target["chat_id"] not in {CHANNEL_ID, PUBLIC_CHANNEL}:
        rows.append([InlineKeyboardButton(
            "🗑 Удалить",
            callback_data=f"admin:target_delete:{target['id']}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin:targets")])
    await query.edit_message_text(
        f"📡 <b>{target['title']}</b>\n{target['chat_id']}\n\n"
        f"Тип: {target['target_type']}\nСтатус: {status}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _show_admins(query) -> None:
    lines = [f"{item['telegram_id']} — {item['role']}" for item in list_admins()]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить Admin", callback_data="admin:add_admin")],
        [InlineKeyboardButton("➖ Удалить Admin", callback_data="admin:remove_admin")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin:home")],
    ])
    await query.edit_message_text("👥 <b>Администраторы</b>\n\n" + "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    action = context.user_data.get("admin_action")
    if action in {
        "awaiting_target_chat_id",
        "awaiting_target_title",
        "awaiting_target_type",
    }:
        user_id = update.effective_user.id
        if not is_superadmin(user_id):
            context.user_data.pop("admin_action", None)
            return False
        value = update.message.text.strip()
        if action == "awaiting_target_chat_id":
            if not value:
                await update.message.reply_text("Chat ID или @username не может быть пустым.")
                return True
            context.user_data["target_chat_id"] = value
            context.user_data["admin_action"] = "awaiting_target_title"
            await update.message.reply_text("Отправьте название точки публикации.")
            return True
        if action == "awaiting_target_title":
            if not value:
                await update.message.reply_text("Название не может быть пустым.")
                return True
            context.user_data["target_title"] = value
            context.user_data["admin_action"] = "awaiting_target_type"
            await update.message.reply_text("Укажите тип: channel или group.")
            return True
        if value.lower() not in {"channel", "group"}:
            await update.message.reply_text("Тип должен быть channel или group.")
            return True
        target_id = add_publication_target(
            context.user_data.pop("target_chat_id"),
            context.user_data.pop("target_title"),
            value.lower(),
        )
        context.user_data.pop("admin_action", None)
        if target_id is None:
            await update.message.reply_text("Такая точка публикации уже существует.")
        else:
            await update.message.reply_text("Точка публикации добавлена.")
        return True

    if action != "awaiting_new_admin_id":
        return False
    user_id = update.effective_user.id
    if not is_superadmin(user_id):
        context.user_data.pop("admin_action", None)
        return False
    value = update.message.text.strip()
    context.user_data.pop("admin_action", None)
    if not value.isdigit() or int(value) <= 0:
        await update.message.reply_text("Нужен положительный Telegram ID из цифр.")
    elif add_admin(int(value), user_id):
        await update.message.reply_text(f"Администратор {value} добавлен.")
    else:
        await update.message.reply_text("Этот Telegram ID уже есть в списке.")
    return True

# Обработка входящих сообщений
async def forward_to_channel(update, context):
    if await handle_admin_text(update, context):
        return
    if await handle_public_search(update, context):
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Используйте /menu для выбора действия.")
        return
    user_message = update.message.text.strip()
    logger.info(f"Сообщение от {update.effective_user.username}: {user_message}")
    try:
        if user_message.startswith("https://harekrishna.ru/"):
            description, image_url = get_full_article(user_message)
            caption = f"""📜 *Новая статья*

🖋️ {description}

[📖 Читать статью]({user_message})

_Источник: [{SOURCE_NAME}]({SOURCE_URL})_"""
            if image_url:
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=caption[:1024], parse_mode=ParseMode.MARKDOWN)
            else:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode=ParseMode.MARKDOWN)
        elif user_message.startswith("https://sridharmaharaj.ru/"):
            title, description, link, image_url = get_sridhar_article()
            caption = f"""📜 *{title}*

🖋️ {description}

[📖 Читать статью]({link})

_Источник: [sridharmaharaj.ru](https://sridharmaharaj.ru/)_"""
            await context.bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"{user_message}\n\n<i>Источник: <a href=\"{SOURCE_URL}\">{SOURCE_NAME}</a></i>",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Ошибка при пересылке сообщения: {e}")

def _rebuild_shloka_jobs() -> None:
    settings = get_shloka_settings()
    for job in scheduler.get_jobs():
        if job.id == "daily_shloka" or job.id.startswith("daily_shloka_"):
            scheduler.remove_job(job.id)
    for slot in list_schedule_slots():
        if not slot["enabled"]:
            continue
        scheduler.add_job(
            send_shloka_day,
            CronTrigger(
                hour=slot["hour"],
                minute=slot["minute"],
                timezone=settings["timezone"],
            ),
            id=f"daily_shloka_{slot['id']}",
            replace_existing=True,
        )


# Основной запуск
def main():
    global application, scheduler
    init_db()
    ensure_superadmin(SUPERADMIN_ID)
    settings = get_shloka_settings()
    scheduler = AsyncIOScheduler(timezone=pytz.timezone(settings["timezone"]))
    scheduler.add_job(send_alternating_article, CronTrigger(hour=11, minute=0))
    scheduler.add_job(send_alternating_article, CronTrigger(hour=17, minute=0))
    _rebuild_shloka_jobs()

    async def post_init(_application):
        scheduler.start()

    async def post_shutdown(_application):
        if scheduler.running:
            scheduler.shutdown(wait=False)

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", public_start))
    application.add_handler(CommandHandler("menu", public_menu))
    application.add_handler(CommandHandler("help", public_help))
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(public_callback, pattern=r"^public:"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_channel))

    logger.info("Бот запущен.")
    application.run_polling()

if __name__ == "__main__":
    main()
