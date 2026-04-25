"""Tests for AutoPollManagerBot skeleton (Task 3).

The Pyrogram Client is patched so tests never touch disk or network.
"""
import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auto_poll_manager_bot import AutoPollManagerBot, VoterHandle
from src.config import CommonConfig, DatabaseConfig, GroupConfig, ManagerBotConfig, PyrogramConfig, ServerConfig
from src.user_repository import UserRecord


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_common() -> CommonConfig:
    """Build a minimal CommonConfig without touching the file system."""
    return CommonConfig(
        pyrogram=PyrogramConfig(api_id=12345, api_hash="deadbeef"),
        group=GroupConfig(chat_id=-100, vote_option="Go!"),
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(port=8080),
        manager=ManagerBotConfig(bot_token="bot:TOKEN"),
    )


def _make_user_record(
    tg_id: Optional[int] = 111,
    enabled: bool = True,
    db_id: int = 1,
) -> UserRecord:
    return UserRecord(
        id=db_id,
        session_name="alice",
        session_string="SESS",
        event_schedule="Game wed",
        vote_delay_seconds=5,
        telegram_user_id=tg_id,
        enabled=enabled,
        reminders_enabled=True,
        reminder_lead_hours=27,
    )


def _make_repo():
    """Return a MagicMock that quacks like UserRepository."""
    repo = MagicMock()
    repo.set_enabled = MagicMock(return_value=1)
    return repo


def _make_reminder_repo():
    """Return a MagicMock that quacks like ReminderRepository."""
    return MagicMock()


