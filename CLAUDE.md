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
- `PORT`: Health check server settings
- `BOT_TOKEN`: Telegram Bot token for the manager bot

### Per-user configuration (stored in SQLite `users` table)
- `session_string`: Pyrogram session string for this user
- `session_name`: Unique name for this Pyrogram client
- `event_schedule`: DSL string defining when to vote (format: `Type day; ...`)
- `vote_delay_seconds`: Delay before voting (default: 5)
- `enabled`: Whether autovoting is active for this user (0/1); mutable at runtime via `/enable`/`/disable`
- `telegram_user_id`: Telegram user ID, populated automatically on first startup via `get_me()` backfill
- `reminders_enabled`: Whether reminder DMs are active for this user (1/0, default 1); mutable via `/reminders`
- `reminder_lead_hours`: How many hours before the event to fire the reminder (default 27); mutable via `/reminders`

## Architecture

### Core Components

1. **AutoPollVoterBot** (`src/auto_poll_voter_bot.py`):
   - One instance per user; wraps a `pyrogram.Client`
   - Constructor: `(common: CommonConfig, user: UserRecord, event_info_parser, manager: AutoPollManagerBot, reminder_discovery: Optional[ReminderDiscovery] = None)`
   - Does **not** cache parsed schedule — re-parses `self.user.event_schedule` on every call to `topic_name_matches()`; schedule edits via ScheduleEditor propagate automatically through the shared `UserRecord` without a restart
   - Core logic: `on_forum_message()` -> `vote_in_thread_poll()`
   - Validates events via `topic_name_matches()` before voting
   - Reads `self.user.enabled` at the top of `on_forum_message`; returns early if False
   - After a successful `vote_poll(...)`, calls `await self.reminder_discovery.record_from_vote(...)` if `reminder_discovery` is not None

2. **EventInfoParser** (`src/event_info_parser.py`):
   - Parses forum topic names to extract event metadata
   - Expected format: `Type YYYY-MM-DD, Day, HH:MM-HH:MM`
   - Example: "Game 2025-09-30, Tue, 20:00-22:00"
   - Handles flexible time formats (8, 08:30, 930, 20.30, etc.)

3. **Schedule DSL** (`src/schedule_dsl.py`):
   - Parses schedule configuration strings
   - Format: `Type day; Type day; ...`
   - Example: `Game wed; Game sat; Training tue`
   - Returns a list of dicts (converted to `ScheduledEvent` objects by `AutoPollVoterBot`)
   - **Strict 2-token validation**: each entry must contain exactly two whitespace-separated tokens (`Type day`); entries with != 2 tokens (including 1-token or 3-token entries like `Game wed 20:30`) raise `ValueError`

4. **Health Check Server** (`src/health_check.py`):
   - Flask server running on separate thread
   - Endpoint: `GET /health` (returns 200 OK or 503 unhealthy)
   - Multi-client: use `register_client(client)` for each bot; reports all N clients

5. **AutoPollManagerBot** (`src/auto_poll_manager_bot.py`):
   - Telegram bot that lets registered users manage their autovoting config via DM commands
   - Commands: `/enable`, `/disable`, `/status`, `/schedule`, `/reminders` (DM-only, registered users only)
   - Maintains a registry of `VoterHandle` objects keyed by Telegram user ID
   - Voter bots use `manager.app.send_message(...)` directly for vote notifications
   - Wires in `ScheduleEditor` on construction: `self._schedule_editor = ScheduleEditor(repo, self._handles)`
   - Wires in `RemindersEditor` and `ReminderScheduler` on construction
   - `start_scheduler()` / `stop_scheduler()` — thin wrappers over `ReminderScheduler.start()` / `.stop()`

6. **VoterHandle** (`src/voter_handle.py`):
   - Two-field dataclass: `user: UserRecord`, `client: pyrogram.Client`
   - Extracted to its own module to break the circular import between manager and voter
   - The `user` field is the **same `UserRecord` instance** shared by reference with the voter bot
   - This means `/enable`/`/disable` and schedule mutations via ScheduleEditor are immediately visible in the voter

7. **ScheduleEditor** (`src/schedule_editor.py`):
   - Owns the entire `/schedule` inline-keyboard UI: command handler + all `sch:*` callback routes
   - Constructor: `(repo: UserRepository, handles: Dict[int, VoterHandle])`; the dict is passed by reference so newly-registered voters are visible immediately
   - `register_handlers(app)` registers one `MessageHandler` (for `/schedule`) and one `CallbackQueryHandler` (for `^sch:` callbacks)
   - Add flow: `/schedule` → `[Add]` → type picker (Game / Training) → day picker (only days not already scheduled for
     the chosen type — enforces `(type, day)` uniqueness; shows "All days are already scheduled for {Type}." + Back when
     nothing is left) → entry appended, DSL saved, main screen redrawn
   - Remove flow: `[Remove]` → numbered list of current entries → tap any → entry removed, list redrawn (stays on remove screen)
   - `Close` dismisses the keyboard; `Back` always returns one level up
   - Persists via `repo.set_event_schedule(telegram_user_id, new_dsl)` and immediately mutates `handle.user.event_schedule`; mutation propagates to voter via the shared `UserRecord` reference
   - Callback data scheme (stateless, ≤64 bytes): `sch:main`, `sch:close`, `sch:add`, `sch:add:t:<Type>`, `sch:add:d:<Type>:<day>`, `sch:rm`, `sch:rm:<index>`

