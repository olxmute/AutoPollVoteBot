"""Tests for AutoPollVoterBot.

Covers:
  - enabled guard: on_forum_message returns early when user.enabled is False
  - enabled guard: vote_in_thread_poll is called when user.enabled is True
  - send_vote_notification: calls manager.send_message with correct args
  - send_vote_notification: logs and does NOT re-raise when manager.send_message raises
  - send_vote_notification: logs warning and does NOT call manager.send_message when
    telegram_user_id is None
  - parse_topic: returns EventInfo on valid topic, None (with warning) on parse failure
  - matches_schedule: stateless re-parse of event_schedule on every call
  - matches_schedule: empty schedule, unparseable DSL, past event, no-match all return False
  - reminder_discovery: record_from_vote called on successful vote
  - reminder_discovery: None is backward-compatible
  - reminder_discovery: NOT called when vote_poll raises
"""
import asyncio
import logging
from datetime import date, time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup

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
# Helper: minimal EventInfo for send_vote_notification tests
# ---------------------------------------------------------------------------

def _make_event_info(
    event_type: str = "Game",
    event_date: date = date(2099, 1, 7),  # a Wednesday — matches default weekday="Wed"
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
# Tests: send_vote_notification
# ---------------------------------------------------------------------------

class TestSendVoteNotification:
    def test_sends_message_with_correct_args(self):
        """send_vote_notification calls manager.app.send_message with correct chat_id, text, parse_mode, and inline keyboard."""
        user = _make_user(enabled=True, telegram_user_id=111)
        manager = _make_manager()
        voter = _make_voter(user=user, manager=manager)

        event_info = _make_event_info(
            event_type="Game",
            event_date=date(2026, 5, 1),
            weekday="Fri",
            start_time=time(20, 0),
            end_time=time(22, 0),
        )
        asyncio.run(voter.send_vote_notification("Game 2026-05-01, Fri, 20:00-22:00", event_info))

        manager.app.send_message.assert_awaited_once()
        kwargs = manager.app.send_message.call_args.kwargs
        assert kwargs["chat_id"] == 111
        assert kwargs["text"] == "<b>Vote Notification</b>\n\nEvent: Game 2026-05-01, Fri, 20:00-22:00"
        assert kwargs["parse_mode"] == ParseMode.HTML

        keyboard = kwargs["reply_markup"]
        assert isinstance(keyboard, InlineKeyboardMarkup)
        assert len(keyboard.inline_keyboard) == 1
        row = keyboard.inline_keyboard[0]
        assert len(row) == 1
        button = row[0]
        assert button.text == "📅 Add to Google Calendar"
        assert button.url.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE&")

    def test_send_message_raises_is_logged_not_reraised(self):
        """When manager.app.send_message raises, send_vote_notification logs and does NOT re-raise."""
        user = _make_user(enabled=True, telegram_user_id=111)
        manager = _make_manager()
        manager.app.send_message = AsyncMock(side_effect=RuntimeError("connection lost"))
        voter = _make_voter(user=user, manager=manager)

        # Should not raise
        asyncio.run(voter.send_vote_notification("Game 2026-05-01, Fri, 20:00-22:00", _make_event_info()))
        manager.app.send_message.assert_awaited_once()

    def test_no_telegram_user_id_skips_send(self):
        """When user.telegram_user_id is None, send_vote_notification does NOT call manager.app.send_message."""
        user = _make_user(enabled=True, telegram_user_id=None)
        manager = _make_manager()
        voter = _make_voter(user=user, manager=manager)

        asyncio.run(voter.send_vote_notification("Game 2026-05-01, Fri, 20:00-22:00", _make_event_info()))

        manager.app.send_message.assert_not_awaited()

    def test_no_telegram_user_id_logs_warning(self, caplog):
        """When telegram_user_id is None, a warning is logged."""
        import logging
        user = _make_user(enabled=True, telegram_user_id=None)
        voter = _make_voter(user=user)

        with caplog.at_level(logging.WARNING, logger=f"forum-poll-voter.{user.session_name}"):
            asyncio.run(voter.send_vote_notification("some topic", _make_event_info()))

        assert any("telegram_user_id not set" in r.message for r in caplog.records)

    def test_send_raises_logs_error(self, caplog):
        """When manager.app.send_message raises, the error is logged."""
        import logging
        user = _make_user(enabled=True, telegram_user_id=111)
        manager = _make_manager()
        manager.app.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        voter = _make_voter(user=user, manager=manager)

        with caplog.at_level(logging.ERROR, logger=f"forum-poll-voter.{user.session_name}"):
            asyncio.run(voter.send_vote_notification("some topic", _make_event_info()))

        assert any("Failed to send vote notification" in r.message for r in caplog.records)

    def test_button_url_uses_event_info_times(self):
        """The button URL must encode the event_info date/times (not a hardcoded value).

        Uses a distinct date from `test_sends_message_with_correct_args` (2026-11-04
        is in CET, UTC+1) so accidental hardcoding of the May date in the URL
        builder would surface here.
        """
        user = _make_user(enabled=True, telegram_user_id=111)
        manager = _make_manager()
        voter = _make_voter(user=user, manager=manager)

        # 2026-11-04 is after Prague's CEST->CET switch (UTC+1): 20:00 Prague -> 19:00 UTC.
        event_info = _make_event_info(
            event_type="Game",
            event_date=date(2026, 11, 4),
            weekday="Wed",
            start_time=time(20, 0),
            end_time=time(22, 0),
        )
        asyncio.run(voter.send_vote_notification("Game 2026-11-04, Wed, 20:00-22:00", event_info))

        kwargs = manager.app.send_message.call_args.kwargs
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        assert "dates=20261104T190000Z/20261104T210000Z" in button.url


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
# Tests: parse_topic
# ---------------------------------------------------------------------------

class TestParseTopic:
    """parse_topic translates a topic-name string into Optional[EventInfo]."""

    def test_returns_event_info_on_valid_topic(self):
        """parse_topic returns the EventInfo produced by event_info_parser on success."""
        voter = _make_voter()
        parsed = _make_event_info(event_type="Game", event_date=date(2099, 1, 7), weekday="Wed")
        voter.event_info_parser.parse_line.return_value = parsed

        assert voter.parse_topic("Game 2099-01-07, Wed, 20:00-22:00") is parsed

    def test_unparseable_topic_returns_none(self, caplog):
        """When event_info_parser.parse_line raises, parse_topic logs a warning and returns None."""
        voter = _make_voter()
        voter.event_info_parser.parse_line.side_effect = ValueError("malformed topic")

        with caplog.at_level(logging.WARNING, logger="forum-poll-voter.alice"):
            result = voter.parse_topic("not a real topic")

        assert result is None
        assert any("didn't parse as event info" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: matches_schedule (stateless re-parse of event_schedule)
# ---------------------------------------------------------------------------

class TestMatchesSchedule:
    """Tests that matches_schedule re-parses event_schedule on every call and applies
    future-date + (type, day) checks."""

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

    def test_matches_when_type_and_day_align(self):
        """Returns True when type+day match a scheduled entry and the event is in the future."""
        voter = self._make_voter_with_schedule("Game wed")
        event_info = _make_event_info(
            event_type="Game", event_date=date(2099, 1, 7), weekday="Wed"
        )

        assert voter.matches_schedule(event_info) is True

    def test_live_reparse_after_schedule_mutation(self):
        """After mutating bot.user.event_schedule, matches_schedule reflects the change on the next call."""
        voter = self._make_voter_with_schedule("Training sat")
        event_info = _make_event_info(
            event_type="Game", event_date=date(2099, 1, 7), weekday="Wed"
        )

        # Initially "Training sat" doesn't match a Game/Wed event
        assert voter.matches_schedule(event_info) is False

        # Mutate the schedule in-memory (simulates ScheduleEditor save)
        voter.user.event_schedule = "Game wed"

        assert voter.matches_schedule(event_info) is True

    def test_empty_schedule_returns_false(self):
        """With empty event_schedule, matches_schedule returns False for any event."""
        voter = self._make_voter_with_schedule("")
        event_info = _make_event_info(
            event_type="Game", event_date=date(2099, 1, 7), weekday="Wed"
        )

        assert voter.matches_schedule(event_info) is False

    def test_unparseable_dsl_logs_and_returns_false(self, caplog):
        """Unparseable DSL (missing day) logs via log.exception and returns False without crashing."""
        # "Game" alone has no day — parse_schedule_dsl raises ValueError
        voter = self._make_voter_with_schedule("Game")
        event_info = _make_event_info(
            event_type="Game", event_date=date(2099, 1, 7), weekday="Wed"
        )

        with caplog.at_level(logging.ERROR, logger="forum-poll-voter.alice"):
            result = voter.matches_schedule(event_info)

        assert result is False
        # log.exception records at ERROR level
        assert any("Could not parse event_schedule" in r.message for r in caplog.records)

    def test_past_event_returns_false(self, caplog):
        """When event_date is in the past, matches_schedule logs and returns False."""
        voter = self._make_voter_with_schedule("Game wed")
        event_info = _make_event_info(
            event_type="Game", event_date=date(2000, 1, 5), weekday="Wed"  # past Wednesday
        )

        with caplog.at_level(logging.INFO, logger="forum-poll-voter.alice"):
            result = voter.matches_schedule(event_info)

        assert result is False
        assert any("is in past" in r.message for r in caplog.records)


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

    def test_send_vote_notification_called_with_parsed_event_info(self):
        """vote_in_thread_poll must await send_vote_notification with the parsed EventInfo.

        Pins the contract that the EventInfo returned by parse_topic is threaded
        into send_vote_notification (which builds the calendar URL).
        """
        voter = self._setup_voter_with_matching_topic(reminder_discovery=None)
        msg = _make_full_poll_message(chat_id=-100, thread_id=42, msg_id=7)

        asyncio.run(voter.vote_in_thread_poll(msg))

        voter.send_vote_notification.assert_awaited_once_with(
            "Game 2099-01-05, Wed, 20:00-22:00",
            voter.event_info_parser.parse_line.return_value,
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

    def test_parse_failure_skips_vote_and_notification(self):
        """When parse_topic returns None (topic name didn't parse), vote_in_thread_poll
        skips vote_poll, record_from_vote, and send_vote_notification.
        """
        discovery = MagicMock()
        discovery.record_from_vote = AsyncMock()

        voter = self._setup_voter_with_matching_topic(reminder_discovery=discovery)
        # Make parse_line raise so parse_topic returns None
        voter.event_info_parser.parse_line.side_effect = ValueError("not a topic")
        msg = _make_full_poll_message()

        asyncio.run(voter.vote_in_thread_poll(msg))

        voter.app.vote_poll.assert_not_awaited()
        discovery.record_from_vote.assert_not_awaited()
        voter.send_vote_notification.assert_not_awaited()

    def test_schedule_mismatch_skips_vote_and_notification(self):
        """When parse_topic succeeds but matches_schedule returns False, vote_in_thread_poll
        skips vote_poll, record_from_vote, and send_vote_notification.
        """
        discovery = MagicMock()
        discovery.record_from_vote = AsyncMock()

        voter = self._setup_voter_with_matching_topic(reminder_discovery=discovery)
        # Topic parses fine but its weekday ("Sat") doesn't match user.event_schedule ("Game wed")
        voter.event_info_parser.parse_line.return_value = EventInfo(
            event_type="Game",
            event_date=date(2099, 1, 10),  # a Saturday
            weekday="Sat",
            start_time=time(20, 0),
            end_time=time(22, 0),
        )
        msg = _make_full_poll_message()

        asyncio.run(voter.vote_in_thread_poll(msg))

        voter.app.vote_poll.assert_not_awaited()
        discovery.record_from_vote.assert_not_awaited()
        voter.send_vote_notification.assert_not_awaited()
