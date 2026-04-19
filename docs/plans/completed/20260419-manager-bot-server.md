# Manager Bot Server for User Config

## Overview

Add a Telegram bot server (same token as the existing REST-based notifier) that lets each registered user manage their autovoting configuration directly from Telegram. Three in-scope features:

1. **`/enable` and `/disable`** — toggle the existing `enabled` flag on the user's row; a disabled voter keeps running but stops acting on forum polls.
2. **`/status`** — probe the user's voter client and reply with its liveness (`up` / `down (reason)`) and the voting flag (`enabled` / `disabled`). Replaces the current Saved-Messages `/ping → pong` flow in `AutoPollVoterBot`.
3. **Vote notifications via Pyrogram bot client** — replace the `requests.post`-based `AutoPollNotifierBot` with a proper Pyrogram client that runs in the same process as the voters, so notifications and commands share one bot.

The manager bot joins the existing `pyrogram.compose(...)` loop alongside the voter clients, which is what makes cross-bot liveness probes cheap (in-process `await`).

## Context (from discovery)

- Entry point `app.py` already uses `compose([bot.app for bot in bots])` to run N voter clients.
- `AutoPollVoterBot` holds one `pyrogram.Client` per DB user; constructor takes `common, user, event_info_parser, notifier=None`.
- `AutoPollNotifierBot` (`src/auto_poll_notifier_bot.py`) is a thin `requests.post` wrapper — no long-running client, will be deleted.
- `users` table has `id, session_name, session_string, event_schedule, vote_delay_seconds, enabled` — no Telegram user ID yet.
- `UserRepository` today is module-level functions in `src/user_repository.py`; will be refactored to a class holding `db_path`.
- Config uses `dataclass-wizard` YAML loading via `src/config.py` + Jinja2 template `config.yaml.j2`.
- Migrations use `yoyo` with files in `migrations/`; runner in `src/database.py`.
- `requests` is still imported by `src/health_check.py` for self-ping, so it stays in `requirements.txt`.
- No `tests/` directory exists; pytest is not in `requirements.txt` yet.

## Development Approach

