# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AutoPollVoteBot is a Telegram bot that automatically votes in forum polls based on configurable event schedules. It uses the Pyrogram library to interact with Telegram's API and monitors specific forum topics for new polls. Multiple users are supported concurrently via a SQLite-backed database and an explicit `asyncio` orchestration loop.

## Running the Bot

The project uses venv for a Python environment.

```bash
# Run locally
python app.py

# Build Docker image
docker build -t autopollvotebot .

# Run with Docker
docker run -d --name autopollvotebot -p 8080:8080 --env-file .env autopollvotebot
```

## Configuration System

The bot uses a Jinja2-based configuration system for shared config, plus a SQLite database for per-user config.

1. **config.yaml.j2**: Template file that reads shared environment variables
2. **Environment variables**: Set in `.env` file or passed directly
3. **Config loading**: `src/config.py` renders the template and parses it into `CommonConfig` dataclass

### Shared environment variables (common to all users)
- `PYROGRAM_API_ID`, `PYROGRAM_API_HASH`: Telegram API credentials
- `GROUP_CHAT_ID`: Target forum chat ID to monitor
- `GROUP_VOTE_OPTION`: Text of poll option to vote for (e.g., "Go!")
- `DATABASE_PATH`: Path to the SQLite database (default: `users.db`)
- `PORT`, `PING_URL`: Health check server settings
- `ENABLE_SELF_PING`: Enable periodic self-ping (default: false)
- `BOT_TOKEN`: Telegram Bot token for the manager bot (**required** — startup fails without it)

### Per-user configuration (stored in SQLite `users` table)
- `session_string`: Pyrogram session string for this user
- `session_name`: Unique name for this Pyrogram client
- `event_schedule`: DSL string defining when to vote (format: `Type day [HH:MM]; ...`)
- `vote_delay_seconds`: Delay before voting (default: 5)
- `enabled`: Whether autovoting is active for this user (0/1); mutable at runtime via `/enable`/`/disable`
- `telegram_user_id`: Telegram user ID, populated automatically on first startup via `get_me()` backfill

## Architecture

### Core Components

1. **AutoPollVoterBot** (`src/auto_poll_voter_bot.py`):
   - One instance per user; wraps a `pyrogram.Client`
   - Constructor: `(common: CommonConfig, user: UserRecord, event_info_parser, manager: AutoPollManagerBot)`
   - Parses the user's `event_schedule` DSL into `ScheduledEvent` objects at init
   - Core logic: `on_forum_message()` -> `vote_in_thread_poll()`
   - Validates events via `topic_name_matches()` before voting
   - Reads `self.user.enabled` at the top of `on_forum_message`; returns early if False

2. **EventInfoParser** (`src/event_info_parser.py`):
   - Parses forum topic names to extract event metadata
   - Expected format: `Type YYYY-MM-DD, Day, HH:MM-HH:MM`
   - Example: "Game 2025-09-30, Tue, 20:00-22:00"
   - Handles flexible time formats (8, 08:30, 930, 20.30, etc.)

3. **Schedule DSL** (`src/schedule_dsl.py`):
   - Parses schedule configuration strings
   - Format: `Type day [HH:MM]; Type day [HH:MM]; ...`
   - Example: `Game wed 20:30; Game sat 11:00; Training tue`
   - Returns a list of dicts (converted to `ScheduledEvent` objects by `AutoPollVoterBot`)

4. **Health Check Server** (`src/health_check.py`):
   - Flask server running on separate thread
   - Endpoint: `GET /health` (returns 200 OK or 503 unhealthy)
   - Multi-client: use `register_client(client)` for each bot; reports all N clients
   - Optional self-ping functionality (disabled by default, enable via `ENABLE_SELF_PING`)

5. **AutoPollManagerBot** (`src/auto_poll_manager_bot.py`):
   - Telegram bot that lets registered users manage their autovoting config via DM commands
   - Commands: `/enable`, `/disable`, `/status` (DM-only, registered users only)
   - Maintains a registry of `VoterHandle` objects keyed by Telegram user ID
   - Voter bots use `manager.app.send_message(...)` directly for vote notifications
   - Replaces the old REST-based `AutoPollNotifierBot`

6. **VoterHandle** (`src/auto_poll_manager_bot.py`):
   - Two-field dataclass: `user: UserRecord`, `client: pyrogram.Client`
   - The `user` field is the **same `UserRecord` instance** shared by reference with the voter bot
   - This means `/enable`/`/disable` writing `handle.user.enabled` is immediately visible in the voter's guard

