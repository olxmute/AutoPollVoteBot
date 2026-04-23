"""Tests for AutoPollVoterBot (Task 6, Task 3, and Task 4).

Covers:
  - enabled guard: on_forum_message returns early when user.enabled is False
  - enabled guard: vote_in_thread_poll is called when user.enabled is True
  - send_vote_notification: calls manager.send_message with correct args
  - send_vote_notification: logs and does NOT re-raise when manager.send_message raises
  - send_vote_notification: logs warning and does NOT call manager.send_message when
    telegram_user_id is None
  - topic_name_matches: stateless re-parse of event_schedule on every call (Task 3)
  - topic_name_matches: empty schedule returns False (Task 3)
  - topic_name_matches: unparseable DSL logs and returns False (Task 3)
  - reminder_discovery: record_from_vote called on successful vote (Task 4)
  - reminder_discovery: None is backward-compatible (Task 4)
  - reminder_discovery: NOT called when vote_poll raises (Task 4)
"""
import asyncio
import logging
from datetime import date, time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

from pyrogram.enums import ParseMode

from src.auto_poll_voter_bot import AutoPollVoterBot
from src.config import CommonConfig, DatabaseConfig, GroupConfig, ManagerBotConfig, PyrogramConfig, ServerConfig
from src.event_info_parser import EventInfo
from src.user_repository import UserRecord


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_common() -> CommonConfig:
    return CommonConfig(
        pyrogram=PyrogramConfig(api_id=12345, api_hash="deadbeef"),
        group=GroupConfig(chat_id=-100, vote_option="Go!"),
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(port=8080),
        manager=ManagerBotConfig(bot_token="bot:TOKEN"),
    )


def _make_user(
    enabled: bool = True,
    telegram_user_id: Optional[int] = 111,
    session_name: str = "alice",
) -> UserRecord:
    return UserRecord(
        id=1,
        session_name=session_name,
        session_string="SESS",
        event_schedule="Game wed",
        vote_delay_seconds=0,
        telegram_user_id=telegram_user_id,
        enabled=enabled,
        reminders_enabled=True,
        reminder_lead_hours=27,
    )


def _make_manager() -> MagicMock:
    """Return a mock AutoPollManagerBot whose app.send_message is async."""
    manager = MagicMock()
    manager.app = MagicMock()
    manager.app.send_message = AsyncMock()
    return manager


def _make_voter(user: Optional[UserRecord] = None, manager=None, reminder_discovery=None) -> AutoPollVoterBot:
    """Create an AutoPollVoterBot with a patched Pyrogram Client."""
    if user is None:
        user = _make_user()
    if manager is None:
        manager = _make_manager()
    common = _make_common()
    event_info_parser = MagicMock()

    with patch("src.auto_poll_voter_bot.Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        voter = AutoPollVoterBot(
            common=common,
            user=user,
            event_info_parser=event_info_parser,
            manager=manager,
            reminder_discovery=reminder_discovery,
        )
    return voter


