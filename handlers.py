"""
handlers.py — Admin command handlers for the BOT ACCOUNT (multi-service architecture).

Every command is restricted to ADMIN_USER_ID (checked by Telegram user ID,
never username). Unauthorized callers get "⛔ Unauthorized." and nothing else.

Interactive flows (/addservice, /addblacklist, /removeblacklist) use a small
in-memory per-admin conversation state dict — safe because only one user
(ADMIN_USER_ID) is ever allowed to drive these flows.

All replies go to the admin's private chat only. Nothing here ever posts to
a source or target channel.
"""

from __future__ import annotations

import json
import logging
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import Database

logger = logging.getLogger("toss_forward_bot")

BOT_VERSION = "2.0.0 (multi-service, filter-based)"

# ---- Simple in-memory conversation state (single admin, so no concurrency issue) ----
# state shape examples:
#   {"action": "addservice_awaiting_source"}
#   {"action": "addservice_awaiting_target", "source_channel_id": -100123}
#   {"action": "addblacklist_awaiting_term"}
#   {"action": "removeblacklist_awaiting_term"}
_pending_state: dict = {}


def _is_admin(update: Update, admin_user_id: int) -> bool:
    user = update.effective_user
    return bool(user) and user.id == admin_user_id


async def _reject(update: Update):
    await update.effective_message.reply_text("⛔ Unauthorized.")
    logger.warning(
        "Rejected command from non-admin user_id=%s",
        update.effective_user.id if update.effective_user else "unknown",
    )


