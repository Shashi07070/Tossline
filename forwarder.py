"""
forwarder.py — The final safety gate before anything reaches the target channel.

This module implements the mandatory fail-closed pipeline from the spec:

    if not matcher.is_valid(message): return
    if already_processed(message): return
    if not forwarding_enabled: return
    if not strict_toss_validation(message): return   # re-check, belt & braces
    forward_original_message()
    record_processed_message()

Only this module is allowed to send messages into the target channel.
Nothing else in the codebase should call target-channel send APIs directly.
"""

from __future__ import annotations

import logging
from typing import Optional

from telethon import TelegramClient
from telethon.tl.custom import Message

from database import Database
from matcher import TossMatcher


class Forwarder:
    def __init__(
        self,
        user_client: TelegramClient,
        db: Database,
        matcher: TossMatcher,
        logger: logging.Logger,
        get_target_channel_id,
        get_forwarding_enabled,
    ):
        """
        get_target_channel_id / get_forwarding_enabled are async callables so the
        forwarder always reads the latest admin-configured values from the DB
        rather than a stale snapshot taken at startup.
        """
        self.user_client = user_client
        self.db = db
        self.matcher = matcher
        self.logger = logger
        self._get_target_channel_id = get_target_channel_id
        self._get_forwarding_enabled = get_forwarding_enabled

    async def handle_incoming(self, message: Message, source_channel_id: int):
        """Entry point called by telegram_user.py for every new channel message."""
        await self.db.increment("checked")

        text_for_matching = self._extract_matchable_text(message)

        # CHECK: does it match?
        result = self.matcher.evaluate(text_for_matching)
        if not result.matched:
            await self.db.increment("ignored")
            await self.db.log_match(message.id, "NO_MATCH", result.reason)
            return

        await self.db.increment("matched")

        # CHECK: already processed? (dedup on channel_id + message_id)
        if await self.db.is_processed(source_channel_id, message.id):
            await self.db.increment("duplicates_prevented")
            await self.db.log_match(message.id, "DUPLICATE_SKIPPED", "")
            return

        # CHECK: forwarding enabled?
        forwarding_enabled = await self._get_forwarding_enabled()
        if not forwarding_enabled:
            await self.db.log_match(message.id, "MATCHED_BUT_DISABLED", "")
            return

        target_channel_id = await self._get_target_channel_id()
        if not target_channel_id:
            self.logger.warning("Match found but no target channel configured; not forwarding.")
            await self.db.log_match(message.id, "MATCHED_NO_TARGET", "")
            return

        # FINAL SAFETY GATE: re-validate immediately before sending. This
        # guards against any state mutation between the initial check and
        # send, and enforces fail-closed behavior on any exception.
        try:
            final_check = self.matcher.evaluate(text_for_matching)
            if not final_check.matched:
                await self.db.log_match(message.id, "FINAL_GATE_REJECTED", final_check.reason)
                return
        except Exception as exc:
            self.logger.exception("Matcher raised during final safety gate; refusing to forward.")
            await self.db.log_match(message.id, "FINAL_GATE_ERROR", str(exc))
            return

        # Forward the ORIGINAL message unchanged (no rewriting, no captions added).
        try:
            sent = await self.user_client.forward_messages(
                entity=target_channel_id,
                messages=message,
            )
            dest_id = self._extract_dest_id(sent)
        except Exception as exc:
            self.logger.exception("Forward failed for source_message_id=%s", message.id)
            await self.db.log_match(message.id, "FORWARD_FAILED", str(exc))
            return

        newly_recorded = await self.db.mark_processed(
            source_channel_id, message.id, dest_id, match_result="MATCH"
        )
        if newly_recorded:
            await self.db.increment("forwarded")
            self.logger.info(
                "Forwarded message %s (team=%s, decision=%s) -> target",
                message.id, result.team, result.decision,
            )
        else:
            # Extremely rare race: another handler already recorded it first.
            await self.db.increment("duplicates_prevented")
            self.logger.warning(
                "Message %s forwarded but a duplicate record already existed (race).",
                message.id,
            )

    @staticmethod
    def _extract_matchable_text(message: Message) -> Optional[str]:
        # Per spec: run matcher against message.text and, when applicable, caption.
        # Telethon exposes both plain text and media captions via `.message` /
        # `.raw_text`; for media messages the caption is also in `.message`.
        return message.raw_text or message.message or None

    @staticmethod
    def _extract_dest_id(sent) -> Optional[int]:
        try:
            if isinstance(sent, list):
                return sent[0].id if sent else None
            return sent.id
        except Exception:
            return None
