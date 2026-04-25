"""Tests for src/reminder_discovery.py — Task 3.

Covers:
- prague_datetime_to_utc (summer/winter/DST-transition)
- chosen_option_is_go (all edge cases, no fallback-to-0)
- ReminderDiscovery.record_from_vote (happy path + guard paths)
"""
import asyncio
import logging
from datetime import date, time, datetime, timezone
from unittest.mock import MagicMock, patch
from typing import Optional

import pytest

from src.reminder_discovery import (
    ReminderDiscovery,
    chosen_option_is_go,
    prague_datetime_to_utc,
)
from src.user_repository import UserRecord

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(
    tg_id: Optional[int] = 42,
    reminders_enabled: bool = True,
    reminder_lead_hours: int = 27,
) -> UserRecord:
    return UserRecord(
        id=1,
        session_name="alice",
        session_string="SESS",
        event_schedule="Game wed",
        vote_delay_seconds=5,
        telegram_user_id=tg_id,
        enabled=True,
        reminders_enabled=reminders_enabled,
        reminder_lead_hours=reminder_lead_hours,
    )


def _make_parser():
    """Return a real EventInfoParser (no mock needed)."""
    from src.event_info_parser import EventInfoParser
    return EventInfoParser()


def _make_repo():
    repo = MagicMock()
    repo.upsert = MagicMock()
    return repo


def _make_poll_option(text: str):
    opt = MagicMock()
    opt.text = text
    return opt


def _make_poll(chosen_option_id, options_texts):
    poll = MagicMock()
    poll.chosen_option_id = chosen_option_id
    poll.options = [_make_poll_option(t) for t in options_texts]
    return poll


# ---------------------------------------------------------------------------
# prague_datetime_to_utc
# ---------------------------------------------------------------------------

class TestPragueDatetimeToUtc:
    def test_summer_cest_utc_minus_2(self):
        """CEST is UTC+2, so Prague 20:00 becomes UTC 18:00."""
        result = prague_datetime_to_utc(date(2025, 6, 15), time(20, 0))
        assert result.year == 2025
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 18
        assert result.minute == 0
        assert result.tzinfo is not None

    def test_winter_cet_utc_minus_1(self):
        """CET is UTC+1, so Prague 20:00 becomes UTC 19:00."""
        result = prague_datetime_to_utc(date(2025, 12, 15), time(20, 0))
        assert result.year == 2025
        assert result.month == 12
        assert result.day == 15
        assert result.hour == 19
        assert result.minute == 0

    def test_dst_transition_day_late_march(self):
        """Last Sunday in March 2025 clocks go forward at 02:00 CET -> 03:00 CEST.

        A time before the switch (e.g. 01:00 Prague) is still CET (UTC+1).
        A time after the switch (e.g. 03:30 Prague) is CEST (UTC+2).
        """
        # 2025 DST transition: 30 March 2025
        # 01:00 Prague (CET/UTC+1) -> UTC 00:00
        before_switch = prague_datetime_to_utc(date(2025, 3, 30), time(1, 0))
        assert before_switch.hour == 0
        assert before_switch.day == 30

        # 03:30 Prague (CEST/UTC+2) -> UTC 01:30
        after_switch = prague_datetime_to_utc(date(2025, 3, 30), time(3, 30))
        assert after_switch.hour == 1
        assert after_switch.minute == 30

    def test_result_is_utc_aware(self):
        """Return value must be timezone-aware (UTC)."""
        result = prague_datetime_to_utc(date(2025, 7, 1), time(12, 0))
        assert result.utcoffset().total_seconds() == 0

    def test_midnight_summer(self):
        """Prague midnight in summer: 00:00 CEST = 22:00 UTC previous day."""
        result = prague_datetime_to_utc(date(2025, 8, 1), time(0, 0))
        assert result.day == 31
        assert result.month == 7
        assert result.hour == 22


# ---------------------------------------------------------------------------
# chosen_option_is_go
# ---------------------------------------------------------------------------

class TestChosenOptionIsGo:
    def test_poll_none_returns_false(self):
        assert chosen_option_is_go(None, "Go!") is False

    def test_chosen_option_id_none_returns_false(self):
        poll = _make_poll(None, ["Go!", "No"])
        assert chosen_option_is_go(poll, "Go!") is False

    def test_index_out_of_range_returns_false(self):
        poll = _make_poll(5, ["Go!", "No"])
        assert chosen_option_is_go(poll, "Go!") is False

    def test_negative_index_returns_false(self):
        poll = _make_poll(-1, ["Go!", "No"])
        assert chosen_option_is_go(poll, "Go!") is False

    def test_matching_option_returns_true(self):
        poll = _make_poll(1, ["No", "Go!"])
        assert chosen_option_is_go(poll, "Go!") is True

    def test_case_insensitive_match(self):
        poll = _make_poll(0, ["go!"])
        assert chosen_option_is_go(poll, "GO!") is True

    def test_substring_match(self):
        """vote_option_text is a substring of the option text."""
        poll = _make_poll(0, ["Definitely Go! Yes"])
        assert chosen_option_is_go(poll, "Go!") is True

    def test_non_matching_option_returns_false(self):
        poll = _make_poll(0, ["No thanks"])
        assert chosen_option_is_go(poll, "Go!") is False

    def test_no_fallback_to_index_0(self):
        """Options=[No, Go], chosen=0 — index 0 text is 'No', must return False."""
        poll = _make_poll(0, ["No", "Go!"])
        assert chosen_option_is_go(poll, "Go!") is False

    def test_empty_options_list_returns_false(self):
        poll = _make_poll(0, [])
        assert chosen_option_is_go(poll, "Go!") is False

    def test_option_text_is_none_returns_false(self):
        """options[idx].text is None — guard against AttributeError."""
        opt = MagicMock()
        opt.text = None
        poll = MagicMock()
        poll.chosen_option_id = 0
        poll.options = [opt]
        assert chosen_option_is_go(poll, "Go!") is False

    def test_options_none_returns_false(self):
        """poll.options is None — guard."""
        poll = MagicMock()
        poll.chosen_option_id = 0
        poll.options = None
        assert chosen_option_is_go(poll, "Go!") is False