def _make_poll_message() -> MagicMock:
    """Build a minimal fake Message that looks like a forum poll message."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = -100
    msg.message_thread_id = 42
    msg.poll = MagicMock()
    msg.poll.chosen_option_id = None
    msg.poll.options = []
    msg.id = 1
    return msg


# ---------------------------------------------------------------------------
# Tests: enabled guard in on_forum_message
# ---------------------------------------------------------------------------

class TestOnForumMessageGuard:
    def test_disabled_user_skips_vote_in_thread_poll(self):
        """When user.enabled is False, on_forum_message returns early without calling vote_in_thread_poll."""
        user = _make_user(enabled=False)
        voter = _make_voter(user=user)

        # Patch vote_in_thread_poll to detect if it was called
        voter.vote_in_thread_poll = AsyncMock()
        msg = _make_poll_message()

        asyncio.run(voter.on_forum_message(None, msg))
        voter.vote_in_thread_poll.assert_not_awaited()

    def test_enabled_user_calls_vote_in_thread_poll(self):
        """When user.enabled is True, on_forum_message proceeds to call vote_in_thread_poll."""
        user = _make_user(enabled=True)
        voter = _make_voter(user=user)

        # Patch vote_in_thread_poll to a no-op to avoid real logic
        voter.vote_in_thread_poll = AsyncMock()
        msg = _make_poll_message()

        asyncio.run(voter.on_forum_message(None, msg))
        voter.vote_in_thread_poll.assert_awaited_once_with(msg)

    def test_disabled_flag_can_be_toggled_at_runtime(self):
        """Toggling user.enabled from False to True causes the next call to proceed."""
        user = _make_user(enabled=False)
        voter = _make_voter(user=user)
        voter.vote_in_thread_poll = AsyncMock()
        msg = _make_poll_message()

        # First call: disabled → should skip
        asyncio.run(voter.on_forum_message(None, msg))
        voter.vote_in_thread_poll.assert_not_awaited()

        # Flip to enabled → should proceed
        user.enabled = True
        asyncio.run(voter.on_forum_message(None, msg))
        voter.vote_in_thread_poll.assert_awaited_once_with(msg)

    def test_on_forum_message_swallows_exception_from_vote_in_thread_poll(self):
        """on_forum_message catches and logs exceptions from vote_in_thread_poll without re-raising."""
        user = _make_user(enabled=True)
        voter = _make_voter(user=user)
        voter.vote_in_thread_poll = AsyncMock(side_effect=RuntimeError("unexpected failure"))
        msg = _make_poll_message()

        # Should not raise
        asyncio.run(voter.on_forum_message(None, msg))
        voter.vote_in_thread_poll.assert_awaited_once_with(msg)


# ---------------------------------------------------------------------------
# Tests: send_vote_notification
# ---------------------------------------------------------------------------

class TestSendVoteNotification:
    def test_sends_message_with_correct_args(self):
        """send_vote_notification calls manager.app.send_message with correct chat_id, text, parse_mode."""
        user = _make_user(enabled=True, telegram_user_id=111)
        manager = _make_manager()
        voter = _make_voter(user=user, manager=manager)

        asyncio.run(voter.send_vote_notification("Game 2026-05-01, Fri, 20:00-22:00"))

        manager.app.send_message.assert_awaited_once_with(
            chat_id=111,
            text="<b>Vote Notification</b>\n\nEvent: Game 2026-05-01, Fri, 20:00-22:00",
            parse_mode=ParseMode.HTML,
        )

    def test_send_message_raises_is_logged_not_reraised(self):
        """When manager.app.send_message raises, send_vote_notification logs and does NOT re-raise."""
        user = _make_user(enabled=True, telegram_user_id=111)
        manager = _make_manager()
        manager.app.send_message = AsyncMock(side_effect=RuntimeError("connection lost"))
        voter = _make_voter(user=user, manager=manager)

        # Should not raise
        asyncio.run(voter.send_vote_notification("Game 2026-05-01, Fri, 20:00-22:00"))
        manager.app.send_message.assert_awaited_once()

    def test_no_telegram_user_id_skips_send(self):
        """When user.telegram_user_id is None, send_vote_notification does NOT call manager.app.send_message."""
        user = _make_user(enabled=True, telegram_user_id=None)
        manager = _make_manager()
        voter = _make_voter(user=user, manager=manager)

        asyncio.run(voter.send_vote_notification("Game 2026-05-01, Fri, 20:00-22:00"))

        manager.app.send_message.assert_not_awaited()

    def test_no_telegram_user_id_logs_warning(self, caplog):
        """When telegram_user_id is None, a warning is logged."""
        import logging
        user = _make_user(enabled=True, telegram_user_id=None)
        voter = _make_voter(user=user)

        with caplog.at_level(logging.WARNING, logger=f"forum-poll-voter.{user.session_name}"):
            asyncio.run(voter.send_vote_notification("some topic"))

        assert any("telegram_user_id not set" in r.message for r in caplog.records)

    def test_send_raises_logs_error(self, caplog):
        """When manager.app.send_message raises, the error is logged."""
        import logging
        user = _make_user(enabled=True, telegram_user_id=111)
        manager = _make_manager()
        manager.app.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        voter = _make_voter(user=user, manager=manager)

        with caplog.at_level(logging.ERROR, logger=f"forum-poll-voter.{user.session_name}"):
            asyncio.run(voter.send_vote_notification("some topic"))

        assert any("Failed to send vote notification" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: removed behavior (smoke checks)
# ---------------------------------------------------------------------------

class TestRemovedBehavior:
    def test_no_log_incoming_message_method(self):
        """log_incoming_message was deleted; voter should not have this method."""
        voter = _make_voter()
        assert not hasattr(voter, "log_incoming_message"), (
            "log_incoming_message should have been deleted in Task 6"
        )

    def test_no_get_current_user_id_method(self):
        """get_current_user_id was deleted; voter should not have this method."""
        voter = _make_voter()
        assert not hasattr(voter, "get_current_user_id"), (
            "get_current_user_id should have been deleted in Task 6"
        )

    def test_no_notifier_attribute(self):
        """notifier= parameter is gone; voter should not have a 'notifier' attribute."""
        voter = _make_voter()
        assert not hasattr(voter, "notifier"), (
            "notifier attribute should have been removed in Task 6"
        )

    def test_manager_attribute_present(self):
        """manager is stored on the voter."""
        manager = _make_manager()
        voter = _make_voter(manager=manager)
        assert voter.manager is manager

    def test_no_schedule_cache_attribute(self):
        """voter must not have a 'schedule' attribute (cache was dropped in Task 3)."""
        voter = _make_voter()
        assert not hasattr(voter, "schedule"), (
            "'schedule' cache attribute should have been removed in Task 3"
        )


# ---------------------------------------------------------------------------
# Helpers for topic_name_matches tests
# ---------------------------------------------------------------------------

def _make_event_info(
    event_type: str = "Game",
    event_date: date = date(2099, 1, 1),
    weekday: str = "Wed",
    start_time: time = time(20, 0),
    end_time: time = time(22, 0),
) -> EventInfo:
    return EventInfo(
        event_type=event_type,
        event_date=event_date,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
    )


# ---------------------------------------------------------------------------
# Tests: topic_name_matches (stateless re-parse — Task 3)
# ---------------------------------------------------------------------------

class TestTopicNameMatchesStatelessReParse:
    """Tests that topic_name_matches re-parses event_schedule on every call (Task 3)."""

    def _make_voter_with_schedule(self, event_schedule: str) -> AutoPollVoterBot:
        user = UserRecord(
            id=1,
            session_name="alice",
            session_string="SESS",
            event_schedule=event_schedule,
            vote_delay_seconds=0,
            telegram_user_id=111,
            enabled=True,
            reminders_enabled=True,
            reminder_lead_hours=27,
        )
        return _make_voter(user=user)

    def test_live_reparse_after_schedule_mutation(self):
        """After bot.user.event_schedule = 'Game wed', topic_name_matches returns True for matching topic."""
        voter = self._make_voter_with_schedule("Training sat")

        # Set up parser to return a matching event info
        future_wed = date(2099, 1, 5)  # a Wednesday
        voter.event_info_parser.parse_line.return_value = _make_event_info(
            event_type="Game", event_date=future_wed, weekday="Wed", start_time=time(20, 0)
        )

        # Initially "Training sat" doesn't match "Game Wed" event
        assert voter.topic_name_matches("Game 2099-01-05, Wed, 20:00-22:00") is False

        # Mutate the schedule in-memory (simulates ScheduleEditor save)
        voter.user.event_schedule = "Game wed"

        # Now the schedule matches
        assert voter.topic_name_matches("Game 2099-01-05, Wed, 20:00-22:00") is True

    def test_empty_schedule_returns_false(self):
        """With empty event_schedule, topic_name_matches returns False for any topic."""
        voter = self._make_voter_with_schedule("")

        future_wed = date(2099, 1, 5)
        voter.event_info_parser.parse_line.return_value = _make_event_info(
            event_type="Game", event_date=future_wed, weekday="Wed", start_time=time(20, 0)
        )

        assert voter.topic_name_matches("Game 2099-01-05, Wed, 20:00-22:00") is False

    def test_unparseable_dsl_logs_and_returns_false(self, caplog):
        """Unparseable DSL (e.g., missing day) logs via log.exception and returns False without crashing."""
        # "Game" alone has no day — parse_schedule_dsl raises ValueError
        voter = self._make_voter_with_schedule("Game")

        with caplog.at_level(logging.ERROR, logger="forum-poll-voter.alice"):
            result = voter.topic_name_matches("Game 2099-01-05, Wed, 20:00-22:00")

        assert result is False
        # log.exception records at ERROR level
        assert any("Could not parse event_schedule" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Helpers for vote_in_thread_poll integration tests
# ---------------------------------------------------------------------------

def _make_voter_with_real_internals(
    user: Optional[UserRecord] = None,
    reminder_discovery=None,
) -> AutoPollVoterBot:
    """Make a voter where app.vote_poll and app.get_forum_topic are async mocks."""
    if user is None:
        user = _make_user()
    manager = _make_manager()
    common = _make_common()
    event_info_parser = MagicMock()

    with patch("src.auto_poll_voter_bot.Client") as mock_client_cls:
        mock_app = MagicMock()
        mock_app.vote_poll = AsyncMock()
        mock_app.get_forum_topic = AsyncMock()
        mock_client_cls.return_value = mock_app
        voter = AutoPollVoterBot(
            common=common,
            user=user,
            event_info_parser=event_info_parser,
            manager=manager,
            reminder_discovery=reminder_discovery,
        )
    return voter


def _make_full_poll_message(chat_id: int = -100, thread_id: int = 42, msg_id: int = 7) -> MagicMock:
    """Build a minimal poll message with chat, thread, and poll option."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.message_thread_id = thread_id
    msg.id = msg_id
    msg.poll = MagicMock()
    msg.poll.chosen_option_id = None
    option = MagicMock()
    option.text = "Go!"
    msg.poll.options = [option]
    return msg


