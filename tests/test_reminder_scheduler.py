"""Tests for ReminderScheduler — 5-minute poller with housekeeping.

All Pyrogram types and repository methods are mocked.
datetime.now is monkey-patched to make the lead-time cutoff deterministic.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, call, patch
from zoneinfo import ZoneInfo

import pytest

from src.config import (
    CommonConfig,
    DatabaseConfig,
    GroupConfig,
    ManagerBotConfig,
    PyrogramConfig,
    ServerConfig,
)
from src.reminder_repository import DueReminder
from src.reminder_scheduler import POLL_INTERVAL_SECONDS, RETENTION_DAYS, ReminderScheduler
from src.voter_handle import VoterHandle
from src.user_repository import UserRecord

_UTC = ZoneInfo("UTC")

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_common(vote_option: str = "Go!") -> CommonConfig:
    return CommonConfig(
        pyrogram=PyrogramConfig(api_id=12345, api_hash="deadbeef"),
        group=GroupConfig(chat_id=-100, vote_option=vote_option),
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(port=8080),
        manager=ManagerBotConfig(bot_token="bot:TOKEN"),
    )


def _make_user_record(tg_id: int = 111) -> UserRecord:
    return UserRecord(
        id=1,
        session_name="alice",
        session_string="SESS",
        event_schedule="Game wed",
        vote_delay_seconds=5,
        telegram_user_id=tg_id,
        enabled=True,
        reminders_enabled=True,
        reminder_lead_hours=27,
    )


def _make_voter_handle(tg_id: int = 111) -> VoterHandle:
    user = _make_user_record(tg_id)
    client = MagicMock()
    client.get_messages = AsyncMock()
    return VoterHandle(user=user, client=client)


def _make_repo() -> MagicMock:
    repo = MagicMock()
    repo.fetch_due = MagicMock(return_value=[])
    repo.mark_reminded = MagicMock(return_value=1)
    repo.delete_old = MagicMock(return_value=0)
    return repo


def _make_manager_app() -> MagicMock:
    app = MagicMock()
    app.send_message = AsyncMock()
    return app


def _make_scheduler(
    repo=None,
    handles: Dict = None,
    manager_app=None,
    vote_option: str = "Go!",
) -> ReminderScheduler:
    if repo is None:
        repo = _make_repo()
    if handles is None:
        handles = {}
    if manager_app is None:
        manager_app = _make_manager_app()
    common = _make_common(vote_option=vote_option)
    return ReminderScheduler(
        common=common,
        repo=repo,
        handles=handles,
        manager_app=manager_app,
    )


# ---------------------------------------------------------------------------
# Helper: build a DueReminder with configurable event_datetime / lead_hours
# ---------------------------------------------------------------------------

def _make_due_reminder(
    id: int = 1,
    telegram_user_id: int = 111,
    chat_id: int = -100,
    topic_id: int = 42,
    poll_message_id: int = 7,
    topic_name: str = "Game 2099-01-05, Wed, 20:00-22:00",
    event_datetime: str = "2099-01-05 18:00:00",   # UTC
    reminder_lead_hours: int = 27,
) -> DueReminder:
    return DueReminder(
        id=id,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        topic_id=topic_id,
        poll_message_id=poll_message_id,
        topic_name=topic_name,
        event_datetime=event_datetime,
        reminder_lead_hours=reminder_lead_hours,
    )


# ---------------------------------------------------------------------------
# Helper: build a fake poll message with a chosen "Go!" option
# ---------------------------------------------------------------------------

def _make_poll_message_with_go(chosen_index: int = 0, options_texts=None) -> MagicMock:
    if options_texts is None:
        options_texts = ["Go!"]
    msg = MagicMock()
    option_mocks = []
    for t in options_texts:
        opt = MagicMock()
        opt.text = t
        option_mocks.append(opt)
    msg.poll = MagicMock()
    msg.poll.chosen_option_id = chosen_index
    msg.poll.options = option_mocks
    return msg


# ---------------------------------------------------------------------------
# "now" used in tests: 27h before the event so the row is exactly due
# event_datetime = "2099-01-05 18:00:00" UTC
# lead_hours = 27  → due when now >= event - 27h = 2099-01-04 15:00:00 UTC
# We set now = 2099-01-04 15:00:00 UTC (exactly on the boundary — still due)
# ---------------------------------------------------------------------------

_EVENT_UTC = datetime(2099, 1, 5, 18, 0, 0, tzinfo=_UTC)
_NOW_DUE = _EVENT_UTC - timedelta(hours=27)          # exactly on boundary
_NOW_NOT_DUE = _EVENT_UTC - timedelta(hours=27, minutes=1)  # 1 min before boundary


# ---------------------------------------------------------------------------
# Tests: _tick — sends reminder for a valid due row
# ---------------------------------------------------------------------------

class TestTickSendsReminder:
    def test_tick_fires_send_and_marks_reminded(self):
        """`_tick` sends a DM and calls mark_reminded for a valid due row."""
        row = _make_due_reminder(
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle = _make_voter_handle(tg_id=111)
        poll_msg = _make_poll_message_with_go(chosen_index=0, options_texts=["Go!"])
        handle.client.get_messages = AsyncMock(return_value=poll_msg)

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])

        manager_app = _make_manager_app()
        handles = {111: handle}
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime  # keep real strptime
            asyncio.run(scheduler._tick())

        manager_app.send_message.assert_awaited_once()
        send_call = manager_app.send_message.call_args
        # First positional arg is telegram_user_id
        assert send_call.args[0] == 111 or send_call.kwargs.get("chat_id") == 111
        repo.mark_reminded.assert_called_once_with(1)
        repo.delete_old.assert_called_once_with(RETENTION_DAYS)

    def test_reminder_text_contains_topic_name(self):
        """The DM text contains the topic name."""
        topic = "Game 2099-01-05, Wed, 20:00-22:00"
        row = _make_due_reminder(
            topic_name=topic,
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle = _make_voter_handle(tg_id=111)
        poll_msg = _make_poll_message_with_go(chosen_index=0, options_texts=["Go!"])
        handle.client.get_messages = AsyncMock(return_value=poll_msg)

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])
        manager_app = _make_manager_app()
        handles = {111: handle}
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        send_args = manager_app.send_message.call_args.args
        send_kwargs = manager_app.send_message.call_args.kwargs
        text = send_args[1] if len(send_args) > 1 else send_kwargs.get("text", "")
        assert topic in text


# ---------------------------------------------------------------------------
# Tests: _tick — Python-side lead-time filter (not yet due)
# ---------------------------------------------------------------------------

class TestTickLeadTimeFilter:
    def test_tick_skips_row_not_yet_within_lead_window(self):
        """`_tick` does NOT send or mark a row whose lead window hasn't opened yet."""
        row = _make_due_reminder(
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle = _make_voter_handle(tg_id=111)
        poll_msg = _make_poll_message_with_go()
        handle.client.get_messages = AsyncMock(return_value=poll_msg)

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])
        manager_app = _make_manager_app()
        handles = {111: handle}
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        # _NOW_NOT_DUE is 1 minute before the lead window opens
        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_NOT_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        manager_app.send_message.assert_not_awaited()
        repo.mark_reminded.assert_not_called()
        # delete_old still called
        repo.delete_old.assert_called_once_with(RETENTION_DAYS)


