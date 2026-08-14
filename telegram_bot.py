"""
telegram_bot.py — BOT ACCOUNT wrapper (python-telegram-bot).

Used exclusively for admin control (commands in handlers.py). This account
is an administrator in the TARGET CHANNEL per the deployment steps, but the
actual forwarding is performed by the Telethon USER ACCOUNT (see forwarder.py) —
this bot never posts to the target channel itself.
"""

from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler

from database import Database
from handlers import build_handlers


class TelegramBot:
    def __init__(self, bot_token: str, admin_user_id: int, db: Database, matcher_holder: dict, logger: logging.Logger):
        self.application = Application.builder().token(bot_token).build()
        self.logger = logger
        handlers = build_handlers(db, admin_user_id, matcher_holder)

        self.application.add_handler(CommandHandler("start", handlers["start"]))
        self.application.add_handler(CommandHandler("status", handlers["status"]))
        self.application.add_handler(CommandHandler("setsource", handlers["setsource"]))
        self.application.add_handler(CommandHandler("settarget", handlers["settarget"]))
        self.application.add_handler(CommandHandler("setpattern", handlers["setpattern"]))
        self.application.add_handler(CommandHandler("test", handlers["test"]))
        self.application.add_handler(CommandHandler("enable", handlers["enable"]))
        self.application.add_handler(CommandHandler("disable", handlers["disable"]))
        self.application.add_handler(CommandHandler("logs", handlers["logs"]))
        self.application.add_handler(CommandHandler("config", handlers["config"]))
        self.application.add_handler(CommandHandler("reset", handlers["reset"]))

    async def start(self):
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)
        self.logger.info("Admin bot polling started.")

    async def stop(self):
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
