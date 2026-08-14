"""
telegram_bot.py — BOT ACCOUNT wrapper (python-telegram-bot), multi-service edition.

Used exclusively for admin control (handlers.py). The BOT account does NOT
need to be an administrator of any source channel; the Telethon USER account
handles all source reading and the actual forward. Where the bot account is
made an administrator of a target channel, that's only relevant if the
deployment later wants the bot itself to post there — the default forward
path uses the USER account exclusively, per spec.
"""

from __future__ import annotations

import logging
import time

from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from database import Database
from handlers import build_handlers


class TelegramBot:
    def __init__(self, bot_token: str, admin_user_id: int, db: Database, user_client, logger: logging.Logger):
        self.application = Application.builder().token(bot_token).build()
        self.logger = logger
        start_time = time.time()
        handlers = build_handlers(db, admin_user_id, user_client, start_time)

        cmd = self.application.add_handler
        cmd(CommandHandler("start", handlers["start"]))
        cmd(CommandHandler("help", handlers["help"]))

        cmd(CommandHandler("addservice", handlers["addservice"]))
        cmd(CommandHandler("services", handlers["services"]))
        cmd(CommandHandler("service", handlers["service"]))
        cmd(CommandHandler("startservice", handlers["startservice"]))
        cmd(CommandHandler("stopservice", handlers["stopservice"]))
        cmd(CommandHandler("removeservice", handlers["removeservice"]))

        cmd(CommandHandler("startall", handlers["startall"]))
        cmd(CommandHandler("stopall", handlers["stopall"]))

        cmd(CommandHandler("blacklist", handlers["blacklist"]))
        cmd(CommandHandler("addblacklist", handlers["addblacklist"]))
        cmd(CommandHandler("removeblacklist", handlers["removeblacklist"]))
        cmd(CommandHandler("clearblacklist", handlers["clearblacklist"]))

        cmd(CommandHandler("status", handlers["status"]))
        cmd(CommandHandler("stats", handlers["stats"]))
        cmd(CommandHandler("health", handlers["health"]))
        cmd(CommandHandler("logs", handlers["logs"]))
        cmd(CommandHandler("errors", handlers["errors"]))
        cmd(CommandHandler("uptime", handlers["uptime"]))

        cmd(CommandHandler("reload", handlers["reload"]))
        cmd(CommandHandler("version", handlers["version"]))

        cmd(CallbackQueryHandler(handlers["callback_query"]))

        # Plain-text replies drive the interactive /addservice and blacklist flows.
        # Must not intercept text starting with "/" (commands).
        cmd(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers["text_reply"]))

    async def start(self):
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)
        self.logger.info("Admin bot polling started.")

    async def stop(self):
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