def _make_manager(common=None, repo=None, reminder_repo=None) -> AutoPollManagerBot:
    """Create AutoPollManagerBot with a patched Pyrogram Client."""
    if common is None:
        common = _make_common()
    if repo is None:
        repo = _make_repo()
    if reminder_repo is None:
        reminder_repo = _make_reminder_repo()

    with patch("src.auto_poll_manager_bot.Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        manager = AutoPollManagerBot(common, repo, reminder_repo)
    return manager


def _make_voter_handle(user: Optional[UserRecord] = None) -> VoterHandle:
    if user is None:
        user = _make_user_record()
    client = MagicMock()
    return VoterHandle(user=user, client=client)


def _make_message(from_user_id: Optional[int] = 111) -> MagicMock:
    msg = MagicMock(spec=["from_user", "reply_text"])
    if from_user_id is None:
        msg.from_user = None
    else:
        msg.from_user = MagicMock()
        msg.from_user.id = from_user_id
    msg.reply_text = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# Tests: VoterHandle dataclass
# ---------------------------------------------------------------------------

class TestVoterHandle:
    def test_fields_accessible(self):
        user = _make_user_record()
        client = MagicMock()
        handle = VoterHandle(user=user, client=client)
        assert handle.user is user
        assert handle.client is client

    def test_enabled_state_is_shared_reference(self):
        """Mutating handle.user.enabled must be visible via the same UserRecord."""
        user = _make_user_record(enabled=True)
        handle = VoterHandle(user=user, client=MagicMock())
        handle.user.enabled = False
        # user and handle.user point to the same object
        assert user.enabled is False


# ---------------------------------------------------------------------------
# Tests: registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_registry_starts_empty(self):
        manager = _make_manager()
        assert manager._handles == {}

    def test_register_voter_adds_handle(self):
        manager = _make_manager()
        handle = _make_voter_handle()
        manager.register_voter(111, handle)
        assert 111 in manager._handles
        assert manager._handles[111] is handle

    def test_duplicate_register_raises(self):
        manager = _make_manager()
        handle = _make_voter_handle()
        manager.register_voter(111, handle)
        with pytest.raises(ValueError, match="already registered"):
            manager.register_voter(111, _make_voter_handle())

    def test_different_ids_can_be_registered(self):
        manager = _make_manager()
        h1 = _make_voter_handle(_make_user_record(tg_id=111, db_id=1))
        h2 = _make_voter_handle(_make_user_record(tg_id=222, db_id=2))
        manager.register_voter(111, h1)
        manager.register_voter(222, h2)
        assert len(manager._handles) == 2


# ---------------------------------------------------------------------------
# Tests: send_message is on self.app directly (no wrapper)
# ---------------------------------------------------------------------------

class TestSendMessage:
    def test_app_send_message_is_callable(self):
        """manager.app.send_message is the direct Pyrogram send_message (no wrapper)."""
        manager = _make_manager()
        manager.app.send_message = AsyncMock()
        asyncio.run(manager.app.send_message(chat_id=999, text="hello", parse_mode=None))
        manager.app.send_message.assert_awaited_once_with(
            chat_id=999, text="hello", parse_mode=None
        )

    def test_no_send_message_wrapper(self):
        """AutoPollManagerBot no longer has a send_message wrapper method."""
        manager = _make_manager()
        assert not hasattr(manager, "send_message")


# ---------------------------------------------------------------------------
# Tests: _get_handle_or_reject
# ---------------------------------------------------------------------------

class TestGetHandleOrReject:
    def test_returns_handle_for_registered_user(self):
        manager = _make_manager()
        handle = _make_voter_handle()
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        result = asyncio.run(manager._get_handle_or_reject(msg))
        assert result is handle
        msg.reply_text.assert_not_called()

    def test_replies_and_returns_none_for_unknown_user(self):
        manager = _make_manager()
        msg = _make_message(from_user_id=999)
        result = asyncio.run(manager._get_handle_or_reject(msg))
        assert result is None
        msg.reply_text.assert_awaited_once_with(
            "You're not registered. Contact the administrator."
        )

    def test_replies_and_returns_none_when_no_from_user(self):
        manager = _make_manager()
        msg = _make_message(from_user_id=None)
        result = asyncio.run(manager._get_handle_or_reject(msg))
        assert result is None
        msg.reply_text.assert_awaited_once_with(
            "You're not registered. Contact the administrator."
        )

    def test_empty_registry_always_rejects(self):
        manager = _make_manager()
        msg = _make_message(from_user_id=42)
        result = asyncio.run(manager._get_handle_or_reject(msg))
        assert result is None
        msg.reply_text.assert_awaited_once_with(
            "You're not registered. Contact the administrator."
        )


# ---------------------------------------------------------------------------
# Tests: Client construction params (smoke)
# ---------------------------------------------------------------------------

class TestClientConstruction:
    def test_client_constructed_with_expected_args(self):
        common = _make_common()
        repo = _make_repo()
        reminder_repo = _make_reminder_repo()
        with patch("src.auto_poll_manager_bot.Client") as mock_client_cls:
            mock_client_cls.return_value = MagicMock()
            AutoPollManagerBot(common, repo, reminder_repo)
        mock_client_cls.assert_called_once_with(
            name="manager",
            api_id=common.pyrogram.api_id,
            api_hash=common.pyrogram.api_hash,
            bot_token=common.manager.bot_token,
            in_memory=True,
        )


# ---------------------------------------------------------------------------
# Tests: /enable handler
# ---------------------------------------------------------------------------

class TestHandleEnable:
    def test_unknown_sender_gets_not_registered(self):
        manager = _make_manager()
        msg = _make_message(from_user_id=999)  # not in registry
        asyncio.run(manager._handle_enable(None, msg))
        msg.reply_text.assert_awaited_once_with(
            "You're not registered. Contact the administrator."
        )

    def test_unknown_sender_no_db_write(self):
        repo = _make_repo()
        manager = _make_manager(repo=repo)
        msg = _make_message(from_user_id=999)
        asyncio.run(manager._handle_enable(None, msg))
        repo.set_enabled.assert_not_called()

    def test_enable_sets_flag_true(self):
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=False)
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_enable(None, msg))
        assert handle.user.enabled is True

    def test_enable_calls_repo_set_enabled(self):
        repo = _make_repo()
        manager = _make_manager(repo=repo)
        user = _make_user_record(tg_id=111, enabled=False)
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_enable(None, msg))
        repo.set_enabled.assert_called_once_with(111, True)

    def test_enable_replies_enabled(self):
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=False)
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_enable(None, msg))
        msg.reply_text.assert_awaited_once_with("Autovoting enabled.")

    def test_enable_idempotent_when_already_enabled(self):
        """Calling /enable when already enabled still writes DB and still replies success."""
        repo = _make_repo()
        manager = _make_manager(repo=repo)
        user = _make_user_record(tg_id=111, enabled=True)  # already enabled
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_enable(None, msg))
        repo.set_enabled.assert_called_once_with(111, True)
        msg.reply_text.assert_awaited_once_with("Autovoting enabled.")
        assert handle.user.enabled is True

    def test_enable_when_repo_raises_replies_internal_error(self):
        """When repo.set_enabled raises, handler replies error and does NOT mutate the flag."""
        repo = _make_repo()
        repo.set_enabled.side_effect = RuntimeError("db exploded")
        manager = _make_manager(repo=repo)
        user = _make_user_record(tg_id=111, enabled=False)  # start disabled so mutation is detectable
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_enable(None, msg))
        msg.reply_text.assert_awaited_once_with("Internal error, try again.")
        assert handle.user.enabled is False  # flag must NOT have been mutated

    def test_enable_when_repo_returns_zero_rows_still_sets_flag_and_replies(self):
        """When set_enabled returns 0, handler still sets flag and replies success (logs warning)."""
        repo = _make_repo()
        repo.set_enabled.return_value = 0
        manager = _make_manager(repo=repo)
        user = _make_user_record(tg_id=111, enabled=False)
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_enable(None, msg))
        assert handle.user.enabled is True
        msg.reply_text.assert_awaited_once_with("Autovoting enabled.")


