# Toss Forward Bot

A production-ready Telegram forwarding system that watches a source channel and
forwards **only** messages matching a strict "won the toss" alert pattern into a
target channel — nothing else, ever.

```
SOURCE CHANNEL → USER ACCOUNT (Telethon) → Strict Matcher → MATCH? → TARGET CHANNEL
                                                            → NO MATCH → ignored
```

- **USER ACCOUNT** (Telethon/MTProto): a normal member of the source channel, no
  admin rights needed there. It also performs the actual forward into the target
  channel.
- **BOT ACCOUNT** (python-telegram-bot): admin control plane only — `/status`,
  `/setpattern`, `/test`, etc. It does **not** need to be in the source channel,
  and it never posts to the target channel itself.

---

## 1. Prerequisites

- Python 3.11+
- A Telegram account to act as the USER ACCOUNT (must be a member of the source channel)
- A Telegram Bot token for the BOT ACCOUNT (admin control plane)

---

## 2. Setup steps

### 2.1 Create the Telegram bot (BotFather)

1. Open a chat with **@BotFather** on Telegram.
2. Send `/newbot`, follow the prompts, and copy the token it gives you.
3. Put it in `.env` as `BOT_TOKEN`.

### 2.2 Obtain your Telegram API ID / API HASH

1. Go to https://my.telegram.org and log in with the phone number you'll use
   for the USER ACCOUNT.
2. Open **API Development Tools**, create an app, and copy `api_id` / `api_hash`.
3. Put them in `.env` as `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`.

### 2.3 Configure environment variables

```bash
cp .env.example .env
# then edit .env and fill in BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH, ADMIN_USER_ID
```

`ADMIN_USER_ID` is your own numeric Telegram user id (e.g. from **@userinfobot**).
Only this user can run admin commands against the bot.

### 2.4 Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2.5 Log in the USER ACCOUNT (first run)

```bash
python main.py
```

On first run it will prompt, in the terminal, for:

1. Phone number (with country code)
2. The OTP code Telegram sends you
3. Your 2FA password, only if you have two-factor auth enabled

After a successful login, a Telethon session file is saved (named per
`TELETHON_SESSION`) and reused automatically on every future start — you will
not be prompted again unless the session is deleted or revoked.

**The USER ACCOUNT needs access to (membership in) the source channel.**
**The BOT ACCOUNT does not need to be present in the source channel at all.**

### 2.6 Add the bot as administrator to the target channel

Open the target channel → Administrators → Add Admin → add your bot.
(The bot account needs appropriate permission in the target channel per the
architecture; the actual forward call is issued by the USER ACCOUNT, so make
sure the USER ACCOUNT also has permission to post there — e.g. it's a member
with posting rights, or an admin, depending on how the channel is configured.)

### 2.7 Configure the source and target channels

In a private chat with your bot (as the admin user):

```
/setsource -1001234567890
/settarget -1009876543210
```

Channel ids are numeric; you can get them via any channel-id-lookup bot, or by
forwarding a channel message to **@userinfobot** / **@JsonDumpBot**.

### 2.8 Configure the toss pattern

The default pattern (matching the current source format) is applied automatically.
To customize it later:

```
/setpattern {"toss_phrase": "WON THE TOSS", "decision_variants": {"DECIDED TO BAT": "BAT", "DECIDED TO BOWL": "BOWL", "DICIDED TO BAT": "BAT", "DICIDED TO BOWL": "BOWL"}}
```

`/setpattern` with no arguments shows the current default config as a starting template.

### 2.9 Test the pattern

```
/test 🇷🇼 RWANDA - U19 🇷🇼 WON THE TOSS AND DECIDED TO BAT ✔️✔️
```

The bot replies **privately** with `MATCH ✅` or `NO MATCH ❌` plus a breakdown.
This test output is never sent to the target channel.

### 2.10 Enable forwarding

```
/enable
```

(`/enable` refuses to run until both source and target are configured.)

### 2.11 Verify forwarding

Post a message matching the pattern in the source channel and confirm it
appears, unmodified, in the target channel. Check `/status` for updated counters.

### 2.12 Deploy

See **Deployment** below.

### 2.13 Troubleshooting

See **Troubleshooting** below.

---

## 3. Admin commands

All commands are usable **only** by `ADMIN_USER_ID`; anyone else gets `Unauthorized.`

| Command | Purpose |
|---|---|
| `/start` | Show command list |
| `/status` | Show running status, source/target, forwarding state, and counters |
| `/setsource <channel_id>` | Set the source channel to monitor |
| `/settarget <channel_id>` | Set the target channel to forward into |
| `/setpattern <json>` | Replace/update the matching pattern config |
| `/test <text>` | Dry-run the matcher against sample text, privately |
| `/enable` | Turn forwarding on |
| `/disable` | Turn forwarding off |
| `/logs` | Show the most recent match/no-match decisions |
| `/config` | Show current source, target, enabled flag, and pattern config |
| `/reset` | Reset the stats counters to zero |

---

## 4. How the strict matching prevents unrelated messages from reaching the target