- **testing approach**: Regular (code first, then tests in the same task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting the next task** — no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run tests after each change
- maintain backward compatibility within the scope noted below

**Intentional breaking change** (user-approved): `BOT_TOKEN` becomes a **required** environment variable; startup fails fast if missing. The previous "optional notifier" behavior is removed.

## Testing Strategy

- **unit tests only** — no integration against live Telegram.
- pytest as the framework (add to `requirements.txt`); all tests under `tests/`.
- `UserRepository`: temp SQLite DB with migrations applied; cover backfill, set_enabled idempotency, unique-index violation surfacing.
- Command handlers: fake registry + fake `Message` object (simple stubs); assert side effects (flag flipped, DB written, exact reply text) and the unregistered-user path with the exact wording `"You're not registered. Contact the administrator."`.
- Voter guard: handler returns early when `enabled_flag.value` is False; proceeds when True.
- Notification path: voter calls `manager.send_message(...)` with correct chat_id + HTML mode; failure is logged and swallowed (does not propagate).
- No e2e tests (project has no UI).

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope
- keep plan in sync with actual work done

## Solution Overview

Single process, single event loop. `compose()` is replaced with an explicit `asyncio.run(main())` orchestration so we can interleave steps between "clients started" and "clients idle":

1. Construct each voter; voter stores its `UserRecord` as `self.user`.
2. Start every voter client.
3. For each voter, call `voter.app.get_me()` to discover the owning Telegram user ID; upsert via `UserRepository.set_telegram_user_id(...)`; mutate `voter.user.telegram_user_id` in place; build a `VoterHandle(user=voter.user, client=voter.app)` and register it in the manager's registry.
4. Start the manager bot (only after the registry is fully populated — otherwise a `/status` during the window would reply "not registered" to a user that is registered).
5. `await asyncio.Event().wait()` — block forever. On SIGINT/KeyboardInterrupt, `asyncio.run` cancels `main`, the `finally` clause stops all clients cleanly. (We avoid `from pyrogram import idle` since it's not a stable public symbol — a bare event wait plus KeyboardInterrupt handling is sufficient and cross-platform.)
6. Stop all clients cleanly (manager first, then voters).

The enabled state lives directly on `UserRecord.enabled` (a mutable `bool` field). The voter (`self.user.enabled`) and the manager's `VoterHandle.user.enabled` share the SAME `UserRecord` instance by reference — single event loop means no locks needed. `VoterHandle` is a two-field dataclass: `user: UserRecord`, `client: pyrogram.Client`.

The manager bot exposes `async send_message(chat_id, text, parse_mode=...)` for voters to use directly — replacing the REST path in `AutoPollNotifierBot`. The voter gets `manager: AutoPollManagerBot` as a required constructor argument (no more `Optional`).

## Technical Details

### Database schema

Migration `0002_add_telegram_user_id.sql`:

```sql
-- depends: 0001_create_users

ALTER TABLE users ADD COLUMN telegram_user_id INTEGER;
CREATE UNIQUE INDEX idx_users_telegram_user_id
    ON users(telegram_user_id) WHERE telegram_user_id IS NOT NULL;
```

The partial unique index allows multiple NULLs (valid state before first-boot backfill) but forbids two rows ever sharing a Telegram ID (config error if it happens).

### `UserRecord` and `UserRepository`

```python
@dataclass
class UserRecord:
    id: int
    session_name: str
    session_string: str
    event_schedule: str
    vote_delay_seconds: int
    telegram_user_id: Optional[int]  # NEW — backfilled at startup
    enabled: bool                    # NEW — mutable runtime state, hydrated from DB

class UserRepository:
    def __init__(self, db_path: str): ...
    def get_enabled_users(self) -> List[UserRecord]: ...  # returns rows where enabled=1; hydrates .enabled=True
    def set_telegram_user_id(self, user_id: int, telegram_user_id: int) -> None: ...
    def set_enabled(self, telegram_user_id: int, enabled: bool) -> int: ...  # returns rows affected
```

`UserRecord.enabled` does double duty: at startup it's hydrated from the DB; at runtime `/enable`/`/disable` mutate it in place (same object reference shared by voter and VoterHandle). This is deliberately simpler than introducing a separate flag type — the instance is already mutable, already shared, already the natural place for this state.

### Config

`src/config.py`:

```python
@dataclass
class ManagerBotConfig:  # renamed from NotificationConfig
    bot_token: str

@dataclass
class CommonConfig(YAMLWizard):
    pyrogram: PyrogramConfig
    group: GroupConfig
    database: DatabaseConfig
    server: ServerConfig
    manager: ManagerBotConfig  # required, no Optional
```

`config.yaml.j2` (the whole `{% if env.BOT_TOKEN %}` guard goes away):

```yaml
manager:
  bot_token: "{{ env.BOT_TOKEN }}"
```

If `BOT_TOKEN` is unset, Jinja2's `StrictUndefined` already raises, which becomes a clear startup failure.

### `AutoPollManagerBot`

`src/auto_poll_manager_bot.py`:

```python
@dataclass
class VoterHandle:
    user: UserRecord   # enabled state lives on user.enabled
    client: Client

class AutoPollManagerBot:
    def __init__(self, common, manager_cfg, repo: UserRepository): ...
    def register_voter(self, telegram_user_id: int, handle: VoterHandle) -> None: ...
    async def send_message(self, chat_id: int, text: str, parse_mode=None) -> None: ...
    # handlers: /enable, /disable, /status (DM-only)
```

Handler authorization: every command looks up `message.from_user.id` in the registry. Miss → reply `"You're not registered. Contact the administrator."` Hit → proceed.

`/status` probe — honest about what we can cheaply check. `client.is_connected` is the primary signal (reflects active MTProto session state); a wrapped `get_me()` catches client objects that are wedged despite reporting connected. Note: `get_me()` is cached on a started client, so it normally returns instantly — it does NOT perform a network roundtrip. The "up" signal therefore means "connected and responsive to a trivial in-process call", not "Telegram is reachable right now". This is sufficient for the purpose (the old Saved-Messages `/ping` gave the same evidence), and avoids pulling in raw Pyrogram internals for a real network ping.

```python
try:
    if not handle.client.is_connected:
        voter_line = "Voter: down (disconnected)"
    else:
        await asyncio.wait_for(handle.client.get_me(), timeout=3)
        voter_line = "Voter: up"
except asyncio.TimeoutError:
    voter_line = "Voter: down (timeout)"
except Exception as e:
    voter_line = f"Voter: down ({type(e).__name__})"
voting_line = f"Voting: {'enabled' if handle.user.enabled else 'disabled'}"
await message.reply_text(f"{voter_line}\n{voting_line}")
```

Every command handler wraps its body in `try/except`; on unexpected error, logs and replies `"Internal error, try again."` No stack traces leaked to users.

### Voter changes

- Constructor gains `manager: AutoPollManagerBot` (required); the old `notifier=` parameter is removed. Stored as `self.manager = manager`. No separate flag param — the voter reads `self.user.enabled` directly.
- `on_forum_message` gains a top-level guard:
  ```python
  if not self.user.enabled:
      self.log.debug("Autovoting disabled; ignoring.")
      return
  ```
- `log_incoming_message` and its Saved-Messages `/ping` handler registration are deleted.
- `send_vote_notification` becomes:
  ```python
  async def send_vote_notification(self, topic_name: str) -> None:
      if self.user.telegram_user_id is None:
          self.log.warning("Skipping notification: telegram_user_id not set (backfill didn't run).")
          return
      text = f"<b>Vote Notification</b>\n\nEvent: {topic_name}"
      try:
          await self.manager.send_message(
              self.user.telegram_user_id, text, parse_mode=ParseMode.HTML,
          )
      except Exception as e:
          self.log.error("Failed to send vote notification: %s", e)
  ```
  The `telegram_user_id is None` branch is defensive — the startup orchestration in Task 7 guarantees backfill before voter handlers can fire, but we don't want to crash the voter if something goes wrong with that invariant.
- `src/auto_poll_notifier_bot.py` is deleted.

### Startup orchestration

`app.py` no longer calls `pyrogram.compose(...)` directly; instead:

```python
async def main(common, repo, manager_bot, voter_bots, health_server):
    for bot in voter_bots:
        await bot.app.start()

    for bot in voter_bots:
        me = await bot.app.get_me()
        repo.set_telegram_user_id(bot.user.id, me.id)
        bot.user.telegram_user_id = me.id
        handle = VoterHandle(user=bot.user, client=bot.app)
        manager_bot.register_voter(me.id, handle)

    await manager_bot.app.start()
    health_server.set_status(True, "Bot running")
    try:
        await asyncio.Event().wait()  # block until cancelled (SIGINT/KeyboardInterrupt)
    finally:
        await manager_bot.app.stop()
        for bot in voter_bots:
            await bot.app.stop()
```

Voter and VoterHandle share the same `UserRecord` object by reference — that's how `/enable`/`/disable` commands (writing `handle.user.enabled`) are visible to the voter's guard (reading `self.user.enabled`).

If a voter's `get_me()` fails, we log the failure loudly and exit the process — the bot cannot function with an un-pinned identity. If `set_telegram_user_id` raises `sqlite3.IntegrityError` (unique-index violation), we also exit loudly — this is a config error (two session strings for the same Telegram account).

Shutdown: `asyncio.run` translates a `KeyboardInterrupt` (Ctrl+C / SIGINT) into a task cancellation, which raises inside `await asyncio.Event().wait()` and triggers the `finally` block. SIGTERM handling is not explicitly added — same as the existing `compose()`-based behavior; out of scope for this change.

## What Goes Where

- **Implementation Steps** (`[ ]` checkboxes below): code changes, tests, README/CLAUDE updates — all achievable in this repo.
- **Post-Completion** (no checkboxes, informational): deployment env-var update (`BOT_TOKEN` now required), one-time first-boot observation that the backfill migration runs and `telegram_user_id` rows populate.

## Implementation Steps

### Task 1: Add migration + refactor UserRepository

**Files:**
- Create: `migrations/0002_add_telegram_user_id.sql`
- Modify: `src/user_repository.py`
- Modify: `app.py` (update import + usage)
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_user_repository.py`
- Modify: `requirements.txt` (add `pytest`)

- [x] create `migrations/0002_add_telegram_user_id.sql` with the ALTER + partial unique index (include `-- depends: 0001_create_users` header per yoyo convention)
- [x] refactor `src/user_repository.py` into `UserRepository` class holding `db_path`; add `telegram_user_id: Optional[int]` AND `enabled: bool` to `UserRecord`; implement `get_enabled_users()` (SELECT includes both new fields; hydrates `enabled=True` since the filter is `WHERE enabled=1`), `set_telegram_user_id(user_id, telegram_user_id)`, `set_enabled(telegram_user_id, enabled)` (returns rows-affected); remove old module-level `get_enabled_users` function
- [x] update `app.py` to instantiate `repo = UserRepository(common.database.path)` and call `repo.get_enabled_users()`
- [x] add `pytest` to `requirements.txt`
- [x] create `tests/conftest.py` with a `tmp_db` fixture that creates a temp sqlite file and runs `apply_migrations` against it
- [x] write `tests/test_user_repository.py`: seed a user row via direct SQL, test `get_enabled_users` returns it with `telegram_user_id=None` and `enabled=True`, test `set_telegram_user_id` upserts, test `set_enabled(True/False)` is idempotent and returns correct rows-affected (0 for unknown tg_id, 1 for known), test that `set_telegram_user_id` raising `IntegrityError` when two users try to share a tg_id surfaces as an exception
- [x] run tests with `pytest tests/test_user_repository.py` — must pass before next task

### Task 2: Rename config, make BOT_TOKEN required

**Files:**
- Modify: `src/config.py`
- Modify: `config.yaml.j2`
- Create: `tests/test_config.py`

- [x] rename `NotificationConfig` → `ManagerBotConfig` in `src/config.py`
- [x] change the `CommonConfig` field from `notification: Optional[NotificationConfig] = None` to `manager: ManagerBotConfig` (required, no Optional, no default)
- [x] update `config.yaml.j2`: remove the `{% if env.BOT_TOKEN %}` guard, rename the top-level key from `notification` to `manager` (the `bot_token` field stays)
- [x] write `tests/test_config.py`: (a) rendering the template with `BOT_TOKEN` set yields a valid `CommonConfig` with `manager.bot_token` populated; (b) rendering without `BOT_TOKEN` raises (Jinja2 StrictUndefined). Keep assertions narrow — don't retest dataclass-wizard or Jinja internals
- [x] run tests — must pass before next task

### Task 3: Add AutoPollManagerBot skeleton

**Files:**
- Create: `src/auto_poll_manager_bot.py`
- Create: `tests/test_auto_poll_manager_bot.py`

- [x] create `src/auto_poll_manager_bot.py` with: `VoterHandle` dataclass (`user: UserRecord`, `client: Client` — enabled state lives on `user.enabled`); `AutoPollManagerBot` class taking `common, manager_cfg, repo` (both `common.pyrogram` and `manager_cfg.bot_token` are consumed for Client construction); internal `self._handles: Dict[int, VoterHandle] = {}`; `Client(name="manager", api_id, api_hash, bot_token=manager_cfg.bot_token, in_memory=True)` as `self.app` (in_memory avoids creating a session file); logger named `"forum-poll-voter.manager"`; `register_voter(telegram_user_id, handle)` method that asserts no duplicate; `async send_message(chat_id, text, parse_mode=None)` wrapper around `self.app.send_message`
- [x] add a private `_get_handle_or_reject(message) -> Optional[VoterHandle]` helper that looks up `message.from_user.id` and, on miss, replies with the exact string `"You're not registered. Contact the administrator."` and returns None; handlers reuse this
- [x] do NOT register `/enable`, `/disable`, `/status` handlers yet (next tasks); but DO wire the `_register_handlers` method scaffold
- [x] write `tests/test_auto_poll_manager_bot.py`: patch `pyrogram.Client` with `unittest.mock.patch` (or pass a pre-constructed Client via in_memory=True) so tests don't touch disk or network; assert registry starts empty, assert `register_voter` adds, assert duplicate `register_voter` raises
- [x] run tests — must pass before next task

### Task 4: /enable and /disable command handlers

**Files:**
- Modify: `src/auto_poll_manager_bot.py`
- Modify: `tests/test_auto_poll_manager_bot.py`

- [x] add `async def _handle_enable(self, client, message)` and `_handle_disable(...)` in `AutoPollManagerBot`; register them with `MessageHandler(..., filters.command("enable") & filters.private)` and the equivalent for `disable`
- [x] both handlers: call `_get_handle_or_reject`; on hit, set `handle.user.enabled = True/False`; call `self.repo.set_enabled(telegram_user_id, True/False)` (ignore rows-affected — for a registered user it should always be 1; if it returns 0 we log a warning but the in-memory state is still the source of truth so we still reply success); reply with exactly `"Autovoting enabled."` / `"Autovoting disabled."`; idempotent (no special casing when already in state)
- [x] wrap handler bodies in try/except; on unexpected error, log and reply `"Internal error, try again."`
- [x] write tests: construct manager bot with a fake `UserRepository` (records calls) and a fake `Message` (records replies, exposes `from_user.id`); register a handle; call the handler coroutines directly with asyncio.run; assert (a) unknown sender gets the exact "not registered" string and no DB write, (b) `/enable` sets `handle.user.enabled=True`, calls `repo.set_enabled(tg_id, True)`, replies "Autovoting enabled.", (c) `/disable` mirror, (d) calling `/enable` when already enabled still writes DB and still replies (idempotency), (e) when `repo.set_enabled` raises, handler logs and replies "Internal error, try again.", (f) when `repo.set_enabled` returns 0 rows-affected, handler logs a warning but still sets the flag and replies success
- [x] run tests — must pass before next task

### Task 5: /status command handler

**Files:**
- Modify: `src/auto_poll_manager_bot.py`
- Modify: `tests/test_auto_poll_manager_bot.py`

- [x] add `async def _handle_status(self, client, message)`; register with `filters.command("status") & filters.private`
- [x] handler: call `_get_handle_or_reject`; first check `handle.client.is_connected` — if False, `voter_line = "Voter: down (disconnected)"`; else `await asyncio.wait_for(handle.client.get_me(), timeout=3)` → `"Voter: up"` on success, `"Voter: down (timeout)"` on `asyncio.TimeoutError`, `f"Voter: down ({type(e).__name__})"` on any other exception; compose `voting_line = f"Voting: {'enabled' if handle.user.enabled else 'disabled'}"`; reply with the two lines joined by `\n`
- [x] wrap handler in the same try/except pattern as task 4
- [x] write tests with a fake client (stub `is_connected` property + stub `get_me` coroutine): (a) connected + get_me returns → "Voter: up\nVoting: enabled", (b) `is_connected=False` → "Voter: down (disconnected)\nVoting: enabled", (c) connected but get_me raises `asyncio.TimeoutError` → "Voter: down (timeout)\nVoting: enabled", (d) connected but get_me raises `RuntimeError("boom")` → "Voter: down (RuntimeError)\nVoting: enabled", (e) `handle.user.enabled=False` flips the Voting line to "disabled", (f) unregistered sender gets the exact not-registered string
- [x] run tests — must pass before next task

### Task 6: Voter guard + notification rewrite + delete old notifier

**Files:**
- Modify: `src/auto_poll_voter_bot.py`
- Delete: `src/auto_poll_notifier_bot.py`
- Create: `tests/test_auto_poll_voter_bot.py`

- [x] change `AutoPollVoterBot.__init__` signature: replace `notifier=None` with `manager: AutoPollManagerBot` (required); store as `self.manager = manager`. No separate flag parameter — voter reads `self.user.enabled` directly
- [x] delete `log_incoming_message` method and its `MessageHandler(..., filters.chat(SAVED_MESSAGES_CHAT))` registration in `_register_handlers`; also remove the `SAVED_MESSAGES_CHAT` constant if it becomes unused
- [x] add guard at the top of `on_forum_message`:
  ```python
  if not self.user.enabled:
      self.log.debug("Autovoting disabled; ignoring.")
      return
  ```
- [x] rewrite `send_vote_notification`: if `self.user.telegram_user_id is None`, log a warning and return early (defensive — backfill should guarantee this never happens at runtime); else call `await self.manager.send_message(self.user.telegram_user_id, text, parse_mode=ParseMode.HTML)`; remove the `get_current_user_id` call path (identity now pinned in `user.telegram_user_id`); keep the log-and-swallow exception handling around the send_message call
- [x] remove the now-unused `get_current_user_id` method if nothing else uses it
- [x] delete `src/auto_poll_notifier_bot.py`
- [x] write `tests/test_auto_poll_voter_bot.py` with direct coroutine invocation: (a) when `user.enabled=False`, `on_forum_message` returns early (assert no downstream calls via stubbed `vote_in_thread_poll`), (b) when True, `vote_in_thread_poll` is called, (c) `send_vote_notification` calls `manager.send_message(user.telegram_user_id, <expected text>, parse_mode=ParseMode.HTML)`, (d) when `manager.send_message` raises, `send_vote_notification` logs and does NOT re-raise, (e) when `user.telegram_user_id is None`, `send_vote_notification` logs a warning and does NOT call `manager.send_message`
- [x] run tests — must pass before next task

### Task 7: Wire it all up in app.py

**Files:**
- Modify: `app.py`

- [x] remove `from pyrogram import compose` and the `AutoPollNotifierBot` import
- [x] add imports: `import asyncio`, `from src.auto_poll_manager_bot import AutoPollManagerBot, VoterHandle`
- [x] replace the current `__main__` block with an `async def main(...)` that: (1) constructs `manager_bot = AutoPollManagerBot(common, common.manager, repo)`, (2) builds each voter with `manager=manager_bot` (voter reads `self.user.enabled` — already hydrated to True by `get_enabled_users`), (3) registers each voter's client with `health_server`, (4) also registers `manager_bot.app` with `health_server`, (5) `await bot.app.start()` for each voter, (6) for each voter: `me = await bot.app.get_me(); repo.set_telegram_user_id(bot.user.id, me.id); bot.user.telegram_user_id = me.id; manager_bot.register_voter(me.id, VoterHandle(user=bot.user, client=bot.app))`, (7) `await manager_bot.app.start()`, (8) `health_server.set_status(True, "Bot running")`, (9) `await asyncio.Event().wait()` in a try-block, (10) in `finally`: `await manager_bot.app.stop()` then `await bot.app.stop()` for each voter
- [x] wrap the whole startup/run block so that any exception during setup sets `health_server.set_status(False, ...)` and re-raises (match current behavior)
- [x] ensure an `IntegrityError` from `set_telegram_user_id` or a failing `get_me()` at startup produces a clear log message and exits — do not try to recover
- [x] invoke with `asyncio.run(main(...))` at the bottom; SIGINT/Ctrl+C will cancel the `Event().wait()` via `asyncio.run`'s `KeyboardInterrupt` handling, triggering the `finally` for graceful shutdown
- [x] no unit test for `app.py` itself (it's pure orchestration); covered transitively by other tasks
- [x] run the full suite `pytest tests/` — must pass before next task

### Task 8: Update README.md and CLAUDE.md

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [x] update `README.md`: document `BOT_TOKEN` as **required**, document the three commands (`/enable`, `/disable`, `/status`) with short description, note that the old Saved-Messages `/ping` behavior is removed, note that `telegram_user_id` is populated automatically on first startup
- [x] update `CLAUDE.md`: update the "Shared environment variables" list so `BOT_TOKEN` is required (not optional); rename "AutoPollNotifierBot" section to "AutoPollManagerBot" with updated description (commands + notifications); add the `VoterHandle` concept to the architecture notes; note that `UserRecord.enabled` is mutable runtime state shared by reference between voter and manager; update the Startup Sequence section to reflect the new orchestration (start voters → backfill → register → start manager → `asyncio.Event().wait()`); update the Per-user configuration section to include `telegram_user_id`; update the File Structure tree (remove `auto_poll_notifier_bot.py`, add `auto_poll_manager_bot.py`, add `migrations/0002_add_telegram_user_id.sql`)
- [x] no tests for documentation changes

### Task 9: Verify acceptance criteria

- [x] verify all three in-scope features (`/enable`/`/disable`, `/status`, notifications via bot) are implemented
- [x] verify unregistered-user path returns the exact string `"You're not registered. Contact the administrator."`
- [x] verify the voter guard path doesn't vote when `enabled_flag.value=False` (test coverage from Task 6)
- [x] verify `AutoPollNotifierBot` and `log_incoming_message` are gone; search for leftover references via ripgrep: `rg "AutoPollNotifierBot|log_incoming_message|SAVED_MESSAGES_CHAT" src/ app.py`
- [x] run full test suite: `pytest tests/`
- [x] confirm `requests` is still imported only by `src/health_check.py` (self-ping); do NOT remove from `requirements.txt`

### Task 10: [Final] Finalize

- [x] confirm README.md and CLAUDE.md updates from Task 8 are in place
- [x] move this plan to `docs/plans/completed/` (create dir if needed)

## Post-Completion

*Items requiring manual intervention or external systems — no checkboxes, informational only*

**Deployment:**
- `.env` / deployment secrets must set `BOT_TOKEN` before this build is deployed. A deploy without `BOT_TOKEN` will fail at startup with a clear Jinja `UndefinedError`. This is intentional and user-approved.
- On first boot after this change, migration `0002_add_telegram_user_id.sql` runs automatically; each enabled voter backfills its `telegram_user_id` row via `get_me()`. No manual data entry needed.

**Manual verification (post-deploy):**
- From a registered user's Telegram account, DM the bot `/status` — expect `Voter: up\nVoting: enabled`.
- `/disable` → confirm a subsequent poll in the monitored forum is NOT voted on; `/status` reports `Voting: disabled`.
- `/enable` → confirm voting resumes on the next matching poll; `/status` reports `Voting: enabled`.
- From an unregistered account, DM the bot `/status` — expect `"You're not registered. Contact the administrator."`

**Out of scope (do not implement even if tempting during development):**
- Additional commands (`/schedule`, `/list`, `/help`, `/start`, etc.).
- Admin / multi-user management surface.
- Integration tests against live Telegram.
- Retries or queueing for failed notifications.
- Changes to `EventInfoParser`, schedule DSL, health-check endpoint semantics, or self-ping behavior.
