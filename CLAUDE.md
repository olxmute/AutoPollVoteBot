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

## Testing

```bash
# Full suite (from project root, .venv activated)
pytest

# Single file
pytest tests/test_reminder_scheduler.py
```

- Tests use the `tmp_db` fixture in `tests/conftest.py`, which creates a temp SQLite DB with all yoyo migrations applied, one per test.
- `tests/conftest.py` installs an explicit asyncio event loop before any `pyrogram` import. Required for Python 3.14 (implicit "current loop" was removed). Don't delete it.

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
- `reminders_enabled`: Whether reminder DMs are active for this user (0/1, default 1); mutable via `/reminders` — new users are opted in and can disable via `/reminders`
- `reminder_lead_hours`: How many hours before the event to fire the reminder (default 27); mutable via `/reminders`; the UI enforces a minimum of 27 (cancellation cutoff is 26h before the event, so the reminder must fire earlier) and a maximum of 720 (30 days)

## Architecture

### Core Components

One file per role in `src/`; read the file for API detail. Non-obvious invariants live in "Design Invariants" below.

| Module | Role |
|---|---|
| `auto_poll_voter_bot.py` | One client per enabled user; listens for forum polls, votes, records reminder. |
| `google_calendar_url.py` | Pure URL builder for the "Add to Google Calendar" inline button on vote notifications. |
| `event_info_parser.py` | Parses topic names like `"Game 2025-09-30, Tue, 20:00-22:00"`. |
| `auto_poll_manager_bot.py` | Manager bot: `/enable`, `/disable`, `/status`; hosts the editors and scheduler. Registry of `VoterHandle`. |
| `voter_handle.py` | `{user: UserRecord, client: Client}` dataclass. Extracted to break the manager↔voter circular import. |
| `schedule_editor.py` | Owns `/schedule` UI (inline keyboard, `sch:*` callbacks). |
| `reminders_editor.py` | Owns `/reminders` UI (inline keyboard, `rem:*` callbacks, ForceReply for lead-time). |
| `reminder_discovery.py` | Called by voter after a successful vote; inserts a reminder row. Helpers: `prague_datetime_to_utc`, `chosen_option_is_go`. |
| `reminder_repository.py` | SQLite store for `reminders` rows. `upsert`, `fetch_due`, `mark_reminded`, `delete_old`. |
| `reminder_scheduler.py` | Background poller (5-min tick) that sends due reminders and prunes old rows. |
| `user_repository.py` | SQLite store for `users`. `UserRecord` is shared live with the voter. |
| `schedule_dsl.py` | Parses/serializes `"Game wed; Training tue"`. Strict 2-token entries — 1- or 3-token lines raise `ValueError`. |
| `config.py` / `yaml_renderer.py` | Loads `config.yaml.j2` → `CommonConfig`. |
| `health_check.py` | Flask `/health` on its own thread; `register_client(client)` per bot. |
| `database.py` | `apply_migrations(db_path)` — runs yoyo migrations in `migrations/`. |

### Design Invariants (the stuff that will bite you)

- **Shared `UserRecord`.** The voter, manager registry, and editors all hold the *same instance*. `/enable`, `/disable`, schedule/reminder edits mutate it in place — picked up on the next poll, no restart. Don't copy or re-fetch.
- **Stateless schedule re-parse.** `matches_schedule()` re-parses `self.user.event_schedule` on every incoming poll. There is no startup cache; that's deliberate.
- **Reminder discovery writes unconditionally.** `record_from_vote` does *not* check `reminders_enabled` or lead hours. Those are the poller's job — so the user can flip reminders back on later and pending rows still fire.
- **`chosen_option_is_go` is strict.** No fallback to option 0 (unlike `AutoPollVoterBot.choose_option`). Revocation check must not falsely pass.
- **`upsert` freezes sent rows** via `WHERE reminded_at IS NULL` — re-voting won't re-fire an already-sent reminder.
- **`_tick` uses `try/finally`** so `delete_old()` runs even if the row loop raises. Don't move it.
- **Poller ticks once before the first sleep** so overdue reminders go out immediately at startup.
- **Pyrogram clients must be constructed inside `main()`**, not at module scope. `Client.__init__` binds to `asyncio.get_event_loop()` at construction; `asyncio.run()` creates a *different* loop and you'll hit `got Future ... attached to a different loop` on the first await. Already bit us once.
- **`VoterHandle` in its own module** to break the manager↔voter circular import. Don't inline it back.

### Startup Sequence (`app.py`)

Module scope (synchronous, pre-loop — only non-async setup):
1. Load `CommonConfig` from `config.yaml.j2`
2. Apply DB migrations (`apply_migrations`)
3. Construct `UserRepository`
4. Start `HealthCheckServer` (Flask on its own thread; loop-independent)
5. `asyncio.run(main(common, repo, health_server))`

Inside `main()` (all Pyrogram construction and async work happens here — see the Pyrogram-loop invariant above):

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

### Voting Logic Flow

1. Enabled guard: return immediately if `self.user.enabled` is False (checked before any work)
2. Pyrogram filters (chat + forum topic + poll) → fetch topic name → parse into `EventInfo`
3. Validate: event date strictly in the future AND matches schedule (type, day)
4. Skip if poll already has `chosen_option_id`; otherwise pick option matching `vote_option`
5. `await asyncio.sleep(vote_delay_seconds)` → `vote_poll(...)` → `await self.reminder_discovery.record_from_vote(...)` inserts/upserts a row in the `reminders` table → notify user via `self.manager.app.send_message(...)` with `ParseMode.HTML`. The notification carries an inline `URL` button to add the event to Google Calendar (built by `google_calendar_url.build_add_event_url`).

### Configuration Rendering

`yaml_renderer.py` renders `config.yaml.j2` with Jinja2 in StrictUndefined mode (missing env vars raise). Templates access env via the `env` object. `parse_schedule_dsl` is registered as a Jinja2 filter for legacy template compatibility.

## File Structure

```
app.py                  # Entry point
src/                    # See Architecture → Core Components for per-module responsibilities
migrations/             # yoyo SQL migrations, numbered 0001_, 0002_, ...
tests/                  # pytest suite (one test_*.py per src module)
config.yaml.j2          # Jinja2 config template
```

## Important Notes

- **Python 3.11+** required; use `.venv` to run python and its packages (`tests/conftest.py` has a Py 3.14 event-loop shim)
- **Session strings**: stored in the DB (`users.session_string`)
- **Forum-specific**: Bot only responds to messages in forum topics (not regular chats)
- **Unused-but-required parameters**: prefix with `_` (e.g., `_client`) when a parameter is mandated by a framework
  contract (such as Pyrogram handler callbacks, which are invoked as `callback(client, update)`) but the body doesn't
  use it
- **remember always update README.md and CLAUDE.md on functionality change**