# ---------------------------------------------------------------------------
# Tests: /disable handler
# ---------------------------------------------------------------------------

class TestHandleDisable:
    def test_unknown_sender_gets_not_registered(self):
        manager = _make_manager()
        msg = _make_message(from_user_id=999)
        asyncio.run(manager._handle_disable(None, msg))
        msg.reply_text.assert_awaited_once_with(
            "You're not registered. Contact the administrator."
        )

    def test_unknown_sender_no_db_write(self):
        repo = _make_repo()
        manager = _make_manager(repo=repo)
        msg = _make_message(from_user_id=999)
        asyncio.run(manager._handle_disable(None, msg))
        repo.set_enabled.assert_not_called()

    def test_disable_sets_flag_false(self):
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=True)
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_disable(None, msg))
        assert handle.user.enabled is False

    def test_disable_calls_repo_set_enabled(self):
        repo = _make_repo()
        manager = _make_manager(repo=repo)
        user = _make_user_record(tg_id=111, enabled=True)
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_disable(None, msg))
        repo.set_enabled.assert_called_once_with(111, False)

    def test_disable_replies_disabled(self):
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=True)
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_disable(None, msg))
        msg.reply_text.assert_awaited_once_with("Autovoting disabled.")

    def test_disable_idempotent_when_already_disabled(self):
        """Calling /disable when already disabled still writes DB and still replies."""
        repo = _make_repo()
        manager = _make_manager(repo=repo)
        user = _make_user_record(tg_id=111, enabled=False)  # already disabled
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_disable(None, msg))
        repo.set_enabled.assert_called_once_with(111, False)
        msg.reply_text.assert_awaited_once_with("Autovoting disabled.")
        assert handle.user.enabled is False

    def test_disable_when_repo_raises_replies_internal_error(self):
        """When repo.set_enabled raises, handler replies error and does NOT mutate the flag."""
        repo = _make_repo()
        repo.set_enabled.side_effect = RuntimeError("db exploded")
        manager = _make_manager(repo=repo)
        user = _make_user_record(tg_id=111, enabled=True)  # start enabled so mutation is detectable
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_disable(None, msg))
        msg.reply_text.assert_awaited_once_with("Internal error, try again.")
        assert handle.user.enabled is True  # flag must NOT have been mutated

    def test_disable_when_repo_returns_zero_rows_still_clears_flag_and_replies(self):
        """When set_enabled returns 0, handler still clears flag and replies success."""
        repo = _make_repo()
        repo.set_enabled.return_value = 0
        manager = _make_manager(repo=repo)
        user = _make_user_record(tg_id=111, enabled=True)
        handle = _make_voter_handle(user)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_disable(None, msg))
        assert handle.user.enabled is False
        msg.reply_text.assert_awaited_once_with("Autovoting disabled.")


