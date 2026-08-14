# Toss Forward Bot — v2.0 (Multi-Service, Filter-Based)

**This is an update to the existing Toss Forward Bot project.** The old
toss-pattern matcher has been completely removed. The bot now supports
multiple independent source → target forwarding services, each filtered by
media type, links, and a global blacklist — with no pattern-matching
requirement at all.

```
SOURCE CHANNEL (any of N) → USER ACCOUNT (Telethon, existing session) → Filters → ALLOWED? → that source's TARGET CHANNEL
                                                                                  → NO → ignored (never notified anywhere)
```

---

## 1. What changed

### Files changed
- `database.py` — added `services`, `blacklist`, `processed_messages_v2`, `event_log` tables. Old tables (`processed_messages`, `kv_settings`, `stats`, `match_log`) are **kept, never dropped**.
- `forwarder.py` — rewritten for multi-service routing + the new filter pipeline (media/link/blacklist) instead of pattern matching.
- `telegram_user.py` — rewritten to route incoming messages to every enabled service whose source channel matches, with a defensive guard so a target channel can never accidentally act as a source. **Reuses the existing Telethon session — no re-login is triggered.**
- `handlers.py` — rewritten with the full new command set (service management, blacklist, monitoring).
- `telegram_bot.py` — rewritten to wire the new commands plus a callback-query handler (for `/removeservice` confirmation buttons) and a text-reply handler (for the interactive `/addservice` / `/addblacklist` / `/removeblacklist` flows).
- `main.py` — rewritten: no longer requires `SOURCE_CHANNEL_ID`/`TARGET_CHANNEL_ID`; services live in the database, managed via `/addservice`. A one-time convenience migration promotes legacy env-var values into "Service #1" **only if no services exist yet**, so upgrading doesn't silently stop forwarding.
- `config.py` — legacy `SOURCE_CHANNEL_ID`/`TARGET_CHANNEL_ID` are now explicitly documented as optional/deprecated (they were already optional in the dataclass).
- `.env.example` — updated to reflect the above; existing `.env` files are untouched and still work.
- `requirements.txt` — Telethon pin loosened to `>=1.36.0,<2.0.0` so a redeploy never reinstalls an older pinned version over a working one.

### Files removed
- `matcher.py` — the entire toss-pattern matching engine. Deleted, not just disabled.
- `test_matcher.py` — its tests (pattern-specific) are no longer applicable.

### Files added
- `filters.py` — the new filter pipeline: `is_text_only_message()`, `contains_link()`, `contains_blacklisted_term()`, `evaluate_message()`.
- `test_filters.py` — unit tests for the new filter pipeline (21 tests, all passing — see §10).

### Dependencies changed
- `telethon` pin loosened from `==1.36.0` to `>=1.36.0,<2.0.0`.
- No other dependency changes (`python-telegram-bot`, `aiosqlite`, `python-dotenv` unchanged).

### Confirmation: the old toss pattern matcher is completely removed
`matcher.py` and `test_matcher.py` no longer exist in the project. No code
path requires a message to contain `"WON THE TOSS"` or any BAT/BOWL
structure. Forwarding is now driven entirely by the filter pipeline in
`filters.py`.

---

## 2. New architecture

- **USER ACCOUNT** (Telethon, your existing authorized session): reads every
  configured source channel and performs the actual forward. Does not need
  admin rights in any source channel.
- **BOT ACCOUNT** (python-telegram-bot): admin control plane only — all
  `/commands`. Never posts to any target channel itself.
- **Services**: each service is an independent `source_channel_id →
  target_channel_id` pair, stored in SQLite, managed via `/addservice`,
  `/services`, `/startservice`, `/stopservice`, `/removeservice`. Messages
  from Source A can only ever reach Source A's own target(s) — there is no
  code path that looks up a different service's target.

### Forwarding rule (no pattern required)

For every new message from a configured, enabled source:

1. **Media/content-type check** — must be genuinely text-only (no photo,
   video, GIF, sticker, voice, video note, audio, document, contact,
   location, poll, or game — even if a caption/text is also present).
2. **Link check** — reject if it contains `http://`, `https://`, `www.`,
   `t.me/`, `telegram.me/`, `telegram.dog/`, or a recognizable domain
   pattern (case-insensitive).
3. **Blacklist check** — reject if it contains any admin-configured
   blacklisted word/phrase (case-insensitive substring match).
