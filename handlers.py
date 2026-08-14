"""
handlers.py — Admin command handlers for the BOT ACCOUNT (python-telegram-bot).

Every command:
  - Is restricted to ADMIN_USER_ID (any other caller is rejected silently-but-logged).
  - Replies ONLY to the admin in their private chat with the bot.
  - Never posts anything into the target channel.

Dynamic settings (source/target channel, pattern config, enabled flag) are
persisted in SQLite via the shared Database instance so they survive restarts
and are picked up live by the forwarder/user-client without a redeploy.
"""

from __future__ import annotations

import json
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from database import Database
from matcher import TossMatcher, DEFAULT_PATTERN_CONFIG

logger = logging.getLogger("toss_forward_bot")


def _is_admin(update: Update, admin_user_id: int) -> bool:
    user = update.effective_user
    return bool(user) and user.id == admin_user_id


async def _reject(update: Update):
    # Deliberately terse — do not reveal bot internals to non-admin callers.
    await update.effective_message.reply_text("Unauthorized.")
    logger.warning(
        "Rejected command from non-admin user_id=%s",
        update.effective_user.id if update.effective_user else "unknown",
    )


def build_handlers(db: Database, admin_user_id: int, matcher_holder: dict):
    """Returns a dict of async command handler functions closing over db/admin_user_id.
    matcher_holder is a single-key mutable dict {'matcher': TossMatcher(...)} so
    /setpattern can hot-swap the active matcher without restarting the process.
    """

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await update.effective_message.reply_text(
            "Toss Forward Bot ready.\n\n"
            "Commands:\n"
            "/status - show status & stats\n"
            "/setsource <channel_id> - set source channel\n"
            "/settarget <channel_id> - set target channel\n"
            "/setpattern <json> - update matching pattern config\n"
            "/test <sample text> - test a message against the current pattern\n"
            "/enable - enable forwarding\n"
            "/disable - disable forwarding\n"
            "/logs - show recent match/no-match decisions\n"
            "/config - show current configuration\n"
            "/reset - reset stats counters"
        )

    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)

        source = await db.get_setting("source_channel_id")
        target = await db.get_setting("target_channel_id")
        enabled = await db.get_setting("forwarding_enabled", False)
        stats = await db.get_stats()
        last_match_ts = await db.get_setting("last_match_ts")
        last_match_str = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_match_ts))
            if last_match_ts else "never"
        )

        msg = (
            "Toss Forward Bot\n"
            "Status: RUNNING\n\n"
            f"Source: {source if source else 'not configured'}\n"
            f"Target: {target if target else 'not configured'}\n\n"
            f"Forwarding: {'ENABLED' if enabled else 'DISABLED'}\n\n"
            f"Messages checked: {stats.get('checked', 0)}\n"
            f"Matched: {stats.get('matched', 0)}\n"
            f"Forwarded: {stats.get('forwarded', 0)}\n"
            f"Ignored: {stats.get('ignored', 0)}\n"
            f"Duplicates prevented: {stats.get('duplicates_prevented', 0)}\n\n"
            f"Last successful match: {last_match_str}"
        )
        await update.effective_message.reply_text(msg)

    async def setsource(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        if not context.args:
            return await update.effective_message.reply_text("Usage: /setsource <channel_id>")
        try:
            channel_id = int(context.args[0])
        except ValueError:
            return await update.effective_message.reply_text("Channel id must be an integer.")
        await db.set_setting("source_channel_id", channel_id)
        await update.effective_message.reply_text(f"Source channel set to {channel_id}.")

    async def settarget(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        if not context.args:
            return await update.effective_message.reply_text("Usage: /settarget <channel_id>")
        try:
            channel_id = int(context.args[0])
        except ValueError:
            return await update.effective_message.reply_text("Channel id must be an integer.")
        await db.set_setting("target_channel_id", channel_id)
        await update.effective_message.reply_text(
            f"Target channel set to {channel_id}.\n"
            "Make sure the BOT ACCOUNT is an administrator there (needed only if the "
            "bot itself posts; the USER account performs the actual forward)."
        )

    async def setpattern(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        raw = " ".join(context.args) if context.args else ""
        if not raw:
            return await update.effective_message.reply_text(
                "Usage: /setpattern <json config>\n\n"
                f"Current default:\n{json.dumps(DEFAULT_PATTERN_CONFIG, indent=2)}"
            )
        try:
            new_config = json.loads(raw)
            if not isinstance(new_config, dict):
                raise ValueError("Pattern config must be a JSON object.")
            # Validate it actually constructs a working matcher before committing.
            TossMatcher(new_config)
        except Exception as exc:
            return await update.effective_message.reply_text(f"Invalid pattern config: {exc}")

        merged = {**DEFAULT_PATTERN_CONFIG, **new_config}
        await db.set_setting("pattern_config", merged)
        matcher_holder["matcher"] = TossMatcher(merged)
        await update.effective_message.reply_text("Pattern configuration updated and applied.")

    async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        sample = update.effective_message.text.split(" ", 1)
        sample_text = sample[1] if len(sample) > 1 else ""
        if not sample_text:
            return await update.effective_message.reply_text("Usage: /test <sample message text>")

        result = matcher_holder["matcher"].evaluate(sample_text)
        if result.matched:
            reply = (
                "MATCH ✅\n\n"
                f"Team detected: {result.team}\n"
                f"Toss phrase detected: yes\n"
                f"Decision detected: {result.decision}\n"
                f"Ending detected: yes\n"
                f"Final validation: PASS"
            )
        else:
            reply = (
                "NO MATCH ❌\n\n"
                f"Reason: {result.reason}\n"
                f"Team detected: {result.team or 'none'}\n"
                f"Toss phrase detected: {'yes' if result.toss_phrase_found else 'no'}\n"
                f"Decision detected: {result.decision or 'no'}\n"
                f"Ending detected: {'yes' if result.ending_found else 'no'}"
            )
        # This reply goes ONLY to the admin's private chat with the bot — never to the target channel.
        await update.effective_message.reply_text(reply)

    async def enable(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        source = await db.get_setting("source_channel_id")
        target = await db.get_setting("target_channel_id")
        if not source or not target:
            return await update.effective_message.reply_text(
                "Cannot enable: configure both /setsource and /settarget first."
            )
        await db.set_setting("forwarding_enabled", True)
        await update.effective_message.reply_text("Forwarding ENABLED.")

    async def disable(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await db.set_setting("forwarding_enabled", False)
        await update.effective_message.reply_text("Forwarding DISABLED.")

    async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        rows = await db.recent_logs(20)
        if not rows:
            return await update.effective_message.reply_text("No log entries yet.")
        lines = []
        for ts, msg_id, decision, reason in rows:
            t = time.strftime("%H:%M:%S", time.localtime(ts))
            lines.append(f"[{t}] msg={msg_id} {decision} {reason}".strip())
        await update.effective_message.reply_text("\n".join(lines))

    async def config_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        source = await db.get_setting("source_channel_id")
        target = await db.get_setting("target_channel_id")
        enabled = await db.get_setting("forwarding_enabled", False)
        pattern = matcher_holder["matcher"].config
        await update.effective_message.reply_text(
            "Current configuration:\n\n"
            f"Source: {source}\n"
            f"Target: {target}\n"
            f"Forwarding enabled: {enabled}\n\n"
            f"Pattern config:\n{json.dumps(pattern, indent=2)}"
        )

    async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        for key in ("checked", "matched", "forwarded", "ignored", "duplicates_prevented"):
            await db.set_setting(f"_reset_marker_{key}", True)  # no-op marker, stats table reset below
        # Directly zero the stats table.
        async with db._lock:
            await db._conn.execute("UPDATE stats SET value = 0")
            await db._conn.commit()
        await update.effective_message.reply_text("Stats counters reset.")

    return {
        "start": start,
        "status": status,
        "setsource": setsource,
        "settarget": settarget,
        "setpattern": setpattern,
        "test": test,
        "enable": enable,
        "disable": disable,
        "logs": logs,
        "config": config_cmd,
        "reset": reset,
    }