# ---------------------------------------------------------------------------
# Tests: reminder_discovery integration in vote_in_thread_poll (Task 4)
# ---------------------------------------------------------------------------

class TestVoteInThreadPollReminderDiscovery:
    """Tests that vote_in_thread_poll wires in reminder_discovery correctly."""

    def _setup_voter_with_matching_topic(self, reminder_discovery=None):
        """Set up a voter that will match a topic and proceed to vote."""
        from datetime import date, time
        from src.event_info_parser import EventInfo

        user = _make_user()
        voter = _make_voter_with_real_internals(user=user, reminder_discovery=reminder_discovery)

        # topic name returns matching event
        topic = MagicMock()
        topic.name = "Game 2099-01-05, Wed, 20:00-22:00"
        voter.app.get_forum_topic = AsyncMock(return_value=topic)

        future_wed = date(2099, 1, 5)
        voter.event_info_parser.parse_line.return_value = EventInfo(
            event_type="Game",
            event_date=future_wed,
            weekday="Wed",
            start_time=time(20, 0),
            end_time=time(22, 0),
        )
        # Patch vote_poll to succeed silently
        voter.app.vote_poll = AsyncMock()
        # Patch send_vote_notification to avoid manager call
        voter.send_vote_notification = AsyncMock()
        return voter

    def test_record_from_vote_called_on_successful_vote(self):
        """After a successful vote_poll, record_from_vote is awaited with correct args."""
        discovery = MagicMock()
        discovery.record_from_vote = AsyncMock()

        voter = self._setup_voter_with_matching_topic(reminder_discovery=discovery)
        msg = _make_full_poll_message(chat_id=-100, thread_id=42, msg_id=7)

        asyncio.run(voter.vote_in_thread_poll(msg))

        discovery.record_from_vote.assert_awaited_once_with(
            "Game 2099-01-05, Wed, 20:00-22:00",
            -100,
            42,
            7,
        )

    def test_reminder_discovery_none_vote_succeeds(self):
        """When reminder_discovery=None, voting still completes without error (backward-compat)."""
        voter = self._setup_voter_with_matching_topic(reminder_discovery=None)
        msg = _make_full_poll_message()

        # Should not raise
        asyncio.run(voter.vote_in_thread_poll(msg))
        voter.app.vote_poll.assert_awaited_once()

    def test_record_from_vote_not_called_when_vote_poll_raises(self):
        """When vote_poll raises an exception, record_from_vote must NOT be called."""
        discovery = MagicMock()
        discovery.record_from_vote = AsyncMock()

        voter = self._setup_voter_with_matching_topic(reminder_discovery=discovery)
        voter.app.vote_poll = AsyncMock(side_effect=RuntimeError("Telegram error"))
        msg = _make_full_poll_message()

        # Should not raise — exception is caught inside vote_in_thread_poll
        asyncio.run(voter.vote_in_thread_poll(msg))

        discovery.record_from_vote.assert_not_awaited()
