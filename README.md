# AutoPollVoteBot

A Telegram bot that automatically votes in forum polls based on configurable schedules and event criteria. Supports
multiple users concurrently via a SQLite-backed user database.

## Features

- Monitors Telegram forum topics for new polls
- Automatically votes based on event schedules (type, day, start time)
- Parses event information from topic names
- Only votes on future events that match configured schedules
- Multi-user support: run N Pyrogram clients concurrently
- Manager bot for per-user configuration via Telegram DM commands
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
PING_URL=http://localhost:8080  # or your server URL
PORT=8080
ENABLE_SELF_PING=false  # Set to true to enable periodic self-ping

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
| `PING_URL`          | No       | `""`       | URL for self-ping health checks                    |
| `ENABLE_SELF_PING`  | No       | `false`    | Enable periodic self-ping to keep service alive    |
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
   event_schedule     TEXT        NOT NULL, -- DSL string, e.g. "Game wed 20:30; Training tue"
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

### Adding users manually

```bash
sqlite3 users.db "INSERT INTO users (session_name, session_string, event_schedule, vote_delay_seconds) VALUES ('user1', 'your_session_string', 'Game wed 20:30; Training tue', 5);"
```

- `session_name` — unique identifier for the Pyrogram client (arbitrary string)
- `session_string` — Pyrogram session string (generate with `python generate_session.py`)
- `event_schedule` — schedule DSL string (see Schedule Configuration below)
- `vote_delay_seconds` — seconds to wait before casting the vote
- `enabled` — set to `0` to disable a user without deleting the row

### Schedule Configuration

The `event_schedule` field uses a DSL format:

```
Game wed; Game sat 11:00; Training tue
```

This configures the bot to vote on:

- Game events on Wednesday (at any time)
- Game events on Saturday at 11:00
- Training events on Tuesday (at any time)

## Manager Bot Commands

Each registered user can DM the bot to manage their autovoting configuration:

| Command    | Description                                                   |
|------------|---------------------------------------------------------------|
| `/enable`  | Resume autovoting (sets `enabled = 1` in DB)                  |
| `/disable` | Pause autovoting (sets `enabled = 0` in DB)                   |
| `/status`  | Report voter client liveness and current voting enabled state |

Commands are **DM-only** and only work for users registered in the database. Unregistered Telegram accounts receive:
`"You're not registered. Contact the administrator."`

> **Note:** The old Saved-Messages `/ping → pong` liveness flow has been removed. Use `/status` instead.

### Example `/status` response

```
Voter: up
Voting: enabled
```

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
   - Sends a notification message via the manager bot with event details

6. Health check endpoint is available at `http://localhost:8080/health` and reports the connection state of all
   registered clients (voters + manager bot)