4. **Dedup check** — reject if this exact `(service, source_channel_id,
   source_message_id)` was already processed.
5. **Forward** — the original message, completely unmodified, to that
   service's target only.

Any exception anywhere in the pipeline is caught and treated as **blocked**
(fail closed) — nothing uncertain is ever forwarded.

---

## 3. Database migration instructions

No manual migration step is required — `database.py` creates the new tables
alongside the old ones automatically on startup (`CREATE TABLE IF NOT
EXISTS`), and nothing is dropped.

**If you were running the old single-service version with `SOURCE_CHANNEL_ID`
and `TARGET_CHANNEL_ID` set in `.env`:** on first startup after this update,
if no services exist yet in the database, those two values are automatically
migrated into "Service #1" so forwarding doesn't stop. After that, manage
everything via `/addservice` etc. — the env vars are ignored from then on.

If you'd rather start clean instead of auto-migrating, just run `/addservice`
yourself and leave `SOURCE_CHANNEL_ID`/`TARGET_CHANNEL_ID` blank in `.env`.

Your existing `toss_forward.db` file and `.session` file are both reused
as-is — back them up before deploying if you want a safety net, but nothing
in this update deletes or recreates them.

---

## 4. Exact AWS deployment commands

Assuming the existing AWS deployment (systemd service `toss-forward-bot.service`):

```bash
# 1. SSH into the instance
ssh your-user@your-instance

# 2. Go to the project directory
cd /path/to/toss-forward-bot

# 3. Stop the running service
sudo systemctl stop toss-forward-bot.service

# 4. Back up the database and session (recommended, not required)
cp toss_forward.db toss_forward.db.bak
cp toss_forward_user.session toss_forward_user.session.bak

# 5. Deploy the updated files (matcher.py / test_matcher.py should be
#    removed; all other files listed in §1 replaced/added)
#    e.g. via git pull, scp, or your existing deployment method

# 6. Update dependencies (Telethon pin loosened — pip will keep or upgrade
#    within the >=1.36.0,<2.0.0 range, never downgrade a working install)
source venv/bin/activate
pip install -r requirements.txt --upgrade

# 7. Restart the service
sudo systemctl restart toss-forward-bot.service

# 8. Confirm it's running and check logs
sudo systemctl status toss-forward-bot.service
tail -f toss_forward.log
```

## 5. Exact systemd restart command

```bash
sudo systemctl restart toss-forward-bot.service
```

