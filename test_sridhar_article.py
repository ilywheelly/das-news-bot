#!/usr/bin/env python3
"""Тесты очистки RSS excerpt и HTML-парсера sridharmaharaj.ru без Telegram."""

import os
import unittest

os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ.setdefault("SUPERADMIN_ID", "1")

from DAS import (  # noqa: E402
    _escape_telegram_md,
    _format_sridhar_telegram_message,
    clean_sridhar_rss_excerpt,
    parse_sridhar_article_html,
)


RSS_EXCERPT = """
<p>Скачать: аудиозапись в MP3&nbsp;(2 мин. 57 сек., 3,0 МБ) транскрипцию в DOCX (17 КБ) транскрипцию в PDF (139 КБ) Шрила Бхакти Ракшак Шридхар Дев-Госвами Махарадж Я не могу не вспоминать то обстоятельство &#8230; <a href="https://sridharmaharaj.ru/example">Читать далее <span class="meta-nav">&#8594;</span></a></p>
<p>Запись <a href="https://sridharmaharaj.ru/example">1107. 1982.05.14.D3. Поэзия</a> впервые появилась <a href="https://sridharmaharaj.ru">Жемчужины духовной мудрости</a>.</p>
"""

ARTICLE_HTML = """
<html><body>
<h1 class="entry-title">1107. 1982.05.14.D3. Поэзия Шридхара Махараджа стала реальностью благодаря проповеди Бхактиведанты Свами</h1>
<div class="entry-content">
<audio class="wp-audio-shortcode" controls="controls"><a href="/dl/file.mp3">/dl/file.mp3</a></audio>
<p style="margin-bottom: 0;">Скачать:</p>
<ul>
<li><a class="download-mp3" href="/dl/file.mp3">аудиозапись в MP3 (2 мин. 57 сек., 3,0 МБ)</a></li>
<li><a class="download-txt" href="/dl/file.docx">транскрипцию в DOCX (17 КБ)</a></li>
<li><a class="download-pdf" href="/dl/file.pdf">транскрипцию в PDF (139 КБ)</a></li>
</ul>
<p style="text-align: right;"><strong>Шрила Бхакти Ракшак Шридхар Дев-Госвами Махарадж</strong></p>
<p>Я не могу не вспоминать то обстоятельство, что Прабхупад хотел отправить проповедовать меня на запад.</p>
<p>Это невозможно, нечто невозможное — успех Свами Махараджа.<span id="more-8492"></span></p>
<div class="sharedaddy">Share</div>
</div>
<div class="entry-utility">Запись опубликована в рубрике тест</div>
<div class="navigation"><a href="/next">Читать далее <span class="meta-nav">→</span></a></div>
</body></html>
"""


class SridharRssFallbackTests(unittest.TestCase):
    def test_strips_html_and_wordpress_boilerplate(self):
        text = clean_sridhar_rss_excerpt(RSS_EXCERPT)
        self.assertNotIn("<p>", text)
        self.assertNotIn("<a href", text)
        self.assertNotIn("<span", text)
        self.assertNotIn("&#8230;", text)
        self.assertNotIn("&#8594;", text)
        self.assertNotIn("Читать далее", text)
        self.assertNotIn("впервые появилась", text)
        self.assertNotIn("Скачать", text)
        self.assertIn("Шрила Бхакти Ракшак Шридхар Дев-Госвами Махарадж", text)
        self.assertIn("Я не могу не вспоминать", text)
        self.assertIn("…", text)

    def test_empty_input(self):
        self.assertEqual(clean_sridhar_rss_excerpt(""), "")


class SridharHtmlParserTests(unittest.TestCase):
    def test_extracts_lecture_and_skips_downloads(self):
        title, body = parse_sridhar_article_html(ARTICLE_HTML)
        self.assertIn("1107. 1982.05.14.D3", title)
        self.assertNotIn("<p>", body)
        self.assertNotIn("<a href", body)
        self.assertNotIn("Скачать", body)
        self.assertNotIn("MP3", body)
        self.assertNotIn("DOCX", body)
        self.assertNotIn("PDF", body)
        self.assertNotIn("Читать далее", body)
        self.assertNotIn("впервые появилась", body)
        self.assertNotIn("Share", body)
        self.assertIn("Шрила Бхакти Ракшак Шридхар Дев-Госвами Махарадж", body)
        self.assertIn("Я не могу не вспоминать", body)
        self.assertIn("\n\n", body)

    def test_markdown_message_has_no_raw_html(self):
        title, body = parse_sridhar_article_html(ARTICLE_HTML)
        message = _format_sridhar_telegram_message(title, body, "https://sridharmaharaj.ru/example")
        self.assertNotIn("<p>", message)
        self.assertNotIn("&#8230;", message)
        self.assertIn("Читать статью", message)
        self.assertIn(_escape_telegram_md(title), message)


if __name__ == "__main__":
    unittest.main()