# ---------------------------------------------------------------------------
# Tests: _tick — missing handle
# ---------------------------------------------------------------------------

class TestTickMissingHandle:
    def test_tick_skips_missing_handle(self):
        """`_tick` skips a row whose telegram_user_id has no registered VoterHandle."""
        row = _make_due_reminder(
            telegram_user_id=999,  # not in handles
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])
        manager_app = _make_manager_app()
        handles = {}  # empty — no handle for user 999
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        manager_app.send_message.assert_not_awaited()
        repo.mark_reminded.assert_not_called()
        repo.delete_old.assert_called_once_with(RETENTION_DAYS)


# ---------------------------------------------------------------------------
# Tests: _tick — revocation checks
# ---------------------------------------------------------------------------

class TestTickRevocationCheck:
    def test_tick_marks_without_sending_when_poll_is_none(self):
        """`_tick` marks reminded without sending when get_messages returns a message with poll=None."""
        row = _make_due_reminder(
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle = _make_voter_handle(tg_id=111)
        msg_no_poll = MagicMock()
        msg_no_poll.poll = None
        handle.client.get_messages = AsyncMock(return_value=msg_no_poll)

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])
        manager_app = _make_manager_app()
        handles = {111: handle}
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        manager_app.send_message.assert_not_awaited()
        repo.mark_reminded.assert_called_once_with(1)

    def test_tick_marks_without_sending_when_chosen_option_not_go(self):
        """`_tick` marks without sending when chosen_option_id points at a non-Go option."""
        row = _make_due_reminder(
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle = _make_voter_handle(tg_id=111)
        # Options: ["No", "Go!"], but chosen_option_id=0 → "No" (not "Go!")
        poll_msg = _make_poll_message_with_go(chosen_index=0, options_texts=["No", "Go!"])
        handle.client.get_messages = AsyncMock(return_value=poll_msg)

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])
        manager_app = _make_manager_app()
        handles = {111: handle}
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        # "No" is not "Go!" → revocation → mark without send
        manager_app.send_message.assert_not_awaited()
        repo.mark_reminded.assert_called_once_with(1)

    def test_tick_marks_without_sending_when_get_messages_returns_none(self):
        """`_tick` marks without sending when get_messages returns None (message deleted)."""
        row = _make_due_reminder(
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle = _make_voter_handle(tg_id=111)
        handle.client.get_messages = AsyncMock(return_value=None)

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])
        manager_app = _make_manager_app()
        handles = {111: handle}
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        manager_app.send_message.assert_not_awaited()
        repo.mark_reminded.assert_called_once_with(1)

    def test_tick_marks_without_sending_when_get_messages_raises(self):
        """`_tick` marks without sending when get_messages raises an exception."""
        row = _make_due_reminder(
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle = _make_voter_handle(tg_id=111)
        handle.client.get_messages = AsyncMock(side_effect=RuntimeError("Telegram error"))

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])
        manager_app = _make_manager_app()
        handles = {111: handle}
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        manager_app.send_message.assert_not_awaited()
        repo.mark_reminded.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# Tests: _tick — send_message failure leaves reminded_at NULL
