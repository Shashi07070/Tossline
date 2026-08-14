"""
telegram_bot.py — BOT ACCOUNT wrapper (python-telegram-bot).

Used exclusively for admin control (commands in handlers.py). This account
is an administrator in the TARGET CHANNEL per the deployment steps, but the
actual forwarding is performed by the Telethon USER ACCOUNT (see forwarder.py) —
this bot never posts to the target channel itself.
"""

from __future__ import annotations

import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from database import Database
from handlers import build_handlers


class TelegramBot:
    def __init__(
        self,
        bot_token: str,
        admin_user_id: int,
        db: Database,
        matcher_holder: dict,
        logger: logging.Logger,
        runtime: dict,
    ):
        self.application = Application.builder().token(bot_token).build()
        self.logger = logger
        runtime["bot_application"] = self.application
        handlers = build_handlers(db, admin_user_id, matcher_holder, runtime)

        # Monitoring
        self.application.add_handler(CommandHandler("start", handlers["start"]))
        self.application.add_handler(CommandHandler("help", handlers["help"]))
        self.application.add_handler(CommandHandler("status", handlers["status"]))
        self.application.add_handler(CommandHandler("stats", handlers["stats"]))
        self.application.add_handler(CommandHandler("uptime", handlers["uptime"]))
        self.application.add_handler(CommandHandler("health", handlers["health"]))
        self.application.add_handler(CommandHandler("logs", handlers["logs"]))
        self.application.add_handler(CommandHandler("errors", handlers["errors"]))

        # Forwarding control
        self.application.add_handler(CommandHandler("startforward", handlers["startforward"]))
        self.application.add_handler(CommandHandler("stopforward", handlers["stopforward"]))
        # Back-compat aliases for the original /enable /disable commands.
        self.application.add_handler(CommandHandler("enable", handlers["startforward"]))
        self.application.add_handler(CommandHandler("disable", handlers["stopforward"]))
        self.application.add_handler(CommandHandler("test", handlers["test"]))

        # Pattern management
        self.application.add_handler(CommandHandler("pattern", handlers["pattern"]))
        self.application.add_handler(CommandHandler("patterns", handlers["patterns"]))
        self.application.add_handler(CommandHandler("addpattern", handlers["addpattern"]))
        self.application.add_handler(CommandHandler("removepattern", handlers["removepattern"]))
        self.application.add_handler(CommandHandler("testpattern", handlers["testpattern"]))
        self.application.add_handler(CommandHandler("setpattern", handlers["setpattern"]))

        # Configuration
        self.application.add_handler(CommandHandler("source", handlers["source"]))
        self.application.add_handler(CommandHandler("target", handlers["target"]))
        self.application.add_handler(CommandHandler("setsource", handlers["setsource"]))
        self.application.add_handler(CommandHandler("settarget", handlers["settarget"]))
        self.application.add_handler(CommandHandler("config", handlers["config"]))
        self.application.add_handler(CommandHandler("reload", handlers["reload"]))
        self.application.add_handler(CommandHandler("version", handlers["version"]))
        self.application.add_handler(CommandHandler("reset", handlers["reset"]))

        # Restart
        self.application.add_handler(CommandHandler("restart", handlers["restart"]))

        # Plain-text follow-ups for /addpattern, /removepattern, /testpattern
        # conversational flows. Must be added after command handlers so it
        # only catches non-command text.
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handlers["handle_pending_text"])
        )

    async def start(self):
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)
        self.logger.info("Admin bot polling started.")

    async def stop(self):
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