# ---------------------------------------------------------------------------
# Tests: /status handler
# ---------------------------------------------------------------------------

def _make_connected_client(get_me_result=None, get_me_raises=None) -> MagicMock:
    """Return a stub client that reports is_connected=True with controllable get_me."""
    client = MagicMock()
    client.is_connected = True
    if get_me_raises is not None:
        client.get_me = AsyncMock(side_effect=get_me_raises)
    else:
        client.get_me = AsyncMock(return_value=get_me_result or MagicMock())
    return client


def _make_disconnected_client() -> MagicMock:
    """Return a stub client that reports is_connected=False."""
    client = MagicMock()
    client.is_connected = False
    return client


class TestHandleStatus:
    def test_unregistered_sender_gets_not_registered(self):
        manager = _make_manager()
        msg = _make_message(from_user_id=999)
        asyncio.run(manager._handle_status(None, msg))
        msg.reply_text.assert_awaited_once_with(
            "You're not registered. Contact the administrator."
        )

    def test_connected_get_me_ok_voting_enabled(self):
        """Connected + get_me returns → 'Voter: up\\nVoting: enabled'."""
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=True)
        client = _make_connected_client()
        handle = VoterHandle(user=user, client=client)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_status(None, msg))
        msg.reply_text.assert_awaited_once_with("Voter: up\nVoting: enabled")

    def test_disconnected_client_reports_down(self):
        """is_connected=False → 'Voter: down (disconnected)\\nVoting: enabled'."""
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=True)
        client = _make_disconnected_client()
        handle = VoterHandle(user=user, client=client)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_status(None, msg))
        msg.reply_text.assert_awaited_once_with(
            "Voter: down (disconnected)\nVoting: enabled"
        )

    def test_get_me_timeout_reports_down_timeout(self):
        """Connected but get_me raises TimeoutError → 'Voter: down (timeout)'."""
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=True)
        client = _make_connected_client(get_me_raises=asyncio.TimeoutError())
        handle = VoterHandle(user=user, client=client)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_status(None, msg))
        msg.reply_text.assert_awaited_once_with(
            "Voter: down (timeout)\nVoting: enabled"
        )

    def test_get_me_raises_other_exception(self):
        """Connected but get_me raises RuntimeError → 'Voter: down (RuntimeError)'."""
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=True)
        client = _make_connected_client(get_me_raises=RuntimeError("boom"))
        handle = VoterHandle(user=user, client=client)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_status(None, msg))
        msg.reply_text.assert_awaited_once_with(
            "Voter: down (RuntimeError)\nVoting: enabled"
        )

    def test_voting_disabled_flips_voting_line(self):
        """handle.user.enabled=False → Voting: disabled."""
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=False)
        client = _make_connected_client()
        handle = VoterHandle(user=user, client=client)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_status(None, msg))
        msg.reply_text.assert_awaited_once_with("Voter: up\nVoting: disabled")

    def test_connected_voting_disabled_combo(self):
        """Disconnected + voting disabled shows both lines correctly."""
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=False)
        client = _make_disconnected_client()
        handle = VoterHandle(user=user, client=client)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_status(None, msg))
        msg.reply_text.assert_awaited_once_with(
            "Voter: down (disconnected)\nVoting: disabled"
        )

    def test_status_outer_except_replies_internal_error(self):
        """When handle.client.is_connected raises unexpectedly, handler replies Internal error."""
        manager = _make_manager()
        user = _make_user_record(tg_id=111, enabled=True)
        client = MagicMock()
        # Make is_connected raise instead of returning a bool
        type(client).is_connected = property(lambda self: (_ for _ in ()).throw(RuntimeError("wedged")))
        handle = VoterHandle(user=user, client=client)
        manager.register_voter(111, handle)
        msg = _make_message(from_user_id=111)
        asyncio.run(manager._handle_status(None, msg))
        msg.reply_text.assert_awaited_once_with("Internal error, try again.")