8. **RemindersEditor** (`src/reminders_editor.py`):
   - Owns the entire `/reminders` inline-keyboard UI: command handler + all `rem:*` callback routes + free-text lead-time reply handler
   - Constructor: `(repo: UserRepository, handles: Dict[int, VoterHandle])`; same shape as `ScheduleEditor`
   - `register_handlers(app)` registers one `MessageHandler` (for `/reminders`), one `CallbackQueryHandler` (for `^rem:` callbacks), and one `MessageHandler` for the free-text lead-time reply (`filters.private & filters.reply & filters.text`)
   - Main screen: text `Reminders: ON|OFF\nLead time: {hours} hours`; buttons `[Disable]/[Enable]` + `[Lead time]` + `[Close]`
   - `rem:toggle` — flips `reminders_enabled`, persists via `repo.set_reminders_enabled`, mutates `handle.user.reminders_enabled`, re-renders
   - `rem:lead` — sends a ForceReply prompt, stashes `self._pending_lead_input[from_user.id] = prompt_message_id`; reply handler validates positive integer, calls `repo.set_reminder_lead_hours`, mutates `handle.user.reminder_lead_hours`, clears pending, re-renders main screen
   - DM-only + registered-user gating identical to `ScheduleEditor`
   - Callback data scheme (stateless, ≤64 bytes): `rem:main`, `rem:close`, `rem:toggle`, `rem:lead`

9. **ReminderDiscovery** (`src/reminder_discovery.py`):
   - Module-level helpers: `prague_datetime_to_utc(event_date, event_time) -> datetime` (Prague/CET/CEST → UTC, DST-aware via `zoneinfo`); `chosen_option_is_go(poll, vote_option_text) -> bool` (pure substring match, NO fallback to index 0 — distinct from `AutoPollVoterBot.choose_option`)
   - Class `ReminderDiscovery(user: UserRecord, event_info_parser: EventInfoParser, reminders: ReminderRepository)` — one instance per voter, constructed in `app.py`
   - `async record_from_vote(topic_name, chat_id, topic_id, poll_message_id)` — called by the voter after a successful vote; skips silently (with warning log) if `telegram_user_id is None` or topic_name is unparseable; no `reminders_enabled` or lead-time check at insert (both handled by the poller)

10. **ReminderRepository** (`src/reminder_repository.py`):
    - `DueReminder` dataclass: `id, telegram_user_id, chat_id, topic_id, poll_message_id, topic_name, event_datetime, reminder_lead_hours`
    - `ReminderRepository(db_path: str)`: `upsert(...)` (INSERT … ON CONFLICT DO UPDATE WHERE reminded_at IS NULL — frozen already-sent rows), `fetch_due() -> list[DueReminder]` (SQL gates on `reminded_at IS NULL`, `reminders_enabled = 1`, `event_datetime > now`; lead-hours returned for Python-side filter), `mark_reminded(id) -> int`, `delete_old(retention_days=7) -> int`

11. **ReminderScheduler** (`src/reminder_scheduler.py`):
    - Class `ReminderScheduler(common: CommonConfig, repo: ReminderRepository, handles: Dict[int, VoterHandle], manager_app: pyrogram.Client)`
    - Constants: `POLL_INTERVAL_SECONDS = 300`, `RETENTION_DAYS = 7`
    - `start()` — spawns a single `asyncio.Task` for the poller loop
    - `stop()` — sets `_stopping`, cancels and awaits the task; second call is a no-op
    - `_tick()`: outer `try/finally` ensures `repo.delete_old(RETENTION_DAYS)` runs even if the row loop raises; for each due row (after Python-side lead-time filter): look up `VoterHandle` (skip if missing), revocation check via `handle.client.get_messages` + `chosen_option_is_go`, send via `manager_app.send_message` + mark reminded; send failures leave `reminded_at` NULL for natural retry

12. **Database** (`src/database.py`):
    - `apply_migrations(db_path)`: applies pending yoyo migrations at startup
    - Migration `0003_add_reminders`: adds `reminders_enabled` and `reminder_lead_hours` to `users`, creates `reminders` table with UNIQUE `(telegram_user_id, chat_id, topic_id)` and index on `(reminded_at, event_datetime)`

