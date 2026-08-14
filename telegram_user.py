"""
telegram_user.py — Telethon USER ACCOUNT client (multi-service routing).

Unchanged responsibility from the original build: this is still the account
that reads source channels and performs the actual forward. The existing
Telethon session is reused as-is — no re-login is triggered if it's already
authorized.

What changed: instead of a single hard-coded source channel, every incoming
NewMessage event is matched against the ENABLED services stored in the
database (source_channel_id -> one or more services), and only messages from
a channel that is actually a configured, enabled source are handed to the
Forwarder. Target channels are never treated as sources, even if a channel id
coincidentally appears as both (defensive guard below).
"""

from __future__ import annotations

import logging

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

from config import Settings


class TelegramUserClient:
    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
        self._forwarder = None
        self._get_services = None  # async callable -> list[dict] of ALL services (enabled + disabled)

    def bind_forwarder(self, forwarder, get_services):
        """get_services returns every configured service (used to build the
        source->services map and to defensively exclude target channels)."""
        self._forwarder = forwarder
        self._get_services = get_services

    async def ensure_authorized(self):
        """Reuses the existing session if valid. Only prompts for phone/OTP/2FA
        if there is genuinely no valid session yet — never forces a new login
        over a working one."""
        await self.client.connect()
        if await self.client.is_user_authorized():
            self.logger.info("Telethon user session already authorized (existing session reused).")
            return

        print("=== Telegram USER ACCOUNT first-time login ===")
        phone = input("Enter phone number (with country code, e.g. +91...): ").strip()
        await self.client.send_code_request(phone)
        code = input("Enter the OTP code you received: ").strip()
        try:
            await self.client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            password = input("Two-factor authentication is enabled. Enter your 2FA password: ").strip()
            await self.client.sign_in(password=password)

        self.logger.info("Telethon user session authorized and saved as '%s'.", self.settings.session_name)
        # Never log phone/code/password.

    def register_handlers(self):
        @self.client.on(events.NewMessage)
        async def _on_new_message(event):
            try:
                if self._forwarder is None or self._get_services is None:
                    return

                chat_id = event.chat_id
                if chat_id is None:
                    return

                all_services = await self._get_services()

                # Defensive guard: never treat a configured target channel as
                # a source, even if it coincidentally matches a source id
                # from a different, unrelated service.
                target_ids = {s["target_channel_id"] for s in all_services}
                if self._normalized_match(chat_id, target_ids):
                    return

                matching_enabled_services = [
                    s for s in all_services
                    if s["enabled"] and self._normalized_match(chat_id, {s["source_channel_id"]})
                ]
                if not matching_enabled_services:
                    return  # not a configured/enabled source — ignore (private chats, groups, unrelated channels, etc.)

                await self._forwarder.handle_incoming(event.message, matching_enabled_services)
            except Exception:
                self.logger.exception("Unhandled error in new-message handler; message ignored (fail closed).")

    @staticmethod
    def _normalized_match(chat_id: int, configured_ids: set[int]) -> bool:
        """Telethon's event.chat_id is typically already the -100-prefixed
        'full' id for channels/supergroups, but configured ids might have
        been entered either way — accept both forms."""
        if not configured_ids:
            return False
        candidates = {chat_id}
        if chat_id < 0:
            bare = int(str(chat_id).replace("-100", "", 1)) if str(chat_id).startswith("-100") else -chat_id
            candidates.add(bare)
            candidates.add(-bare)
            candidates.add(int(f"-100{abs(bare)}"))
        else:
            candidates.add(-chat_id)
            candidates.add(int(f"-100{chat_id}"))
        return bool(candidates & configured_ids)

    async def run_until_disconnected(self):
        self.logger.info("Telethon user client listening for new messages across all configured sources.")
        await self.client.run_until_disconnected()

    async def disconnect(self):
        await self.client.disconnect()
