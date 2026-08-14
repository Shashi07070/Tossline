"""
main.py — Toss Forward Bot entrypoint.

Run with:  python main.py

Startup sequence:
  1. Load settings (fails fast on missing required env vars).
  2. Connect SQLite database, seed dynamic settings from env if unset.
  3. Build the pattern matcher from stored (or default) pattern config.
  4. Start the admin bot (python-telegram-bot) for /commands.
  5. Authorize + connect the Telethon user client (first run prompts for login).
  6. Wire the Forwarder as the single gate between "new source message" and
     "posted to target channel".
  7. Run until disconnected; handle reconnects/FloodWait via Telethon's
     built-in retry behavior plus our own exception guards.

No startup/shutdown/status message is ever sent to the target channel —
all of that goes to the admin's private chat with the bot, or to local logs.
"""

from __future__ import annotations

import asyncio
import signal
import time

from config import settings
from database import Database
from logger import setup_logger
from matcher import TossMatcher, DEFAULT_PATTERN_CONFIG
from telegram_user import TelegramUserClient
from telegram_bot import TelegramBot
from forwarder import Forwarder


async def async_main():
    logger = setup_logger("toss_forward_bot", settings.log_path)
    logger.info("Starting Toss Forward Bot...")

    db = Database(settings.db_path)
    await db.connect()

    # Seed dynamic settings from env on first run only (don't clobber admin changes).
    if await db.get_setting("source_channel_id") is None and settings.source_channel_id:
        await db.set_setting("source_channel_id", settings.source_channel_id)
    if await db.get_setting("target_channel_id") is None and settings.target_channel_id:
        await db.set_setting("target_channel_id", settings.target_channel_id)
    if await db.get_setting("forwarding_enabled") is None:
        await db.set_setting("forwarding_enabled", False)
    if await db.get_setting("pattern_config") is None:
        await db.set_setting("pattern_config", DEFAULT_PATTERN_CONFIG)

    pattern_config = await db.get_setting("pattern_config", DEFAULT_PATTERN_CONFIG)
    matcher_holder = {"matcher": TossMatcher(pattern_config)}

    # Shared mutable state for admin commands that need things which don't
    # exist yet at TelegramBot-construction time (e.g. the Telethon client),
    # or process-level facts (start time) — see handlers.py docstring.
    runtime = {
        "start_time": time.time(),
        "get_user_client": lambda: None,  # replaced below once user_client exists
    }

    # --- Admin bot (control plane) ---
    bot = TelegramBot(settings.bot_token, settings.admin_user_id, db, matcher_holder, logger, runtime)
    await bot.start()

    # --- User client (monitors source channel) ---
    user_client = TelegramUserClient(settings, logger)
    await user_client.ensure_authorized()
    runtime["get_user_client"] = lambda: user_client.client

    async def get_target_channel_id():
        return await db.get_setting("target_channel_id")

    async def get_forwarding_enabled():
        return bool(await db.get_setting("forwarding_enabled", False))

    async def get_source_channel_id():
        return await db.get_setting("source_channel_id")

    forwarder = Forwarder(
        user_client=user_client.client,
        db=db,
        matcher=matcher_holder["matcher"],
        logger=logger,
        get_target_channel_id=get_target_channel_id,
        get_forwarding_enabled=get_forwarding_enabled,
    )

    # Keep forwarder's matcher reference in sync if /setpattern hot-swaps it.
    class _ForwarderMatcherProxy:
        def evaluate(self, text):
            return matcher_holder["matcher"].evaluate(text)

        def is_match(self, text):
            return matcher_holder["matcher"].is_match(text)

    forwarder.matcher = _ForwarderMatcherProxy()

    user_client.bind_forwarder(forwarder, get_source_channel_id)
    user_client.register_handlers()

    logger.info("Toss Forward Bot fully started. Monitoring for new messages only (no history scan).")

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