The matcher (`matcher.py`) runs a sequential, fail-closed, multi-layer check —
**every** layer must pass, in order, or the message is dropped silently:

1. **Team identifier check** — there must be non-trivial alphanumeric text
   before the toss phrase (emoji/flags/punctuation are stripped as decoration,
   never counted as the identifier itself, and never required).
2. **Toss phrase check** — the literal configured phrase (default
   `WON THE TOSS`) must appear.
3. **Connector check** — the word `AND` must immediately follow (only
   whitespace in between).
4. **Decision check** — must immediately be followed by one of the configured
   decision variants (`DECIDED TO BAT`, `DECIDED TO BOWL`, and the known typo
   variants `DICIDED TO BAT` / `DICIDED TO BOWL`). Nothing else after "AND" is
   accepted — `DECIDED TO PLAY` or arbitrary text fails here.
5. **Ending check** — the decision must be immediately followed (whitespace
   tolerated) by exactly the configured number of checkmark characters, and
   **nothing substantive is allowed after them** — trailing junk like
   "... ✔️✔️ Follow our channel!" is rejected.
6. **Structural sanity** — because checks 1–5 are enforced in strict sequence
   over contiguous text (not independently via "contains" checks anywhere in
   the message), a message can't pass by having the right keywords scattered
   in the wrong order or context (e.g. "Rwanda won the toss but match
   cancelled" fails at the connector/decision stage).

Any exception anywhere in the matcher is caught and converted into a
**NO MATCH** result — the system fails closed by design. Typo tolerance is
implemented as an explicit, configurable list of literal variants rather than
fuzzy string matching, so it can never accidentally widen the net to catch
unrelated messages.

A final safety gate re-runs full validation immediately before the forward
call in `forwarder.py`, and duplicate delivery is prevented via a SQLite
`UNIQUE(source_channel_id, source_message_id)` constraint.

---

## 5. Deployment

### Option A — Generic Python host / VPS

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in values
python main.py
```

Run under a process supervisor (systemd, supervisord, pm2, or the platform's
own worker mechanism) so it restarts automatically on crash. A `Procfile` is
included for `Procfile`-based hosts:

```
worker: python main.py
```

**Important:** the Telethon session file and the SQLite database must persist
across restarts/deploys — mount/persist whatever directory `TELETHON_SESSION`
and `DB_PATH` point to.

### Option B — Docker

```bash
docker build -t toss-forward-bot .
docker run -d \
  --name toss-forward-bot \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  toss-forward-bot
```

The Dockerfile persists the session and database under `/app/data` via a
volume so they survive container restarts.

**Note on first login with Docker:** the interactive phone/OTP/2FA prompt
needs a TTY. Run once interactively to create the session:

```bash
docker run -it --rm --env-file .env -v $(pwd)/data:/app/data toss-forward-bot
```

Then subsequent normal runs (`docker run -d ...`) will reuse the saved session.

---

## 6. Running the tests

```bash
python -m pytest test_matcher.py -v
# or
python test_matcher.py
```

`test_matcher.py` covers all example messages from the spec (positive and
negative), plus additional robustness cases (empty input, non-string input,
whitespace variation, typo variants, custom pattern configs, and weird
Unicode input never raising an exception).

---

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Bot prompts for phone/OTP every restart | `TELETHON_SESSION` path isn't persisted (e.g. ephemeral container filesystem) — mount a persistent volume. |
| `Missing required environment variable: ...` | Copy `.env.example` to `.env` and fill in the missing value. |
| `/setsource` accepted but nothing forwards | Run `/enable`, confirm `/status` shows `Forwarding: ENABLED`, and confirm the USER ACCOUNT is actually a member of the source channel. |
| Matches detected in `/test` but nothing arrives in target | Check `/logs` for `FORWARD_FAILED` entries — usually a permissions issue; ensure the USER ACCOUNT can post in the target channel. |
| `FloodWaitError` in logs | Telegram is rate-limiting; Telethon will wait automatically. Avoid restarting repeatedly. |
| Old/backlog messages appear on startup | Should never happen — the client only listens for `NewMessage` events after connecting; there is no history scan. If seen, file it as a bug. |
| Duplicate messages in target | Should not happen due to the SQLite unique constraint; check `/logs` for `DUPLICATE_SKIPPED` to confirm dedup is firing. |
| Non-admin can run commands | Should never happen — every handler checks `ADMIN_USER_ID`. Verify `.env` has the correct numeric id (not a username). |

---

## 8. Project structure

```
toss-forward-bot/
├── main.py            # entrypoint — wires everything together
├── config.py           # env var loading
├── database.py          # SQLite: dedup, settings, stats, local logs
├── matcher.py           # strict multi-layer pattern engine (core logic)
├── telegram_user.py       # Telethon USER ACCOUNT client (monitors source)
├── telegram_bot.py        # BOT ACCOUNT wrapper (admin commands)
├── forwarder.py          # final safety gate + forward call
├── handlers.py           # admin command implementations
├── logger.py            # local logging setup
├── test_matcher.py        # unit tests for the matcher
├── requirements.txt
├── .env.example
├── Procfile
├── Dockerfile
└── README.md
```
