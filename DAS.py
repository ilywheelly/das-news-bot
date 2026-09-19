import logging
import os
import requests
from bs4 import BeautifulSoup
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from telegram.constants import ParseMode
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import random
import re
import pytz
from shloka_day import publish_from_index

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = "@t_svt4ok"
SOURCE_NAME = "Шри Чайтанья Сарасват Матх"
SOURCE_URL = "https://harekrishna.ru/"

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
        candidate = await publish_from_index(application.bot.send_message, CHANNEL_ID)
        if candidate:
            logger.info(
                f"Шлока дня опубликована: {candidate.scripture_code} "
                f"{candidate.reference} ({candidate.unique_id})"
            )
        else:
            logger.info("Шлока дня: неопубликованных записей не осталось")
    except Exception as e:
        logger.error(f"Ошибка публикации шлоки дня: {e}")

# Обработка входящих сообщений
async def forward_to_channel(update, context):
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

# Основной запуск
def main():
    global application
    scheduler = AsyncIOScheduler(timezone=pytz.timezone('Asia/Tomsk'))
    scheduler.add_job(send_alternating_article, CronTrigger(hour=11, minute=0))
    scheduler.add_job(send_alternating_article, CronTrigger(hour=17, minute=0))
    scheduler.add_job(
        send_shloka_day,
        CronTrigger(hour=19, minute=0, timezone="Asia/Tomsk"),
    )

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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_channel))

    logger.info("Бот запущен.")
    application.run_polling()

if __name__ == "__main__":
    main()
