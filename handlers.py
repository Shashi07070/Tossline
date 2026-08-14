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
import platform
import re
import time

import telethon
from telegram import Update
from telegram.ext import ContextTypes

from database import Database
from logger import get_recent_errors
from matcher import TossMatcher, DEFAULT_PATTERN_CONFIG

logger = logging.getLogger("toss_forward_bot")

BOT_VERSION = "1.1.0"
DEFAULT_DECISION_VARIANTS = DEFAULT_PATTERN_CONFIG["decision_variants"]
DAY_SECONDS = 86400


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


def build_handlers(db: Database, admin_user_id: int, matcher_holder: dict, runtime: dict):
    """Returns a dict of async command handler functions closing over db/admin_user_id.
    matcher_holder is a single-key mutable dict {'matcher': TossMatcher(...)} so
    /setpattern can hot-swap the active matcher without restarting the process.

    runtime is a single mutable dict shared with main.py/telegram_bot.py holding
    things that don't exist yet at handler-construction time:
      - start_time: float, process start (for /uptime)
      - bot_application: the python-telegram-bot Application (for /health, /version)
      - get_user_client: callable -> Telethon TelegramClient | None
      - get_source_channel_id / get_target_channel_id: async callables (from main.py)
      - get_forwarding_enabled: async callable
    Also used as a tiny per-admin conversation-state holder for /addpattern,
    /removepattern, /testpattern's "send the message" flow: runtime['pending'].
    """
    runtime.setdefault("pending", {})  # {admin_user_id: "addpattern" | "removepattern" | "testpattern"}

    def _fmt_duration(seconds: float) -> str:
        seconds = int(seconds)
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours or days:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await update.effective_message.reply_text(
            "🟢 Toss Forward Bot ready.\n\n"
            "Send /help for the full command list."
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

    def _format_match_result(sample_text: str, result) -> str:
        if result.matched:
            return (
                "✅ MATCH\n"
                "This message WOULD be forwarded.\n\n"
                f"Team detected: {result.team}\n"
                f"Toss phrase detected: yes\n"
                f"Decision detected: {result.decision}\n"
                f"Ending detected: yes\n"
                f"Final validation: PASS"
            )
        return (
            "❌ NO MATCH\n"
            "This message would be ignored.\n\n"
            f"Reason: {result.reason}\n"
            f"Team detected: {result.team or 'none'}\n"
            f"Toss phrase detected: {'yes' if result.toss_phrase_found else 'no'}\n"
            f"Decision detected: {result.decision or 'no'}\n"
            f"Ending detected: {'yes' if result.ending_found else 'no'}"
        )

    async def testpattern(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Spec item 16: test whether a message would be forwarded, without
        actually forwarding it. Supports both '/testpattern <text>' inline
        and the conversational '/testpattern' -> 'send the message' flow."""
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        sample = update.effective_message.text.split(" ", 1)
        sample_text = sample[1] if len(sample) > 1 else ""
        if not sample_text:
            runtime["pending"][admin_user_id] = "testpattern"
            return await update.effective_message.reply_text("Send the message to test.")

        result = matcher_holder["matcher"].evaluate(sample_text)
        # This reply goes ONLY to the admin's private chat with the bot — never to the target channel.
        await update.effective_message.reply_text(_format_match_result(sample_text, result))

    async def startforward(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        source = await db.get_setting("source_channel_id")
        target = await db.get_setting("target_channel_id")
        if not source or not target:
            return await update.effective_message.reply_text(
                "Cannot enable: configure both /setsource and /settarget first."
            )
        await db.set_setting("forwarding_enabled", True)
        await update.effective_message.reply_text("🟢 Forwarding enabled.")

    async def stopforward(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await db.set_setting("forwarding_enabled", False)
        await update.effective_message.reply_text("⏸ Forwarding paused. Bot is still online.")

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
        for key in ("checked", "matched", "forwarded", "ignored", "duplicates_prevented", "errors"):
            await db.set_setting(f"_reset_marker_{key}", True)  # no-op marker, stats table reset below
        # Directly zero the stats table.
        async with db._lock:
            await db._conn.execute("UPDATE stats SET value = 0")
            await db._conn.commit()
        await update.effective_message.reply_text("Stats counters reset.")

    # ------------------------------------------------------------------
    # New commands (admin control system spec)
    # ------------------------------------------------------------------

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await update.effective_message.reply_text(
            "🛠 Toss Forward Bot — Admin Commands\n\n"
            "MONITORING\n"
            "/status - quick status & counters\n"
            "/stats - detailed statistics\n"
            "/uptime - process uptime\n"
            "/health - real connectivity/health check\n"
            "/logs - last 20 match decisions\n"
            "/errors - last 10 errors\n\n"
            "FORWARDING CONTROL\n"
            "/startforward - enable forwarding\n"
            "/stopforward - pause forwarding (bot stays online)\n"
            "/test - real connectivity test\n\n"
            "PATTERN MANAGEMENT\n"
            "/pattern - show active matching rules summary\n"
            "/patterns - numbered list of decision patterns\n"
            "/addpattern - add a new decision pattern\n"
            "/removepattern [n] - remove a non-default pattern\n"
            "/testpattern [text] - test if a message would forward\n"
            "/setpattern <json> - replace full pattern config (advanced)\n\n"
            "CONFIGURATION\n"
            "/source - source channel config/status\n"
            "/target - target channel config/status\n"
            "/setsource <id> - set source channel\n"
            "/settarget <id> - set target channel\n"
            "/config - show full current configuration\n"
            "/reload - reload config/pattern from DB\n"
            "/version - bot & dependency versions\n"
            "/reset - zero out stats counters\n\n"
            "RESTART\n"
            "/restart - restart info (systemd-managed)"
        )

    async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        s = await db.get_stats()
        today_start = _midnight_ts()
        forwarded_today = await db.count_matchlog_since(today_start, ("FORWARDED",))
        ignored_today = await db.count_matchlog_since(today_start, ("NO_MATCH",))
        last_match_ts = await db.get_setting("last_match_ts")
        last_match_str = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_match_ts))
            if last_match_ts else "never"
        )
        await update.effective_message.reply_text(
            "📊 Statistics\n\n"
            f"Total received: {s.get('checked', 0)}\n"
            f"Total matched: {s.get('matched', 0)}\n"
            f"Total forwarded: {s.get('forwarded', 0)}\n"
            f"Total ignored: {s.get('ignored', 0)}\n"
            f"Total errors: {s.get('errors', 0)}\n\n"
            f"Forwarded today: {forwarded_today}\n"
            f"Ignored today: {ignored_today}\n\n"
            f"Last forwarded: {last_match_str}"
        )

    def _midnight_ts() -> float:
        now = time.localtime()
        return time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))

    async def uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        elapsed = time.time() - runtime.get("start_time", time.time())
        await update.effective_message.reply_text(f"⏱ Uptime: {_fmt_duration(elapsed)}")

    async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        lines = ["🩺 Health Check\n"]

        # Bot process — we're running, so this is green by definition.
        lines.append("Bot Process: 🟢")

        # Telegram Bot API
        app = runtime.get("bot_application")
        try:
            if app is not None:
                await app.bot.get_me()
                lines.append("Telegram Bot API: 🟢")
            else:
                lines.append("Telegram Bot API: 🔴")
        except Exception:
            lines.append("Telegram Bot API: 🔴")

        # Telethon user session
        user_client = runtime.get("get_user_client", lambda: None)()
        telethon_ok = False
        try:
            if user_client is not None:
                telethon_ok = user_client.is_connected() and await user_client.is_user_authorized()
            lines.append(f"Telethon User Session: {'🟢' if telethon_ok else '🔴'}")
        except Exception:
            lines.append("Telethon User Session: 🔴")

        # Source / target channel reachability (only meaningful if session is up)
        source_id = await db.get_setting("source_channel_id")
        target_id = await db.get_setting("target_channel_id")

        async def _check_entity(channel_id):
            if not telethon_ok or not channel_id:
                return False
            try:
                await user_client.get_entity(int(channel_id))
                return True
            except Exception:
                return False

        source_ok = await _check_entity(source_id)
        target_ok = await _check_entity(target_id)
        lines.append(f"Source Channel: {'🟢' if source_ok else '🔴'}")
        lines.append(f"Target Channel: {'🟢' if target_ok else '🔴'}")

        # Forwarding engine
        enabled = await db.get_setting("forwarding_enabled", False)
        engine_ok = bool(enabled) and matcher_holder.get("matcher") is not None
        lines.append(f"Forwarding Engine: {'🟢' if engine_ok else ('🟡 paused' if matcher_holder.get('matcher') else '🔴')}")

        # Database
        try:
            await db.get_stats()
            lines.append("Database: 🟢")
        except Exception:
            lines.append("Database: 🔴")

        await update.effective_message.reply_text("\n".join(lines))

    async def errors_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        recent = get_recent_errors(10)
        if not recent:
            return await update.effective_message.reply_text("✅ No recent errors.")
        lines = ["🚨 Recent Errors\n"]
        for ts, msg in recent:
            t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            # Truncate long tracebacks to keep the message readable.
            msg_short = msg if len(msg) < 300 else msg[:300] + "…"
            lines.append(f"[{t}] {msg_short}")
        await update.effective_message.reply_text("\n\n".join(lines))

    async def test_connectivity(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Spec item 11: real connectivity test (Bot API, Telethon session,
        source/target access, forwarding config). Does NOT send anything to
        the target channel."""
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        # Reuse the same real checks as /health, phrased as a connectivity result.
        await health(update, context)

    async def pattern_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        cfg = matcher_holder["matcher"].config
        variants = "\n".join(f"  • {k} → {v}" for k, v in cfg["decision_variants"].items())
        await update.effective_message.reply_text(
            "📋 Active matching rule\n\n"
            f"Toss phrase: \"{cfg['toss_phrase']}\"\n"
            f"Connector: \"{cfg['connector']}\"\n"
            f"Decision variants:\n{variants}\n"
            f"Required checkmarks: {cfg['ending_checkmarks']}\n\n"
            "A message must contain a team identifier, then the toss phrase, "
            "the connector, a decision variant, and end with the checkmarks — "
            "in that order, with no unrelated text in between."
        )

    async def patterns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        cfg = matcher_holder["matcher"].config
        lines = ["📋 Patterns (decision variants)\n"]
        for i, (phrase, outcome) in enumerate(cfg["decision_variants"].items(), start=1):
            protected = " (default, protected)" if phrase in DEFAULT_DECISION_VARIANTS else ""
            lines.append(f"{i}. \"{phrase}\" → {outcome}{protected}")
        lines.append("\nUse /addpattern to add one, /removepattern <n> to remove a non-default one.")
        await update.effective_message.reply_text("\n".join(lines))

    async def addpattern(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        raw = " ".join(context.args) if context.args else ""
        if not raw:
            runtime["pending"][admin_user_id] = "addpattern"
            return await update.effective_message.reply_text(
                "Send the new pattern/rule.\n"
                "Format: PHRASE => BAT   or   PHRASE => BOWL\n"
                "Example: DECIDED TO FIELD => BOWL"
            )
        await _do_addpattern(update, raw)

    async def _do_addpattern(update: Update, raw: str):
        m = re.match(r"^(.+?)\s*(?:=>|->)\s*(BAT|BOWL)\s*$", raw.strip(), re.IGNORECASE)
        if not m:
            return await update.effective_message.reply_text(
                "Invalid format. Use: PHRASE => BAT  or  PHRASE => BOWL"
            )
        phrase = m.group(1).strip().upper()
        outcome = m.group(2).strip().upper()
        if not phrase:
            return await update.effective_message.reply_text("Pattern phrase cannot be empty.")

        cfg = matcher_holder["matcher"].config
        new_variants = {**cfg["decision_variants"], phrase: outcome}
        new_cfg = {**cfg, "decision_variants": new_variants}
        try:
            TossMatcher(new_cfg)  # validate before committing
        except Exception as exc:
            return await update.effective_message.reply_text(f"Could not add pattern: {exc}")

        await db.set_setting("pattern_config", new_cfg)
        matcher_holder["matcher"] = TossMatcher(new_cfg)
        await update.effective_message.reply_text("✅ Pattern added.")

    async def removepattern(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        cfg = matcher_holder["matcher"].config
        variants = list(cfg["decision_variants"].items())

        if not context.args:
            lines = ["Select a pattern to remove — send /removepattern <number>:\n"]
            for i, (phrase, outcome) in enumerate(variants, start=1):
                protected = " (default, protected)" if phrase in DEFAULT_DECISION_VARIANTS else ""
                lines.append(f"{i}. \"{phrase}\" → {outcome}{protected}")
            return await update.effective_message.reply_text("\n".join(lines))

        try:
            idx = int(context.args[0]) - 1
            if idx < 0 or idx >= len(variants):
                raise ValueError
        except ValueError:
            return await update.effective_message.reply_text("Invalid pattern number. See /patterns.")

        phrase, _outcome = variants[idx]
        if phrase in DEFAULT_DECISION_VARIANTS:
            return await update.effective_message.reply_text(
                f"❌ \"{phrase}\" is a default/core pattern and cannot be removed."
            )

        new_variants = {k: v for k, v in variants if k != phrase}
        new_cfg = {**cfg, "decision_variants": new_variants}
        await db.set_setting("pattern_config", new_cfg)
        matcher_holder["matcher"] = TossMatcher(new_cfg)
        await update.effective_message.reply_text(f"✅ Removed pattern \"{phrase}\".")

    async def source_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        source_id = await db.get_setting("source_channel_id")
        user_client = runtime.get("get_user_client", lambda: None)()
        connected = False
        if source_id and user_client is not None:
            try:
                await user_client.get_entity(int(source_id))
                connected = True
            except Exception:
                connected = False
        await update.effective_message.reply_text(
            "📥 Source Channel\n\n"
            f"Channel ID: {source_id if source_id else 'not configured'}\n"
            f"Access: {'CONNECTED' if connected else 'DISCONNECTED'}"
        )

    async def target_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        target_id = await db.get_setting("target_channel_id")
        user_client = runtime.get("get_user_client", lambda: None)()
        connected = False
        if target_id and user_client is not None:
            try:
                await user_client.get_entity(int(target_id))
                connected = True
            except Exception:
                connected = False
        await update.effective_message.reply_text(
            "📤 Target Channel\n\n"
            f"Channel ID: {target_id if target_id else 'not configured'}\n"
            f"Access: {'CONNECTED' if connected else 'DISCONNECTED'}"
        )

    async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        pattern_config = await db.get_setting("pattern_config", DEFAULT_PATTERN_CONFIG)
        try:
            matcher_holder["matcher"] = TossMatcher(pattern_config)
        except Exception as exc:
            return await update.effective_message.reply_text(f"Reload failed: {exc}")
        await update.effective_message.reply_text("🔄 Configuration reloaded from database.")

    async def version_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        try:
            from importlib.metadata import version as pkg_version
            ptb_version = pkg_version("python-telegram-bot")
        except Exception:
            ptb_version = "unknown"
        await update.effective_message.reply_text(
            "ℹ️ Version Info\n\n"
            f"Bot: Toss Forward Bot v{BOT_VERSION}\n"
            f"Python: {platform.python_version()}\n"
            f"Telethon: {telethon.__version__}\n"
            f"python-telegram-bot: {ptb_version}"
        )

    async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await update.effective_message.reply_text(
            "🔁 This bot runs under systemd on AWS EC2.\n"
            "Restart must be performed by the server/systemd, e.g.:\n"
            "sudo systemctl restart toss-forward-bot\n\n"
            "There is no safe in-process self-restart mechanism, so none was triggered."
        )

    async def handle_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Routes a plain text message to whichever conversational flow the
        admin most recently started (/addpattern, /removepattern, /testpattern)."""
        if not _is_admin(update, admin_user_id):
            return  # silently ignore non-admin free text; no command was invoked
        pending = runtime["pending"].pop(admin_user_id, None)
        if pending is None:
            return  # not in a conversation flow; ignore stray text
        text = update.effective_message.text or ""

        if pending == "testpattern":
            result = matcher_holder["matcher"].evaluate(text)
            return await update.effective_message.reply_text(_format_match_result(text, result))

        if pending == "addpattern":
            return await _do_addpattern(update, text)

        if pending == "removepattern":
            cfg = matcher_holder["matcher"].config
            variants = list(cfg["decision_variants"].items())
            try:
                idx = int(text.strip()) - 1
                if idx < 0 or idx >= len(variants):
                    raise ValueError
            except ValueError:
                return await update.effective_message.reply_text("Invalid pattern number. See /patterns.")
            phrase, _outcome = variants[idx]
            if phrase in DEFAULT_DECISION_VARIANTS:
                return await update.effective_message.reply_text(
                    f"❌ \"{phrase}\" is a default/core pattern and cannot be removed."
                )
            new_variants = {k: v for k, v in variants if k != phrase}
            new_cfg = {**cfg, "decision_variants": new_variants}
            await db.set_setting("pattern_config", new_cfg)
            matcher_holder["matcher"] = TossMatcher(new_cfg)
            return await update.effective_message.reply_text(f"✅ Removed pattern \"{phrase}\".")

    return {
        "start": start,
        "help": help_cmd,
        "status": status,
        "stats": stats,
        "uptime": uptime,
        "health": health,
        "setsource": setsource,
        "settarget": settarget,
        "setpattern": setpattern,
        "test": test_connectivity,
        "testpattern": testpattern,
        "startforward": startforward,
        "stopforward": stopforward,
        "logs": logs,
        "errors": errors_cmd,
        "config": config_cmd,
        "reset": reset,
        "pattern": pattern_cmd,
        "patterns": patterns_cmd,
        "addpattern": addpattern,
        "removepattern": removepattern,
        "source": source_cmd,
        "target": target_cmd,
        "reload": reload_cmd,
        "version": version_cmd,
        "restart": restart_cmd,
        "handle_pending_text": handle_pending_text,
    }
