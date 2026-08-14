"""
forwarder.py — The final safety gate before anything reaches any target channel.

Rewritten for the multi-service, filter-based architecture (the old
toss-pattern matcher is gone). Pipeline for every new source-channel message:

    1. Identify which service(s) this source channel belongs to.
    2. For each matching, ENABLED service:
       a. media/content-type check  -> text-only, or reject
       b. link check                -> no URL/link, or reject
       c. blacklist check           -> no blacklisted term, or reject
       d. dedup check                -> not already processed, or skip
       e. send the message TEXT as a NEW message to THAT service's target only
       f. record processed + stats

A message from Source A can only ever reach Source A's configured target(s) —
services are looked up by exact source_channel_id match, and each service's
send call is scoped to that service's own target_channel_id.

Only this module is allowed to send messages into target channels.
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
        """get_blacklist is an async callable returning the current blacklist
        (list[str]) so changes via /addblacklist take effect immediately
        without restarting."""
        self.user_client = user_client
        self.db = db
        self.logger = logger
        self._get_blacklist = get_blacklist

    async def handle_incoming(self, message: Message, matching_services: list[dict]):
        """matching_services: all ENABLED services whose source_channel_id
        matches this message's chat. Normally a source belongs to exactly one
        service, but the lookup supports multiple services sharing a source
        without ever mixing targets."""
        if not matching_services:
            return

        await self.db.increment("received")
        blacklist = await self._get_blacklist()
        result = evaluate_message(message, blacklist)

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

        # Allowed text message -> send as a NEW message (no forward attribution)
        # to each matching service's own target.
        for service in matching_services:
            await self._send_to_service(message, source_channel_id, service, result)

    async def _send_to_service(self, message: Message, source_channel_id: int, service: dict, result):
        service_id = service["id"]
        target_channel_id = service["target_channel_id"]

        # Dedup check.
        if await self.db.is_processed(service_id, source_channel_id, message.id):
            await self.db.increment("duplicates_prevented")
            await self.db.log_event("duplicate", service_id=service_id, source_message_id=message.id)
            return

        # Final safety re-check immediately before sending (fail closed).
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

        # Guard: ensure we have actual text to send.
        text_to_send = message.text
        if not text_to_send:
            self.logger.warning("Message %s from source %s has no text; skipping.", message.id, source_channel_id)
            await self.db.log_event("error", service_id=service_id, source_message_id=message.id, detail="Empty text")
            return

        try:
            # Send as a NEW message — no "Forwarded from" attribution.
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
            # Rare race: another handler already recorded it first.
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
