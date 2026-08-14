"""
forwarder.py — The final safety gate before anything reaches any target channel.

DIAGNOSTIC EDITION: adds detailed logging for blacklist debugging.
"""

from __future__ import annotations

import logging
from typing import Optional

from telethon import TelegramClient
from telethon.tl.custom import Message

from database import Database
from filters import evaluate_message


class Forwarder:
    def __init__(
        self,
        user_client: TelegramClient,
        db: Database,
        logger: logging.Logger,
        get_blacklist,
    ):
        self.user_client = user_client
        self.db = db
        self.logger = logger
        self._get_blacklist = get_blacklist

    async def handle_incoming(self, message: Message, matching_services: list[dict]):
        if not matching_services:
            return

        await self.db.increment("received")
        blacklist = await self._get_blacklist()

        # ─── DIAGNOSTIC LOGGING ───
        msg_text = message.raw_text or message.message or ""
        self.logger.info(
            "[BLACKLIST DEBUG] msg_id=%s chat_id=%s text=%r blacklist_terms=%r",
            message.id, message.chat_id, msg_text, blacklist,
        )
        # ───────────────────────────

        result = evaluate_message(message, blacklist)

        # ─── DIAGNOSTIC LOGGING ───
        self.logger.info(
            "[BLACKLIST DEBUG] msg_id=%s result.allowed=%s result.reason=%r",
            message.id, result.allowed, result.reason,
        )
        # ───────────────────────────

        source_channel_id = message.chat_id

        if not result.allowed:
            await self.db.increment("blocked")
            for service in matching_services:
                await self.db.record_service_block(service["id"])
                await self.db.log_event(
                    "blocked", service_id=service["id"],
                    source_message_id=message.id, detail=result.reason,
                )
            return

        for service in matching_services:
            await self._send_to_service(message, source_channel_id, service, result)

    async def _send_to_service(self, message: Message, source_channel_id: int, service: dict, result):
        service_id = service["id"]
        target_channel_id = service["target_channel_id"]

        if await self.db.is_processed(service_id, source_channel_id, message.id):
            await self.db.increment("duplicates_prevented")
            await self.db.log_event("duplicate", service_id=service_id, source_message_id=message.id)
            return

        try:
            final = evaluate_message(message, await self._get_blacklist())
            if not final.allowed:
                await self.db.log_event(
                    "final_gate_rejected", service_id=service_id,
                    source_message_id=message.id, detail=final.reason,
                )
                return
        except Exception as exc:
            self.logger.exception("Final safety gate raised for service %s; refusing to send.", service_id)
            await self.db.increment("errors")
            await self.db.log_event("error", service_id=service_id, source_message_id=message.id, detail=str(exc))
            return

        text_to_send = message.text
        if not text_to_send:
            self.logger.warning("Message %s from source %s has no text; skipping.", message.id, source_channel_id)
            await self.db.log_event("error", service_id=service_id, source_message_id=message.id, detail="Empty text")
            return

        try:
            sent = await self.user_client.send_message(
                entity=target_channel_id,
                message=text_to_send,
            )
            dest_id = self._extract_dest_id(sent)
        except Exception as exc:
            self.logger.exception("Send failed (service=%s, source_message_id=%s)", service_id, message.id)
            await self.db.increment("errors")
            await self.db.record_service_error(service_id, str(exc))
            await self.db.log_event("error", service_id=service_id, source_message_id=message.id, detail=str(exc))
            return

        newly_recorded = await self.db.mark_processed(
            service_id, source_channel_id, message.id, dest_id, result="FORWARDED"
        )
        if newly_recorded:
            await self.db.increment("forwarded")
            await self.db.record_service_forward(service_id)
            await self.db.log_event("forwarded", service_id=service_id, source_message_id=message.id)
            self.logger.info(
                "Sent new message (source %s -> target %s, service %s) based on message %s",
                source_channel_id, target_channel_id, service_id, message.id,
            )
        else:
            await self.db.increment("duplicates_prevented")
            await self.db.log_event("duplicate_race", service_id=service_id, source_message_id=message.id)

    @staticmethod
    def _extract_dest_id(sent) -> Optional[int]:
        try:
            if isinstance(sent, list):
                return sent[0].id if sent else None
            return sent.id
        except Exception:
            return None