# ---------------------------------------------------------------------------
# ReminderDiscovery.record_from_vote
# ---------------------------------------------------------------------------

class TestRecordFromVote:
    def _make_discovery(self, user=None, repo=None):
        if user is None:
            user = _make_user()
        if repo is None:
            repo = _make_repo()
        return ReminderDiscovery(
            user=user,
            event_info_parser=_make_parser(),
            reminders=repo,
        )

    def test_happy_path_calls_upsert(self):
        repo = _make_repo()
        disc = self._make_discovery(repo=repo)
        asyncio.run(
            disc.record_from_vote(
                topic_name="Game 2099-09-30, Tue, 20:00-22:00",
                chat_id=100,
                topic_id=200,
                poll_message_id=300,
            )
        )
        repo.upsert.assert_called_once()
        call_kwargs = repo.upsert.call_args
        assert call_kwargs.args[0] == 42          # telegram_user_id
        assert call_kwargs.args[1] == 100         # chat_id
        assert call_kwargs.args[2] == 200         # topic_id
        assert call_kwargs.args[3] == 300         # poll_message_id
        assert call_kwargs.args[4] == "Game 2099-09-30, Tue, 20:00-22:00"  # topic_name
        # event_datetime_utc is a UTC-aware datetime for 20:00 Prague (CEST=UTC+2 in Sept)
        event_utc = call_kwargs.args[5]
        assert isinstance(event_utc, datetime)
        assert event_utc.utcoffset().total_seconds() == 0
        # Sept 30 20:00 Prague CEST = UTC 18:00
        assert event_utc.hour == 18
        assert event_utc.day == 30

    def test_telegram_user_id_none_skips_upsert(self, caplog):
        user = _make_user(tg_id=None)
        repo = _make_repo()
        disc = self._make_discovery(user=user, repo=repo)
        with caplog.at_level(logging.WARNING):
            asyncio.run(
                disc.record_from_vote(
                    topic_name="Game 2099-09-30, Tue, 20:00-22:00",
                    chat_id=100,
                    topic_id=200,
                    poll_message_id=300,
                )
            )
        repo.upsert.assert_not_called()
        assert any("telegram_user_id is None" in r.message for r in caplog.records)

    def test_unparseable_topic_name_skips_upsert(self, caplog):
        repo = _make_repo()
        disc = self._make_discovery(repo=repo)
        with caplog.at_level(logging.WARNING):
            asyncio.run(
                disc.record_from_vote(
                    topic_name="not a valid topic name at all",
                    chat_id=100,
                    topic_id=200,
                    poll_message_id=300,
                )
            )
        repo.upsert.assert_not_called()
        assert any("could not parse topic_name" in r.message for r in caplog.records)

    def test_unparseable_topic_name_does_not_propagate_exception(self):
        """No exception should escape record_from_vote on parse failure."""
        disc = self._make_discovery()
        # Should not raise
        asyncio.run(
            disc.record_from_vote(
                topic_name="!!!INVALID!!!",
                chat_id=100,
                topic_id=200,
                poll_message_id=300,
            )
        )

    def test_insert_happens_when_reminders_disabled(self):
        """reminders_enabled=False on the user must NOT prevent insert.

        The poller's SELECT is responsible for filtering; record_from_vote
        must be unconditional (except for the two guards above).
        """
        user = _make_user(reminders_enabled=False)
        repo = _make_repo()
        disc = self._make_discovery(user=user, repo=repo)
        asyncio.run(
            disc.record_from_vote(
                topic_name="Game 2099-09-30, Tue, 20:00-22:00",
                chat_id=100,
                topic_id=200,
                poll_message_id=300,
            )
        )
        repo.upsert.assert_called_once()

    def test_insert_happens_even_when_lead_time_short(self):
        """No too-late guard — insert even when event is imminent."""
        # reminder_lead_hours=9999 simulates a user who wants very early reminders,
        # but any vote cast with a near-future event should still be recorded.
        user = _make_user(reminder_lead_hours=9999)
        repo = _make_repo()
        disc = self._make_discovery(user=user, repo=repo)
        asyncio.run(
            disc.record_from_vote(
                topic_name="Game 2099-09-30, Tue, 20:00-22:00",
                chat_id=100,
                topic_id=200,
                poll_message_id=300,
            )
        )
        repo.upsert.assert_called_once()

    def test_upsert_receives_correct_utc_datetime_winter(self):
        """CET (UTC+1): Prague 20:00 on a winter date → UTC 19:00."""
        repo = _make_repo()
        disc = self._make_discovery(repo=repo)
        asyncio.run(
            disc.record_from_vote(
                topic_name="Game 2099-12-15, Mon, 20:00-22:00",
                chat_id=100,
                topic_id=200,
                poll_message_id=300,
            )
        )
        event_utc = repo.upsert.call_args[0][5]
        assert event_utc.hour == 19
        assert event_utc.day == 15
        assert event_utc.month == 12