def _fmt_ts(ts) -> str:
    if not ts:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def build_handlers(db: Database, admin_user_id: int, user_client, start_time: float):
    """user_client: the Telethon client, used to validate channel access when
    adding a service. start_time: unix timestamp of process start, for /uptime."""

    async def _validate_channel_access(channel_id: int) -> tuple[bool, str]:
        try:
            entity = await user_client.get_entity(channel_id)
            title = getattr(entity, "title", str(channel_id))
            return True, title
        except Exception as exc:
            return False, str(exc)

    # ---------------- Basic ----------------

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await help_cmd(update, context)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await update.effective_message.reply_text(
            "Toss Forward Bot — multi-service text forwarder\n\n"
            "SERVICE MANAGEMENT:\n"
            "/addservice — add a new source→target service\n"
            "/services — list all services\n"
            "/service <id> — show one service's detail\n"
            "/startservice <id> — enable a service\n"
            "/stopservice <id> — disable a service\n"
            "/removeservice <id> — remove a service (asks confirmation)\n\n"
            "GLOBAL:\n"
            "/startall — enable all services\n"
            "/stopall — disable all services\n\n"
            "FILTERS:\n"
            "/blacklist — show blacklist\n"
            "/addblacklist — add a word/phrase\n"
            "/removeblacklist — remove a word/phrase\n"
            "/clearblacklist — clear the whole blacklist\n\n"
            "MONITORING:\n"
            "/status  /stats  /health  /logs  /errors  /uptime\n\n"
            "CONFIG:\n"
            "/reload  /version"
        )

    # ---------------- /addservice (interactive) ----------------

    async def addservice(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        _pending_state.clear()
        _pending_state["action"] = "addservice_awaiting_source"
        await update.effective_message.reply_text("Send source channel ID.")

    # ---------------- /services ----------------

    async def services(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        all_services = await db.list_services()
        if not all_services:
            return await update.effective_message.reply_text("No services configured yet. Use /addservice.")

        lines = ["Toss Forward Bot\n"]
        for s in all_services:
            status = "🟢 ENABLED" if s["enabled"] else "🔴 DISABLED"
            lines.append(
                f"Service {s['id']}\n"
                f"Source: {s['source_channel_id']}\n"
                f"Target: {s['target_channel_id']}\n"
                f"Status: {status}\n"
                f"Forwarded: {s['forwarded_count']}\n"
                f"Blocked: {s['blocked_count']}\n"
            )
        await update.effective_message.reply_text("\n".join(lines))

    # ---------------- /service <id> ----------------

    async def service_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        if not context.args:
            return await update.effective_message.reply_text("Usage: /service <id>")
        try:
            service_id = int(context.args[0])
        except ValueError:
            return await update.effective_message.reply_text("Service id must be an integer.")
        s = await db.get_service(service_id)
        if not s:
            return await update.effective_message.reply_text(f"Service {service_id} not found.")

        await update.effective_message.reply_text(
            f"Service {s['id']}\n\n"
            f"Status: {'ENABLED' if s['enabled'] else 'DISABLED'}\n"
            f"Source: {s['source_channel_id']}\n"
            f"Target: {s['target_channel_id']}\n"
            f"Forwarded: {s['forwarded_count']}\n"
            f"Blocked: {s['blocked_count']}\n"
            f"Last forwarded: {_fmt_ts(s['last_forwarded_at'])}\n"
            f"Last error: {s['last_error'] or 'None'}"
        )

    # ---------------- start/stop individual service ----------------

    async def startservice(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        if not context.args:
            return await update.effective_message.reply_text("Usage: /startservice <id>")
        try:
            service_id = int(context.args[0])
        except ValueError:
            return await update.effective_message.reply_text("Service id must be an integer.")
        ok = await db.set_service_enabled(service_id, True)
        if not ok:
            return await update.effective_message.reply_text(f"Service {service_id} not found.")
        await update.effective_message.reply_text(f"🟢 Service {service_id} enabled.")

    async def stopservice(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        if not context.args:
            return await update.effective_message.reply_text("Usage: /stopservice <id>")
        try:
            service_id = int(context.args[0])
        except ValueError:
            return await update.effective_message.reply_text("Service id must be an integer.")
        ok = await db.set_service_enabled(service_id, False)
        if not ok:
            return await update.effective_message.reply_text(f"Service {service_id} not found.")
        await update.effective_message.reply_text(f"⏸ Service {service_id} disabled.")

    # ---------------- remove service (with confirmation) ----------------

    async def removeservice(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        if not context.args:
            return await update.effective_message.reply_text("Usage: /removeservice <id>")
        try:
            service_id = int(context.args[0])
        except ValueError:
            return await update.effective_message.reply_text("Service id must be an integer.")
        s = await db.get_service(service_id)
        if not s:
            return await update.effective_message.reply_text(f"Service {service_id} not found.")

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("CONFIRM", callback_data=f"removeservice_confirm:{service_id}"),
            InlineKeyboardButton("CANCEL", callback_data="removeservice_cancel"),
        ]])
        await update.effective_message.reply_text(
            f"Remove Service {service_id} permanently?", reply_markup=keyboard
        )

    async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query:
            return
        if not query.from_user or query.from_user.id != admin_user_id:
            return await query.answer("⛔ Unauthorized.", show_alert=True)

        data = query.data or ""
        if data.startswith("removeservice_confirm:"):
            service_id = int(data.split(":", 1)[1])
            ok = await db.remove_service(service_id)
            await query.edit_message_text(
                f"✅ Service {service_id} removed." if ok else f"Service {service_id} was already gone."
            )
        elif data == "removeservice_cancel":
            await query.edit_message_text("Cancelled. Service not removed.")
        await query.answer()

    # ---------------- start all / stop all ----------------

    async def startall(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await db.set_all_services_enabled(True)
        await update.effective_message.reply_text("🟢 All services enabled.")

    async def stopall(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await db.set_all_services_enabled(False)
        await update.effective_message.reply_text("🔴 All services disabled. Bot remains online.")

    # ---------------- blacklist ----------------

    async def blacklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        terms = await db.list_blacklist()
        if not terms:
            return await update.effective_message.reply_text("Blacklist is empty.")
        await update.effective_message.reply_text("Blacklist:\n\n" + "\n".join(f"- {t}" for t in terms))

    async def addblacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        # Allow both "/addblacklist casino" (direct) and interactive "/addblacklist" then reply.
        if context.args:
            term = " ".join(context.args)
            added = await db.add_blacklist_term(term)
            return await update.effective_message.reply_text(
                "✅ Added to blacklist." if added else "Already in blacklist."
            )
        _pending_state.clear()
        _pending_state["action"] = "addblacklist_awaiting_term"
        await update.effective_message.reply_text("Send the word or phrase to blacklist.")

    async def removeblacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        if context.args:
            term = " ".join(context.args)
            removed = await db.remove_blacklist_term(term)
            return await update.effective_message.reply_text(
                "✅ Removed from blacklist." if removed else "Term not found in blacklist."
            )
        _pending_state.clear()
        _pending_state["action"] = "removeblacklist_awaiting_term"
        await update.effective_message.reply_text("Send the word or phrase to remove from the blacklist.")

    async def clearblacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        count = await db.clear_blacklist()
        await update.effective_message.reply_text(f"✅ Cleared {count} blacklist entr{'y' if count == 1 else 'ies'}.")

    # ---------------- monitoring ----------------

    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        all_services = await db.list_services()
        enabled_count = sum(1 for s in all_services if s["enabled"])
        stats = await db.get_stats()
        connected = user_client.is_connected() if hasattr(user_client, "is_connected") else True

        await update.effective_message.reply_text(
            "Toss Forward Bot\n"
            "Status: RUNNING\n\n"
            f"Services: {len(all_services)}\n"
            f"Enabled: {enabled_count}\n"
            f"Disabled: {len(all_services) - enabled_count}\n\n"
            f"Total forwarded: {stats.get('forwarded', 0)}\n"
            f"Total blocked: {stats.get('blocked', 0)}\n"
            f"Total errors: {stats.get('errors', 0)}\n\n"
            f"Uptime: {_format_uptime(time.time() - start_time)}\n\n"
            f"Telethon session: {'CONNECTED' if connected else 'DISCONNECTED'}"
        )

    async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        stats = await db.get_stats()
        all_services = await db.list_services()
        lines = [
            "Global stats:",
            f"Received: {stats.get('received', 0)}",
            f"Forwarded: {stats.get('forwarded', 0)}",
            f"Blocked: {stats.get('blocked', 0)}",
            f"Duplicates prevented: {stats.get('duplicates_prevented', 0)}",
            f"Errors: {stats.get('errors', 0)}",
            "",
            "Per-service:",
        ]
        for s in all_services:
            lines.append(f"  Service {s['id']}: forwarded={s['forwarded_count']} blocked={s['blocked_count']}")
        await update.effective_message.reply_text("\n".join(lines))

    async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        connected = user_client.is_connected() if hasattr(user_client, "is_connected") else True
        await update.effective_message.reply_text(
            "Health check:\n"
            f"Database: OK\n"
            f"Telethon session: {'CONNECTED' if connected else 'DISCONNECTED'}\n"
            f"Uptime: {_format_uptime(time.time() - start_time)}"
        )

    async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        rows = await db.recent_events(20)
        if not rows:
            return await update.effective_message.reply_text("No log entries yet.")
        lines = []
        for ts, service_id, msg_id, event, detail in rows:
            t = time.strftime("%H:%M:%S", time.localtime(ts))
            lines.append(f"[{t}] svc={service_id} msg={msg_id} {event} {detail or ''}".strip())
        await update.effective_message.reply_text("\n".join(lines))

    async def errors_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        rows = await db.recent_errors(20)
        if not rows:
            return await update.effective_message.reply_text("No errors logged.")
        lines = []
        for ts, service_id, msg_id, detail in rows:
            t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            lines.append(f"[{t}] svc={service_id} msg={msg_id}: {detail}")
        await update.effective_message.reply_text("\n".join(lines))

    async def uptime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await update.effective_message.reply_text(f"Uptime: {_format_uptime(time.time() - start_time)}")

    # ---------------- config ----------------

    async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        # Services/blacklist are already read live from the DB on every message,
        # so there's nothing stale to reload — this simply confirms that.
        await update.effective_message.reply_text("✅ Configuration is live from the database (nothing to reload).")

    async def version_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return await _reject(update)
        await update.effective_message.reply_text(f"Toss Forward Bot v{BOT_VERSION}")

    # ---------------- interactive text reply handler (for addservice / blacklist flows) ----------------

    async def text_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin(update, admin_user_id):
            return  # silently ignore stray text from non-admins
        if not _pending_state:
            return  # not in a flow; ignore plain text

        action = _pending_state.get("action")
        text = (update.effective_message.text or "").strip()

        if action == "addservice_awaiting_source":
            try:
                source_id = int(text)
            except ValueError:
                return await update.effective_message.reply_text("That doesn't look like a channel ID. Send source channel ID.")
            ok, info = await _validate_channel_access(source_id)
            if not ok:
                _pending_state.clear()
                return await update.effective_message.reply_text(
                    f"❌ Could not access source channel {source_id}: {info}\n"
                    "Make sure the USER account is a member of that channel, then try /addservice again."
                )
            _pending_state["action"] = "addservice_awaiting_target"
            _pending_state["source_channel_id"] = source_id
            return await update.effective_message.reply_text("Send target channel ID.")

        if action == "addservice_awaiting_target":
            try:
                target_id = int(text)
            except ValueError:
                return await update.effective_message.reply_text("That doesn't look like a channel ID. Send target channel ID.")
            ok, info = await _validate_channel_access(target_id)
            if not ok:
                _pending_state.clear()
                return await update.effective_message.reply_text(
                    f"❌ Could not access target channel {target_id}: {info}\n"
                    "Make sure the USER account can post there, then try /addservice again."
                )
            source_id = _pending_state["source_channel_id"]
            _pending_state.clear()
            service_id = await db.add_service(source_id, target_id)
            return await update.effective_message.reply_text(
                f"Service #{service_id}\n"
                f"Source: {source_id}\n"
                f"Target: {target_id}\n"
                f"Status: ENABLED"
            )

        if action == "addblacklist_awaiting_term":
            _pending_state.clear()
            added = await db.add_blacklist_term(text)
            return await update.effective_message.reply_text(
                "✅ Added to blacklist." if added else "Already in blacklist."
            )

        if action == "removeblacklist_awaiting_term":
            _pending_state.clear()
            removed = await db.remove_blacklist_term(text)
            return await update.effective_message.reply_text(
                "✅ Removed from blacklist." if removed else "Term not found in blacklist."
            )

    return {
        "start": start,
        "help": help_cmd,
        "addservice": addservice,
        "services": services,
        "service": service_detail,
        "startservice": startservice,
        "stopservice": stopservice,
        "removeservice": removeservice,
        "callback_query": callback_query_handler,
        "startall": startall,
        "stopall": stopall,
        "blacklist": blacklist_cmd,
        "addblacklist": addblacklist,
        "removeblacklist": removeblacklist,
        "clearblacklist": clearblacklist,
        "status": status,
        "stats": stats_cmd,
        "health": health,
        "logs": logs_cmd,
        "errors": errors_cmd,
        "uptime": uptime_cmd,
        "reload": reload_cmd,
        "version": version_cmd,
        "text_reply": text_reply_handler,
    }


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)