# ---------------------------------------------------------------------------
# Tests: Task 7 — ScheduleEditor wiring
# ---------------------------------------------------------------------------

class TestScheduleEditorWiring:
    def test_manager_wires_schedule_editor(self):
        """ScheduleEditor is constructed with (repo, manager._handles) and
        register_handlers is called with manager.app."""
        common = _make_common()
        repo = _make_repo()
        reminder_repo = _make_reminder_repo()

        with patch("src.auto_poll_manager_bot.Client") as mock_client_cls, \
             patch("src.auto_poll_manager_bot.ScheduleEditor") as mock_editor_cls:
            mock_client_cls.return_value = MagicMock()
            mock_editor_instance = MagicMock()
            mock_editor_cls.return_value = mock_editor_instance

            manager = AutoPollManagerBot(common, repo, reminder_repo)

        # ScheduleEditor constructed with repo and the manager's _handles dict
        mock_editor_cls.assert_called_once_with(repo, manager._handles)
        # register_handlers called with manager.app
        mock_editor_instance.register_handlers.assert_called_once_with(manager.app)

    def test_manager_schedule_editor_stored_as_attribute(self):
        """AutoPollManagerBot exposes _schedule_editor attribute after init."""
        manager = _make_manager()
        assert hasattr(manager, "_schedule_editor")

    def test_existing_commands_still_work_after_wiring(self):
        """Existing /enable, /disable, /status handlers are registered unaffected.

        We verify by inspecting the add_handler call list on manager.app —
        the first three calls must be MessageHandlers for enable/disable/status
        (in order), and the schedule-editor handlers come after.
        """

        common = _make_common()
        repo = _make_repo()
        reminder_repo = _make_reminder_repo()

        with patch("src.auto_poll_manager_bot.Client") as mock_client_cls:
            mock_app = MagicMock()
            mock_client_cls.return_value = mock_app
            manager = AutoPollManagerBot(common, repo, reminder_repo)

        # Collect all add_handler positional-arg types
        call_args_list = mock_app.add_handler.call_args_list
        # At least 8 handlers: enable, disable, status, schedule-cmd, sch-callback,
        # reminders-cmd, rem-callback, lead-time-reply
        assert len(call_args_list) >= 8, (
            f"Expected at least 8 add_handler calls, got {len(call_args_list)}"
        )

        handler_types = [type(call.args[0]).__name__ for call in call_args_list]
        # First three must be MessageHandlers (enable, disable, status)
        assert handler_types[:3] == ["MessageHandler", "MessageHandler", "MessageHandler"], (
            f"First 3 handlers should be MessageHandler, got: {handler_types[:3]}"
        )
        # 4th must be MessageHandler for /schedule
        assert handler_types[3] == "MessageHandler", (
            f"4th handler should be MessageHandler (/schedule), got: {handler_types[3]}"
        )
        # 5th must be CallbackQueryHandler for sch:* callbacks
        assert handler_types[4] == "CallbackQueryHandler", (
            f"5th handler should be CallbackQueryHandler (sch:*), got: {handler_types[4]}"
        )
        # 6th must be MessageHandler for /reminders
        assert handler_types[5] == "MessageHandler", (
            f"6th handler should be MessageHandler (/reminders), got: {handler_types[5]}"
        )
        # 7th must be CallbackQueryHandler for rem:* callbacks
        assert handler_types[6] == "CallbackQueryHandler", (
            f"7th handler should be CallbackQueryHandler (rem:*), got: {handler_types[6]}"
        )
        # 8th must be MessageHandler for free-text lead-time reply
        assert handler_types[7] == "MessageHandler", (
            f"8th handler should be MessageHandler (lead-time reply), got: {handler_types[7]}"
        )


