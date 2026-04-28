# AutoPollVoteBot

A Telegram bot that automatically votes in forum polls based on configurable schedules and event criteria. Supports
multiple users concurrently via a SQLite-backed user database.

## Features

- Monitors Telegram forum topics for new polls
- Automatically votes based on event schedules (type, day)
- Parses event information from topic names
- Only votes on future events that match configured schedules
- Multi-user support: run N Pyrogram clients concurrently
- Manager bot for per-user configuration via Telegram DM commands
- Per-event reminders: DMs each user before events they auto-voted for
- Add to Google Calendar: vote-notification DMs include an inline button that prefills the GCal app
- Health check endpoint reporting all client connection states
- Docker support

## Requirements

- Python 3.11+
- Telegram API credentials (API ID and API Hash)
- Telegram Bot token (`BOT_TOKEN`) for the manager/notification bot
- SQLite database with at least one enabled user row

## Installation

1. Clone the repository:

```bash
git clone https://github.com/olxmute/AutoPollVoteBot
cd AutoPollVoteBot
```

2. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

3. Set up environment variables (see Configuration section)

4. Create the database and add users (see Database section)

## Configuration

Create a `.env` file with the following variables:

```env
# Pyrogram API credentials (get from https://my.telegram.org)
PYROGRAM_API_ID=your_api_id
PYROGRAM_API_HASH=your_api_hash

# Group settings
GROUP_CHAT_ID=your_chat_id
GROUP_VOTE_OPTION=Go!

# Database
DATABASE_PATH=users.db  # optional, defaults to users.db

# Server settings
PORT=8080

# Manager bot (required)
BOT_TOKEN=your_bot_token_here  # Required: Telegram Bot token for the manager bot
```

### Environment Variables