13. **User Repository** (`src/user_repository.py`):
    - `UserRecord` holds **mutable runtime state**: `enabled`, `event_schedule`, `reminders_enabled`, and `reminder_lead_hours` are mutated in-place by manager commands — no locks needed (single event loop), and the voter/poller pick up changes on the next poll
    - `UserRepository(db_path)`: `get_enabled_users()`, `set_telegram_user_id(user_id, telegram_user_id)`, `set_enabled(telegram_user_id, enabled) -> int`, `set_event_schedule(telegram_user_id, dsl) -> int`, `set_reminders_enabled(telegram_user_id, enabled) -> int`, `set_reminder_lead_hours(telegram_user_id, hours) -> int`

### Startup Sequence (`app.py`)

Module scope (synchronous, pre-loop — only non-async setup):
1. Load `CommonConfig` from `config.yaml.j2`
2. Apply DB migrations (`apply_migrations`)
3. Construct `UserRepository`
4. Start `HealthCheckServer` (Flask on its own thread; loop-independent)
5. `asyncio.run(main(common, repo, health_server))`

Inside `main()` (all Pyrogram construction and async work happens here, so asyncio objects bind to the running loop —
see note below):

1. Construct `ReminderRepository(common.database.path)`
2. Construct `AutoPollManagerBot(common, repo, reminder_repo)` (requires `BOT_TOKEN`; wires `RemindersEditor` + `ReminderScheduler` internally)
3. Load enabled users via `repo.get_enabled_users()`; raise `RuntimeError` if none (propagates to the module-scope
   `except Exception`, which flips health to unhealthy)
4. For each user: construct `ReminderDiscovery(user, event_info_parser, reminder_repo)`, then `AutoPollVoterBot(..., reminder_discovery=...)`
5. Register all voter clients and the manager client with `health_server`
6. Start each voter client (`await bot.app.start()`); track started voters for safe cleanup
7. For each voter: `get_me()`, upsert `telegram_user_id` in DB, mutate `bot.user.telegram_user_id`, register
   `VoterHandle` in manager
8. Start manager client (`await manager_bot.app.start()`)
9. `await manager_bot.start_scheduler()` — starts the 5-minute reminder poller task
10. Set health server to healthy
11. Block on `await asyncio.Event().wait()` until cancelled (SIGINT/KeyboardInterrupt)
12. `finally`: `await manager_bot.stop_scheduler()` first, then stop manager client, then each started voter in reverse order

**Why Pyrogram clients must be constructed inside `main()`**: `pyrogram.Client.__init__` binds internal asyncio
primitives to whatever loop `asyncio.get_event_loop()` resolves at construction time. Constructing clients at module
scope (before `asyncio.run()`) binds them to an implicit default loop; `asyncio.run()` then creates a **different**
loop, causing `got Future ... attached to a different loop` at the first `await`. Do not "simplify" this by lifting
construction back above `asyncio.run()`.

### Voting Logic Flow

1. Enabled guard: return immediately if `self.user.enabled` is False (checked before any work)
2. Pyrogram filters (chat + forum topic + poll) → fetch topic name → parse into `EventInfo`
3. Validate: event date strictly in the future AND matches schedule (type, day)
4. Skip if poll already has `chosen_option_id`; otherwise pick option matching `vote_option`
5. `await asyncio.sleep(vote_delay_seconds)` → `vote_poll(...)` → `await self.reminder_discovery.record_from_vote(...)` inserts/upserts a row in the `reminders` table → notify user via `self.manager.app.send_message(...)` with `ParseMode.HTML`

### Configuration Rendering

The `yaml_renderer.py` module uses Jinja2 with a custom filter:
- `parse_schedule_dsl`: Converts DSL string to YAML-compatible structure; exposed as a Jinja2 filter for legacy template compatibility; also called on every poll arrival in the voter (stateless re-parse replaces the old startup cache) and on every schedule save in `ScheduleEditor`
- `serialize_schedule_dsl` (in `src/schedule_dsl.py`): inverse of the parser; called by `ScheduleEditor._save` to write the updated DSL back to DB
- Templates have access to `env` object containing all environment variables
- StrictUndefined mode ensures missing variables raise errors

## File Structure

```
app.py                  # Entry point
src/                    # See Architecture → Core Components for per-module responsibilities
migrations/             # yoyo SQL migrations, numbered 0001_, 0002_, ...
tests/                  # pytest suite (one test_*.py per src module)
config.yaml.j2          # Jinja2 config template
```

## Important Notes

- **Python environment**: use .venv to run python and its packages
- **Session strings**: Generated separately with `python generate_session.py` and stored in the DB
- **Forum-specific**: Bot only responds to messages in forum topics (not regular chats)
- **Unused-but-required parameters**: prefix with `_` (e.g., `_client`) when a parameter is mandated by a framework
  contract (such as Pyrogram handler callbacks, which are invoked as `callback(client, update)`) but the body doesn't
  use it
- **remember always update README.md and CLAUDE.md on functionality change**
