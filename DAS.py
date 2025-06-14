import logging
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

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "8019538415:AAHSknxd3-GZ_YNDtnG2sNG-YPqtDsqxJz0"
CHANNEL_ID = "@t_svt4ok"
SOURCE_NAME = "Шри Чайтанья Сарасват Матх"
SOURCE_URL = "https://harekrishna.ru/"

# Проверка на кириллицу
def is_russian(text):
    cyrillic = len(re.findall(r'[а-яА-Я]', text))
    return len(text) > 0 and (cyrillic / len(text)) > 0.6

# Парсинг статьи
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

# Обработка входящих сообщений
async def forward_to_channel(update, context):
    user_message = update.message.text
    logger.info(f"Сообщение от {update.effective_user.username}: {user_message}")
    try:
        if user_message.startswith("https://harekrishna.ru/"):
            description, image_url = get_full_article(user_message)

            # Попытка получить заголовок со страницы
            headers = {"User-Agent": "bot_DAS/1.0"}
            try:
                html = requests.get(user_message, headers=headers).text
                soup = BeautifulSoup(html, "html.parser")
                raw_title = soup.title.string if soup.title else ""
                clean_title = raw_title.split('|')[0].strip()
                if not clean_title or len(clean_title) < 10 or "новая статья" in clean_title.lower():
                    PLACEHOLDER_TITLES = [
                        "📜 Углублённое наставление",
                        "🪔 Созерцание сути",
                        "🎙️ Слова вайшнава",
                        "🧭 Вдохновение пути",
                        "🌿 Откровение преданности",
                        "📖 Чтение и размышление"
                    ]
                    title = random.choice(PLACEHOLDER_TITLES)
                else:
                    title = clean_title
            except Exception as e:
                logger.warning(f"Ошибка при получении заголовка: {e}")
                PLACEHOLDER_TITLES = [
                    "📜 Углублённое наставление",
                    "🪔 Созерцание сути",
                    "🎙️ Слова вайшнава",
                    "🧭 Вдохновение пути",
                    "🌿 Откровение преданности",
                    "📖 Чтение и размышление"
                ]
                title = random.choice(PLACEHOLDER_TITLES)

            caption = f"""📜 *{title}*

🖋️ {description}

[📖 Читать статью]({user_message})

_Источник: [{SOURCE_NAME}]({SOURCE_URL})_"""
            if image_url:
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=caption[:1024], parse_mode=ParseMode.MARKDOWN)
            else:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"{user_message}\n\n<i>Источник: <a href=\"{SOURCE_URL}\">{SOURCE_NAME}</a></i>",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Ошибка при пересылке сообщения: {e}")

# Автоматическая публикация
async def send_harekrishna_article():
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

# Основной запуск
def main():
    global application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_channel))

    # Планировщик по Томскому времени (UTC+7)
    scheduler = AsyncIOScheduler(timezone=pytz.timezone('Asia/Tomsk'))
    scheduler.add_job(send_harekrishna_article, CronTrigger(hour=11, minute=0))
    scheduler.add_job(send_harekrishna_article, CronTrigger(hour=17, minute=0))
    scheduler.start()

    async def test_msg():
        try:
            await application.bot.send_message(chat_id=CHANNEL_ID, text=f"Тестовое сообщение\n\n{SOURCE_NAME}: {SOURCE_URL}", parse_mode=ParseMode.MARKDOWN)
            logger.info("Тестовое сообщение успешно отправлено.")
        except Exception as e:
            logger.error(f"Ошибка при отправке теста: {e}")

    logger.info("Бот запущен.")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_msg())
    application.run_polling()

if __name__ == "__main__":
    main()