| Variable            | Required | Default    | Description                                        |
|---------------------|----------|------------|----------------------------------------------------|
| `PYROGRAM_API_ID`   | Yes      | -          | Telegram API ID (get from https://my.telegram.org) |
| `PYROGRAM_API_HASH` | Yes      | -          | Telegram API Hash                                  |
| `GROUP_CHAT_ID`     | Yes      | -          | Chat ID of the forum/group to monitor              |
| `GROUP_VOTE_OPTION` | No       | `Go!`      | Text of the poll option to vote for                |
| `DATABASE_PATH`     | No       | `users.db` | Path to the SQLite database file                   |
| `PORT`              | No       | `8080`     | Port for the health check server                   |
| `BOT_TOKEN`         | **Yes**  | -          | Telegram Bot token for the manager bot (required)  |

> **Note:** `BOT_TOKEN` is **required**. Startup will fail immediately if it is not set.

## Database

The bot uses a SQLite database to store per-user configuration. The database and schema are created automatically at
startup via yoyo migrations.

### Schema

```sql
CREATE TABLE users
(
   id                 INTEGER PRIMARY KEY AUTOINCREMENT,
   session_name       TEXT UNIQUE NOT NULL,
   session_string     TEXT        NOT NULL,
   event_schedule     TEXT        NOT NULL, -- DSL string, e.g. "Game wed; Training tue"
   vote_delay_seconds INTEGER     NOT NULL DEFAULT 5,
   enabled            BOOLEAN     NOT NULL DEFAULT 1,
   telegram_user_id   INTEGER              -- populated automatically on first startup
);

-- Partial unique index: allows multiple NULLs but prevents two rows sharing the same Telegram ID
CREATE UNIQUE INDEX idx_users_telegram_user_id
    ON users(telegram_user_id) WHERE telegram_user_id IS NOT NULL;
```

The `telegram_user_id` column is populated automatically on first startup: each enabled voter calls `get_me()` to
discover its Telegram account and upserts the value. No manual data entry is needed.

Migration `0003_add_reminders` adds two columns to `users` and creates the `reminders` table:

```sql
ALTER TABLE users ADD COLUMN reminders_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN reminder_lead_hours INTEGER NOT NULL DEFAULT 27;

CREATE TABLE reminders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    chat_id          INTEGER NOT NULL,
    topic_id         INTEGER NOT NULL,
    poll_message_id  INTEGER NOT NULL,
    topic_name       TEXT    NOT NULL,
    event_datetime   TEXT    NOT NULL, -- ISO-8601 UTC
    reminded_at      TEXT,             -- NULL = not yet sent
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (telegram_user_id, chat_id, topic_id)
);
```

New users default to reminders on (`reminders_enabled = 1`); each user can opt out via `/reminders`.

### Adding users manually

```bash
sqlite3 users.db "INSERT INTO users (session_name, session_string, event_schedule, vote_delay_seconds) VALUES ('user1', 'your_session_string', 'Game wed; Training tue', 5);"
```

- `session_name` — unique identifier for the Pyrogram client (arbitrary string)
- `session_string` — Pyrogram session string (generate with `python generate_session.py`)
- `event_schedule` — schedule DSL string (see Schedule Configuration below)
- `vote_delay_seconds` — seconds to wait before casting the vote
- `enabled` — set to `0` to disable a user without deleting the row

### Schedule Configuration

The `event_schedule` field uses a DSL format:

```
Game wed; Game sat; Training tue
```

This configures the bot to vote on:

- Game events on Wednesday
- Game events on Saturday
- Training events on Tuesday

## Manager Bot Commands

Each registered user can DM the bot to manage their autovoting configuration:

| Command       | Description                                                   |
|---------------|---------------------------------------------------------------|
| `/enable`     | Resume autovoting (sets `enabled = 1` in DB)                  |
| `/disable`    | Pause autovoting (sets `enabled = 0` in DB)                   |
| `/status`     | Report voter client liveness and current voting enabled state |
| `/schedule`   | Open the inline-keyboard schedule editor (see below)          |
| `/reminders`  | Open the inline-keyboard reminders editor (see below)         |

Commands are **DM-only** and only work for users registered in the database. Unregistered Telegram accounts receive:
`"You're not registered. Contact the administrator."`

> **Note:** The old Saved-Messages `/ping → pong` liveness flow has been removed. Use `/status` instead.

### Example `/status` response

```
Voter: up
Voting: enabled
```

### Schedule Editor (`/schedule`)

The `/schedule` command opens an inline keyboard that lets you view, add, and remove schedule entries without needing DB access.

**Main screen** — lists all current entries and shows action buttons:

```
Your schedule:
1. Game wed
2. Training tue

[➕ Add]  [❌ Remove]  [✖ Close]
```

- If the schedule is empty, only `[➕ Add]` and `[✖ Close]` are shown.
- If the DB entry is malformed, an error message is shown with only `[✖ Close]`.

**Add flow:**

1. Tap `[➕ Add]` → choose event type: `[🏐 Game]` or `[🏃 Training]`
2. Choose a weekday — only days not already scheduled for the chosen type are shown (so each `(type, day)` pair stays
   unique). If every day is already taken for that type, a message explains there's nothing left to add.
3. Entry is appended
4. Main screen is redrawn with the new entry

**Remove flow:**

1. Tap `[❌ Remove]` → each existing entry appears as a button with its details
2. Tap any entry to delete it immediately (no confirmation prompt)
3. The remove list stays open after deletion to support bulk cleanup; tap `[⬅ Back]` to return to the main screen

**Live propagation:** changes take effect on the next poll without a bot restart — the voter re-parses the schedule from the database on every incoming forum message.

> **Note:** If the bot restarts while you have a `/schedule` keyboard open, the inline buttons become stale. Simply re-send `/schedule` to get a fresh keyboard.

### Reminders (`/reminders`)

The `/reminders` command opens an inline keyboard for managing per-event reminder settings.

**Main screen:**

```
Reminders: OFF
27 hours before the event

[Enable]  [Change timing]
[✖ Close]
```

- **Reminders ON/OFF** — tap `[Disable]` or `[Enable]` to toggle. When OFF, no reminders are sent for pending events,
  but rows are retained. Toggling back to ON before the event fires will send the reminder at the next poller tick.
- **Reminder timing** — how many hours before an event to send the reminder. Default: **27 hours**, which is also the minimum the UI accepts (cancellation cutoff is 26h before the event, so the reminder must fire earlier); maximum is **720 hours** (30 days). Tap `[Change timing]`, then reply to the bot's prompt with a whole number between 27 and 720 (e.g. `36`).

**Key facts:**

- Reminders are only sent for events the autovoter voted on. Votes cast manually from another Telegram client are
  **not** covered.
- The poller runs every **5 minutes**. Reminders may fire up to 5 minutes late.
- Rows for events that have passed more than **7 days** ago are pruned automatically on each poller tick.
- The revocation check is performed immediately before sending: if your vote was retracted, the reminder is silently
  skipped (but the row is marked as processed so it won't retry).

> **Note:** If the bot restarts while you have a `/reminders` keyboard open, the inline buttons become stale. Simply
> re-send `/reminders` to get a fresh keyboard.

## Usage

### Running Locally

```bash
python app.py
```

### Running with Docker

1. Build the image:

```bash
docker build -t autopollvotebot .
```

2. Run the container:

```bash
docker run -d \
  --name autopollvotebot \
  -p 8080:8080 \
  --env-file .env \
  -v $(pwd)/users.db:/app/users.db \
  autopollvotebot
```

## How It Works

1. At startup, pending DB migrations are applied and all enabled users are loaded
2. One Pyrogram voter client is started per user
3. Each voter calls `get_me()` to discover its Telegram user ID, which is stored in the DB (`telegram_user_id`)
4. The manager bot client starts after all voters are registered
5. When a poll is posted in a monitored forum topic, each voter independently:
   - Checks if autovoting is enabled (can be toggled via `/enable`/`/disable`)
   - Parses the topic name to extract event information (type, date, time)
   - Checks if the event date is in the future
   - Verifies if the event matches the user's configured schedule
   - Automatically votes for the configured option (e.g., "Go!")
   - Skips voting if already voted
   - Sends a notification message via the manager bot with event details and an inline 'Add to Google Calendar' button
   - On a successful vote, records a row in the `reminders` table for later delivery

6. A background poller runs every **5 minutes** inside the manager bot:
   - Fetches all un-sent reminder rows whose event is still in the future and whose user has reminders enabled
   - Applies the per-user lead-time filter in Python (event ≤ `reminder_lead_hours` hours away)
   - Performs a revocation check (re-fetches the poll from Telegram); if the user's vote was retracted, marks the row
     as processed without sending
   - Sends the reminder DM via the manager bot; marks the row as sent on success
   - Prunes rows for events that passed more than 7 days ago (runs in `finally`, so cleanup is never skipped)

7. Health check endpoint is available at `http://localhost:8080/health` and reports the connection state of all
   registered clients (voters + manager bot)
