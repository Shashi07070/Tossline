"""
telegram_user.py — Telethon USER ACCOUNT client.

Responsibilities ONLY:
  - Authenticate as a normal Telegram user (phone + OTP + optional 2FA).
  - Listen for NEW messages in the configured source channel (no history scan).
  - Hand each new message to the Forwarder for matching + forwarding.

This account does NOT need admin rights in the source channel, and it is the
account that actually calls forward_messages() into the target channel (the
bot account is admin.control-plane only, per spec section 17).
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
        self._get_source_channel_id = None

    def bind_forwarder(self, forwarder, get_source_channel_id):
        """Wire in the forwarder + a getter for the (possibly admin-updated)
        source channel id, called from main.py during startup."""
        self._forwarder = forwarder
        self._get_source_channel_id = get_source_channel_id

    async def ensure_authorized(self):
        """First-time interactive login flow. Safe to call on every startup —
        if a session already exists this is a no-op beyond connecting."""
        await self.client.connect()
        if await self.client.is_user_authorized():
            self.logger.info("Telethon user session already authorized.")
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
                source_channel_id = await self._get_source_channel_id()
                if not source_channel_id:
                    return  # no source configured yet

                chat = await event.get_chat()
                chat_id = getattr(chat, "id", None)
                # Telethon channel ids from get_chat() are the "bare" id; normalize
                # comparison against the configured (often -100-prefixed) id.
                if not self._channel_ids_match(chat_id, source_channel_id):
                    return

                if self._forwarder is None:
                    return

                await self._forwarder.handle_incoming(event.message, source_channel_id)
            except Exception:
                self.logger.exception("Unhandled error in new-message handler; message ignored (fail closed).")

    @staticmethod
    def _channel_ids_match(bare_id: int | None, configured_id: int | None) -> bool:
        if bare_id is None or configured_id is None:
            return False
        # Configured id may be stored either as the bare id or the -100-prefixed
        # "full" id used elsewhere in the Bot API. Accept both forms.
        candidates = {bare_id, -bare_id, int(f"-100{bare_id}")}
        return configured_id in candidates or configured_id == bare_id

    async def run_until_disconnected(self):
        self.logger.info("Telethon user client listening for new source-channel messages only.")
        await self.client.run_until_disconnected()

    async def disconnect(self):
        await self.client.disconnect()