The existing unit file does not need to change — it still just runs `python
main.py`. It continues to:
- start automatically after reboot (if `enabled`)
- restart after crashes (if `Restart=on-failure` or similar is set, as before)
- keep running after SSH disconnects
- reconnect to Telegram automatically (Telethon's built-in reconnect logic)

---

## 6. Telegram command list

**Basic**
- `/start`, `/help`

**Service management**
- `/addservice` — interactive: bot asks for source channel ID, then target channel ID, validates access to both, creates the service (ENABLED by default)
- `/services` — list all services with status and per-service forwarded/blocked counts
- `/service <id>` — detail view for one service (status, source, target, counts, last forwarded, last error)
- `/startservice <id>` / `/stopservice <id>` — enable/disable one service; others keep running
- `/removeservice <id>` — asks for CONFIRM/CANCEL before deleting

**Global**
- `/startall` / `/stopall` — enable/disable every service at once (bot itself stays online either way)

**Filters**
- `/blacklist` — show all blacklisted terms
- `/addblacklist` — interactive ("Send the word or phrase to blacklist.") or `/addblacklist <term>` directly
- `/removeblacklist` — interactive or `/removeblacklist <term>` directly
- `/clearblacklist` — removes all blacklist entries

**Monitoring**
- `/status` — services enabled/disabled counts, total forwarded/blocked/errors, uptime, Telethon connection state
- `/stats` — global + per-service breakdown
- `/health` — quick DB/Telethon connectivity check
- `/logs` — recent 20 events (received/forwarded/blocked/duplicate/error) — private only
- `/errors` — recent 20 error entries with detail
- `/uptime` — process uptime

**Configuration**
- `/reload` — confirms config is read live from the DB (nothing to actually reload)
- `/version` — bot version string

All commands check the caller's numeric Telegram user ID against
`ADMIN_USER_ID` — never a username. Anyone else gets `⛔ Unauthorized.` and
nothing else.

---

## 7. Example `/addservice` flow

```
Admin:  /addservice
Bot:    Send source channel ID.
Admin:  -1001631852106
Bot:    Send target channel ID.
Admin:  -1002284155038
Bot:    Service #1
        Source: -1001631852106
        Target: -1002284155038
        Status: ENABLED
```

If the USER account can't access either channel, the bot reports the
specific problem and does **not** create a broken service:

```
Bot:    ❌ Could not access source channel -1001631852106: <error detail>
        Make sure the USER account is a member of that channel, then try /addservice again.
```

---

## 8. Example blacklist setup

```
Admin:  /addblacklist
Bot:    Send the word or phrase to blacklist.
Admin:  casino
Bot:    ✅ Added to blacklist.
```

Or directly: `/addblacklist casino` does the same in one step.

```
Admin:  /blacklist
Bot:    Blacklist:

        - casino
```

---

## 9. Setup / prerequisites (unchanged from before)

Your existing `.env`, Telethon session, and deployment mechanism all
continue to work as-is. If you're setting this up fresh, see the original
setup steps for BotFather / my.telegram.org / first login — nothing about
that process changed. The only difference: you no longer need to set
`SOURCE_CHANNEL_ID` / `TARGET_CHANNEL_ID` — use `/addservice` after startup
instead.

---

## 10. Testing performed before this update was delivered

All of the following were actually executed against the real code (not
just written) before this was called complete:

**Unit tests — `test_filters.py` (21 tests, all passing)**
```
python -m pytest test_filters.py -v
```
Covers link detection (all required forms + case-insensitivity + plain-text
non-matches), blacklist matching (case-insensitive, empty inputs), the
media/content-type gate (text-only vs. every blocked media type), and the
combined `evaluate_message()` pipeline including a fail-closed check for
broken/unexpected input.

**Database smoke test** (`database.py`, run against a real SQLite file):
service add/list/enable/disable, blacklist add/dup-reject/remove/clear,
per-service dedup isolation, global + per-service stats/counters, service
removal, event logging. All assertions passed.

**End-to-end forwarder smoke test** (`forwarder.py`, real `Database` +
fake Telethon client), directly covering the spec's test list:

| # | Spec test | Result |
|---|---|---|
| 1 | Normal text → forwarded | PASSED |
| 2 | Blacklisted text → blocked | PASSED |
| 3 | Text with URL → blocked | PASSED |
| 4 | Photo → blocked | PASSED |
| 5 | Video → blocked | PASSED |
| 6 | GIF → blocked | PASSED |
| 7 | Sticker → blocked | PASSED |
| 8 | Two source services → each only reaches its own target | PASSED |
| 9 | Duplicate delivery → not forwarded twice | PASSED |
| 10 | Service with no enabled match → forwards nothing | PASSED |
| 11 | Other service keeps working independently | PASSED |

**Routing/normalization test** (`telegram_user.py`): confirmed channel-id
matching works whether IDs are stored bare or `-100`-prefixed, and that an
unrelated target channel never matches as a source.

**Static checks**: every module (`config.py`, `database.py`, `filters.py`,
`logger.py`, `forwarder.py`, `telegram_user.py`, `handlers.py`,
`telegram_bot.py`, `main.py`, `test_filters.py`) compiles cleanly with
`python -m py_compile`.

Not independently testable in this environment (no live network / Telegram
credentials available here): actual Telegram API connectivity, real
`/addservice` channel-access validation against live channels, and the
interactive Telegram button/callback flow end-to-end. These follow the same
python-telegram-bot / Telethon APIs used in the original working
deployment and should be verified once against your real bot/channels
after deploying (see the walkthroughs in §7–§8).

---

## 11. Project structure

```
toss-forward-bot/
├── main.py            # entrypoint — wires everything together
├── config.py           # env var loading (legacy source/target now optional)
├── database.py          # SQLite: services, blacklist, dedup, stats, event log
├── filters.py            # NEW — link/media/blacklist filter pipeline (replaces matcher.py)
├── telegram_user.py       # Telethon USER ACCOUNT client (multi-service routing)
├── telegram_bot.py        # BOT ACCOUNT wrapper (admin commands + callbacks)
├── forwarder.py          # final safety gate + per-service forward call
├── handlers.py           # admin command implementations (service mgmt, blacklist, monitoring)
├── logger.py            # local logging setup
├── test_filters.py        # unit tests for the filter pipeline
├── requirements.txt
├── .env.example
├── Procfile
├── Dockerfile
└── README.md
```
