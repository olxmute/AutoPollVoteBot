"""Helpers and discovery class for recording reminders on autovote success.

Module-level helpers `prague_datetime_to_utc` and `chosen_option_is_go` are
shared with the poller (ReminderScheduler) and `prague_datetime_to_utc` is
also imported by `google_calendar_url`; they live here because this module is
imported by the voter bot, the scheduler, and the calendar URL builder.
"""
import logging
from datetime import datetime, time as dtime, date as ddate
from zoneinfo import ZoneInfo

from src.event_info_parser import EventInfoParser
from src.reminder_repository import ReminderRepository
from src.user_repository import UserRecord

_PRAGUE = ZoneInfo("Europe/Prague")
_UTC = ZoneInfo("UTC")


def prague_datetime_to_utc(event_date: ddate, event_time: dtime) -> datetime:
    """Combine a naive Prague local date + time and return as UTC-aware datetime."""
    naive = datetime.combine(event_date, event_time)
    return naive.replace(tzinfo=_PRAGUE).astimezone(_UTC)


def chosen_option_is_go(poll, vote_option_text: str) -> bool:
    """True iff poll.chosen_option_id points at an option whose text matches vote_option_text.

    Uses a case-insensitive substring match.  Has NO fallback to index 0 —
    this is intentional so the revocation check is strict (unlike
    AutoPollVoterBot.choose_option which falls back to 0 when nothing matches).
    """
    if poll is None or poll.chosen_option_id is None:
        return False
    options = poll.options or []
    idx = poll.chosen_option_id
    if not (0 <= idx < len(options)):
        return False
    text = (options[idx].text or "").lower()
    return vote_option_text.lower() in text


class ReminderDiscovery:
    """One instance per voter; records a reminder row when a vote succeeds."""

    def __init__(
        self,
        user: UserRecord,
        event_info_parser: EventInfoParser,
        reminders: ReminderRepository,
    ) -> None:
        self._user = user
        self._parser = event_info_parser
        self._reminders = reminders
        self.log = logging.getLogger(f"forum-poll-voter.{user.session_name}.reminders")

    async def record_from_vote(
        self,
        topic_name: str,
        chat_id: int,
        topic_id: int,
        poll_message_id: int,
    ) -> None:
        """Insert (or refresh) a reminder row for a successfully cast vote.

        Guards:
        - Skips (with a warning) if telegram_user_id is None (backfill not done yet).
        - Skips (with a warning) if topic_name cannot be parsed.

        Deliberately does NOT check reminders_enabled or lead time — those are
        the poller's responsibility.  See Technical Details § Discovery rule.
        """
        if self._user.telegram_user_id is None:
            self.log.warning(
                "record_from_vote: telegram_user_id is None for user %s — skipping",
                self._user.session_name,
            )
            return

        try:
            event_info = self._parser.parse_line(topic_name)
        except Exception as exc:
            self.log.warning(
                "record_from_vote: could not parse topic_name %r — skipping (%s)",
                topic_name,
                exc,
            )
            return

        event_datetime_utc = prague_datetime_to_utc(event_info.event_date, event_info.start_time)
        self._reminders.upsert(
            self._user.telegram_user_id,
            chat_id,
            topic_id,
            poll_message_id,
            topic_name,
            event_datetime_utc,
        )
