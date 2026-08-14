"""
main.py — Toss Forward Bot entrypoint (multi-service, filter-based edition).

Run with:  python main.py

Startup sequence:
  1. Load settings (fails fast on missing required env vars). Legacy
     SOURCE_CHANNEL_ID/TARGET_CHANNEL_ID are NOT required anymore — services
     live in the database and are managed via /addservice.
  2. Connect SQLite database (additive migration — no data is dropped).
  3. Start the admin bot (python-telegram-bot) for /commands.
  4. Reuse the EXISTING Telethon session if valid (no forced re-login).
  5. Wire the Forwarder as the single gate between "new source message" and
     "posted to a target channel", routed per-service.
  6. Run until disconnected; reconnect automatically on drop.

No startup/shutdown/status/blocked message is ever sent to any target
channel — all of that goes to the admin's private chat with the bot, or to
local logs (LOG_PATH) / the event_log table.
"""

from __future__ import annotations

import asyncio
import signal
import time

from config import settings
from database import Database
from logger import setup_logger
from telegram_user import TelegramUserClient
from telegram_bot import TelegramBot
from forwarder import Forwarder


async def async_main():
    logger = setup_logger("toss_forward_bot", settings.log_path)
    logger.info("Starting Toss Forward Bot (multi-service)...")

    db = Database(settings.db_path)
    await db.connect()

    # One-time convenience migration: if legacy SOURCE_CHANNEL_ID/TARGET_CHANNEL_ID
    # are set in .env AND no services exist yet at all, create Service #1 from
    # them so upgrading doesn't silently stop forwarding. This never runs again
    # once at least one service exists, and never overwrites admin-managed services.
    existing_services = await db.list_services()
    if not existing_services and settings.source_channel_id and settings.target_channel_id:
        service_id = await db.add_service(settings.source_channel_id, settings.target_channel_id)
        logger.info(
            "Migrated legacy SOURCE_CHANNEL_ID/TARGET_CHANNEL_ID from .env into Service #%s.",
            service_id,
        )

    start_time = time.time()

    # --- User client (reuses existing Telethon session; monitors all configured sources) ---
    user_client = TelegramUserClient(settings, logger)
    await user_client.ensure_authorized()

    # --- Admin bot (control plane) ---
    bot = TelegramBot(settings.bot_token, settings.admin_user_id, db, user_client.client, logger)
    await bot.start()

    async def get_blacklist():
        return await db.list_blacklist()

    async def get_all_services():
        return await db.list_services()

    forwarder = Forwarder(
        user_client=user_client.client,
        db=db,
        logger=logger,
        get_blacklist=get_blacklist,
    )

    user_client.bind_forwarder(forwarder, get_all_services)
    user_client.register_handlers()

    logger.info(
        "Toss Forward Bot fully started. %d service(s) configured. Monitoring for new messages only.",
        len(await db.list_services()),
    )

    stop_event = asyncio.Event()

    def _handle_shutdown_signal():
        logger.info("Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_shutdown_signal)
        except NotImplementedError:
            pass  # Windows fallback: rely on KeyboardInterrupt

    run_task = asyncio.create_task(user_client.run_until_disconnected())
    stop_task = asyncio.create_task(stop_event.wait())

    await asyncio.wait([run_task, stop_task], return_when=asyncio.FIRST_COMPLETED)

    logger.info("Shutting down...")
    await user_client.disconnect()
    await bot.stop()
    await db.close()
    logger.info("Shutdown complete.")


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
