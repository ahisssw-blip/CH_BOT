#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════╗
║         بوت الاشتراكات الاحترافي - النسخة المطورة          ║
║         تطوير: Manus AI  |  v6.0 Professional           ║
╚══════════════════════════════════════════════════════════╝
"""

import logging

from config import ADMIN_IDS, BASE_DIR, TOKEN
from database import init_db, close_db
from telegram import BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from handlers.admin import cmd_admin
from handlers.common import backup_database, build_channels_keyboard, resume_broadcasts, run_broadcast
from handlers.user import cmd_start, cmd_help, handle_callback, handle_text
from locales import TEXT
from utils.anti_spam import cleanup_spam_trackers, is_spamming, spam_tracker, spam_warned, SPAM_MUTE_SECONDS

__all__ = [
    "ADMIN_IDS", "TEXT", "ParseMode", "build_channels_keyboard", "cmd_start", "cmd_help",
    "handle_callback", "handle_text", "run_broadcast", "is_spamming", "spam_tracker",
    "spam_warned", "SPAM_MUTE_SECONDS",
]

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "🚀 بدء البوت"),
        BotCommand("help", "❓ مساعدة")
    ])
    application.create_task(resume_broadcasts(application), name="resume-broadcasts")


def main():
    init_db()
    if not TOKEN:
        print("❌ خطأ: لم يتم العثور على TOKEN في ملف الإعدادات!")
        return

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    if app.job_queue:
        app.job_queue.run_repeating(backup_database, interval=21600, first=10)
        app.job_queue.run_repeating(cleanup_spam_trackers, interval=600, first=600)

    logger.info("🚀 البوت v6.0 يعمل الآن بكافة التعديلات المطلوبة!")
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        close_db()


if __name__ == "__main__":
    main()