# ---------------------------------------------------------------------------

class TestTickSendFailure:
    def test_tick_leaves_reminded_at_null_when_send_raises(self):
        """`_tick` does NOT call mark_reminded when send_message raises."""
        row = _make_due_reminder(
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle = _make_voter_handle(tg_id=111)
        poll_msg = _make_poll_message_with_go(chosen_index=0, options_texts=["Go!"])
        handle.client.get_messages = AsyncMock(return_value=poll_msg)

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])
        manager_app = _make_manager_app()
        manager_app.send_message = AsyncMock(side_effect=RuntimeError("send failed"))
        handles = {111: handle}
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        repo.mark_reminded.assert_not_called()
        # delete_old still called despite send failure
        repo.delete_old.assert_called_once_with(RETENTION_DAYS)

    def test_second_row_fires_after_first_row_send_fails(self):
        """`_tick` continues processing subsequent rows even when an earlier send fails."""
        row1 = _make_due_reminder(
            id=1,
            telegram_user_id=111,
            topic_id=1,
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )
        row2 = _make_due_reminder(
            id=2,
            telegram_user_id=222,
            topic_id=2,
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle1 = _make_voter_handle(tg_id=111)
        poll_msg = _make_poll_message_with_go(chosen_index=0, options_texts=["Go!"])
        handle1.client.get_messages = AsyncMock(return_value=poll_msg)

        handle2 = _make_voter_handle(tg_id=222)
        handle2.client.get_messages = AsyncMock(return_value=poll_msg)

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row1, row2])

        manager_app = _make_manager_app()
        # First send fails, second succeeds
        manager_app.send_message = AsyncMock(
            side_effect=[RuntimeError("send failed for row1"), None]
        )

        handles = {111: handle1, 222: handle2}
        scheduler = _make_scheduler(repo=repo, handles=handles, manager_app=manager_app)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        # row1 send failed → not marked
        # row2 send succeeded → marked
        assert repo.mark_reminded.call_count == 1
        repo.mark_reminded.assert_called_once_with(2)
        assert manager_app.send_message.await_count == 2


# ---------------------------------------------------------------------------
# Tests: _tick — delete_old housekeeping
# ---------------------------------------------------------------------------