# ---------------------------------------------------------------------------
# Tests: Task 7 — RemindersEditor wiring and scheduler lifecycle
# ---------------------------------------------------------------------------

class TestRemindersEditorWiring:
    def test_manager_wires_reminders_editor(self):
        """RemindersEditor is constructed with (repo, manager._handles) and
        register_handlers is called with manager.app."""
        common = _make_common()
        repo = _make_repo()
        reminder_repo = _make_reminder_repo()

        with patch("src.auto_poll_manager_bot.Client") as mock_client_cls, \
             patch("src.auto_poll_manager_bot.RemindersEditor") as mock_editor_cls:
            mock_client_cls.return_value = MagicMock()
            mock_editor_instance = MagicMock()
            mock_editor_cls.return_value = mock_editor_instance

            manager = AutoPollManagerBot(common, repo, reminder_repo)

        # RemindersEditor constructed with repo and the manager's _handles dict
        mock_editor_cls.assert_called_once_with(repo, manager._handles)
        # register_handlers called with manager.app
        mock_editor_instance.register_handlers.assert_called_once_with(manager.app)

    def test_manager_reminders_editor_stored_as_attribute(self):
        """AutoPollManagerBot exposes _reminders_editor attribute after init."""
        manager = _make_manager()
        assert hasattr(manager, "_reminders_editor")

    def test_manager_scheduler_stored_as_attribute(self):
        """AutoPollManagerBot exposes _scheduler attribute after init."""
        manager = _make_manager()
        assert hasattr(manager, "_scheduler")


class TestSchedulerLifecycle:
    def test_start_scheduler_delegates_to_scheduler_start(self):
        """start_scheduler() calls self._scheduler.start()."""
        manager = _make_manager()
        manager._scheduler = MagicMock()
        manager._scheduler.start = AsyncMock()
        asyncio.run(manager.start_scheduler())
        manager._scheduler.start.assert_awaited_once()

    def test_stop_scheduler_delegates_to_scheduler_stop(self):
        """stop_scheduler() calls self._scheduler.stop()."""
        manager = _make_manager()
        manager._scheduler = MagicMock()
        manager._scheduler.stop = AsyncMock()
        asyncio.run(manager.stop_scheduler())
        manager._scheduler.stop.assert_awaited_once()

    def test_scheduler_constructed_with_reminder_repo(self):
        """ReminderScheduler is constructed with common, reminder_repo, handles, app."""
        common = _make_common()
        repo = _make_repo()
        reminder_repo = _make_reminder_repo()

        with patch("src.auto_poll_manager_bot.Client") as mock_client_cls, \
             patch("src.auto_poll_manager_bot.ReminderScheduler") as mock_scheduler_cls:
            mock_app = MagicMock()
            mock_client_cls.return_value = mock_app
            mock_scheduler_instance = MagicMock()
            mock_scheduler_cls.return_value = mock_scheduler_instance

            manager = AutoPollManagerBot(common, repo, reminder_repo)

        # ReminderScheduler should be constructed with (common, reminder_repo, handles, app)
        mock_scheduler_cls.assert_called_once_with(common, reminder_repo, manager._handles, mock_app)
        assert manager._scheduler is mock_scheduler_instance