7. **Database** (`src/database.py`):
   - `apply_migrations(db_path)`: applies pending yoyo migrations at startup

8. **User Repository** (`src/user_repository.py`):
   - `UserRecord` dataclass: `id`, `session_name`, `session_string`, `event_schedule`, `vote_delay_seconds`, `telegram_user_id: Optional[int]`, `enabled: bool`
   - `UserRecord.enabled` is **mutable runtime state**: hydrated from DB at startup (`True` for enabled rows), then mutated in-place by manager commands — no locks needed (single event loop)
   - `UserRepository(db_path)` class: `get_enabled_users()`, `set_telegram_user_id(user_id, telegram_user_id)`, `set_enabled(telegram_user_id, enabled) -> int`

### Startup Sequence (`app.py`)

1. Load `CommonConfig` from `config.yaml.j2`
2. Apply DB migrations (`apply_migrations`)
3. Load enabled users (`repo.get_enabled_users()`); exit with error if none found
4. Start `HealthCheckServer`
5. Construct `AutoPollManagerBot` (requires `BOT_TOKEN`)
6. Build one `AutoPollVoterBot` per user, passing `manager=manager_bot`
7. Register all voter clients and the manager client with `health_server`
8. `asyncio.run(main(...))` which:
   - Starts each voter client (`await bot.app.start()`)
   - For each voter: calls `get_me()`, upserts `telegram_user_id` in DB, mutates `bot.user.telegram_user_id`, registers `VoterHandle` in manager
   - Starts manager client (`await manager_bot.app.start()`)
   - Sets health server to healthy
   - Blocks on `await asyncio.Event().wait()` until cancelled (SIGINT/KeyboardInterrupt)
   - `finally`: stops manager first, then each voter

### Voting Logic Flow

1. Bot receives new message in monitored forum
2. Guard: if `self.user.enabled` is False, return immediately
3. Filters for: correct chat + forum topic + poll message
4. Fetches topic name using `get_forum_topic()`
5. Parses topic name to extract event info
6. Validates:
   - Event date is in the future
   - Event matches user's configured schedule (type, day, optional start_time)
7. Checks if already voted (skip if yes)
8. Selects poll option matching `vote_option` config
9. Waits for user's configured delay, then votes
10. Sends notification via `self.manager.app.send_message(chat_id=telegram_user_id, text=..., parse_mode=ParseMode.HTML)`

### Configuration Rendering

The `yaml_renderer.py` module uses Jinja2 with a custom filter:
- `parse_schedule_dsl`: Converts DSL string to YAML-compatible structure (used historically; DSL is now in DB)
- Templates have access to `env` object containing all environment variables
- StrictUndefined mode ensures missing variables raise errors (including missing `BOT_TOKEN`)

## File Structure

```
app.py                          # Entry point
src/
  auto_poll_voter_bot.py        # Per-user voter client
  auto_poll_manager_bot.py      # Manager bot (commands + notifications)
  config.py                     # CommonConfig, ManagerBotConfig dataclasses
  database.py                   # Migration runner
  event_info_parser.py          # Topic name parser
  health_check.py               # Flask health endpoint
  schedule_dsl.py               # Schedule DSL parser
  user_repository.py            # UserRecord, UserRepository
  yaml_renderer.py              # Jinja2 template renderer
migrations/
  0001_create_users.sql
  0002_add_telegram_user_id.sql
tests/
  __init__.py
  conftest.py
  test_auto_poll_manager_bot.py
  test_auto_poll_voter_bot.py
  test_config.py
  test_user_repository.py
config.yaml.j2                  # Jinja2 config template
```

## Important Notes

- **Python environment**: use .venv to run python and its packages
- **Session strings**: Generated separately with `python generate_session.py` and stored in the DB
- **Forum-specific**: Bot only responds to messages in forum topics (not regular chats)
- **Future events only**: Bot will not vote on events dated today or in the past
- **Schedule matching**: If `start_time` is specified in schedule, event must match exactly; otherwise any time is accepted
- **BOT_TOKEN required**: Startup fails immediately if `BOT_TOKEN` is not set (Jinja2 StrictUndefined raises)
- **telegram_user_id backfill**: On first boot, each voter's `telegram_user_id` is populated automatically via `get_me()`; no manual entry needed
- **Shared UserRecord**: The voter and its `VoterHandle` in the manager hold the same `UserRecord` object by reference — `/enable`/`/disable` mutations are instantly visible to the voter guard
- **No `/ping` handler**: The old Saved-Messages `/ping → pong` flow has been removed; use `/status` instead
- **remember always update README.md and CLAUDE.md on functionality change**