class TestTickDeleteOld:
    def test_tick_calls_delete_old_when_row_loop_completes_normally(self):
        """`_tick` always calls delete_old after the row loop (normal path)."""
        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[])
        scheduler = _make_scheduler(repo=repo)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        repo.delete_old.assert_called_once_with(RETENTION_DAYS)

    def test_tick_calls_delete_old_even_when_row_loop_raises(self):
        """`_tick` calls delete_old even if process_row raises (try/finally)."""
        row = _make_due_reminder(
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle = _make_voter_handle(tg_id=111)
        # get_messages raises — but process_row catches it and marks without sending.
        # To simulate the row loop itself raising, we patch _process_row to raise.
        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row])
        scheduler = _make_scheduler(repo=repo, handles={111: handle})

        async def boom(_row):
            raise RuntimeError("process_row exploded")

        scheduler._process_row = boom

        with pytest.raises(RuntimeError, match="process_row exploded"):
            asyncio.run(scheduler._tick())

        # delete_old must have been called despite the exception
        repo.delete_old.assert_called_once_with(RETENTION_DAYS)

    def test_tick_calls_delete_old_exactly_once_for_multiple_rows(self):
        """`_tick` calls delete_old exactly once even when multiple rows are processed."""
        row1 = _make_due_reminder(
            id=1,
            telegram_user_id=111,
            topic_id=1,
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )
        row2 = _make_due_reminder(
            id=2,
            telegram_user_id=222,
            topic_id=2,
            event_datetime=_EVENT_UTC.strftime("%Y-%m-%d %H:%M:%S"),
            reminder_lead_hours=27,
        )

        handle1 = _make_voter_handle(tg_id=111)
        handle2 = _make_voter_handle(tg_id=222)
        poll_msg = _make_poll_message_with_go(chosen_index=0, options_texts=["Go!"])
        handle1.client.get_messages = AsyncMock(return_value=poll_msg)
        handle2.client.get_messages = AsyncMock(return_value=poll_msg)

        repo = _make_repo()
        repo.fetch_due = MagicMock(return_value=[row1, row2])

        handles = {111: handle1, 222: handle2}
        scheduler = _make_scheduler(repo=repo, handles=handles)

        with patch("src.reminder_scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW_DUE
            mock_dt.strptime = datetime.strptime
            asyncio.run(scheduler._tick())

        # delete_old called exactly once regardless of how many rows were processed
        repo.delete_old.assert_called_once_with(RETENTION_DAYS)


# ---------------------------------------------------------------------------
# Tests: start / stop lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_stop_cancels_in_flight_task(self):
        """stop() cancels the background task and completes without error."""

        async def _run():
            scheduler = _make_scheduler()
            await scheduler.start()
            assert scheduler._task is not None
            assert not scheduler._task.done()
            await scheduler.stop()
            # After stop, task is gone
            assert scheduler._task is None

        asyncio.run(_run())

    def test_second_stop_is_no_op(self):
        """Calling stop() a second time does nothing (idempotent)."""

        async def _run():
            scheduler = _make_scheduler()
            await scheduler.start()
            await scheduler.stop()
            # Second stop — should not raise
            await scheduler.stop()

        asyncio.run(_run())

    def test_stop_before_start_is_no_op(self):
        """stop() before start() does nothing."""

        async def _run():
            scheduler = _make_scheduler()
            await scheduler.stop()  # No task started yet

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests: scheduler loop absorbs tick exceptions and continues
# ---------------------------------------------------------------------------

class TestSchedulerLoopResilience:
    def test_loop_absorbs_tick_exception_and_continues(self):
        """Scheduler loop logs exception from _tick and continues to the next iteration.

        Strategy: make _tick raise on the first call, succeed on the second,
        then set _stopping = True so the loop exits.
        Also asserts that asyncio.sleep is called after the failed tick so a
        regression removing the inter-tick sleep would be caught.
        """
        tick_call_count = 0
        sleep_call_count = 0

        async def _run():
            nonlocal tick_call_count, sleep_call_count
            scheduler = _make_scheduler()

            async def controlled_tick():
                nonlocal tick_call_count
                tick_call_count += 1
                if tick_call_count == 1:
                    raise RuntimeError("first tick failed")
                # Second tick succeeds; stop after it
                scheduler._stopping = True

            scheduler._tick = controlled_tick

            async def counting_sleep(_seconds):
                nonlocal sleep_call_count
                sleep_call_count += 1

            # Run the loop directly (not via start/stop, to keep the test synchronous)
            with patch("src.reminder_scheduler.asyncio.sleep", side_effect=counting_sleep) as mock_sleep:
                # Use a short timeout to avoid infinite loop in case of test bugs
                await asyncio.wait_for(scheduler._run(), timeout=5)
                # sleep must be called at least once (after the failed tick)
                assert mock_sleep.call_count >= 1, (
                    f"asyncio.sleep not called after tick exception; call_count={mock_sleep.call_count}"
                )

        asyncio.run(_run())
        assert tick_call_count == 2, f"Expected 2 tick calls, got {tick_call_count}"
        assert sleep_call_count >= 1, f"Expected sleep after exception tick, got {sleep_call_count}"
