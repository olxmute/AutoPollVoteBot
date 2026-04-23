"""ReminderScheduler — 5-minute poller that sends due reminders and prunes old rows.

Responsibilities:
- Every POLL_INTERVAL_SECONDS: fetch candidate rows from ReminderRepository,
  apply the Python-side lead-time filter, run the revocation+send dance, mark rows.
- In try/finally after every tick: prune rows older than RETENTION_DAYS via
  repo.delete_old(), even if the row loop raises.
- Spawned as a single asyncio.Task via start(); cancelled cleanly by stop().
"""
import asyncio
import html
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from pyrogram import Client
from pyrogram.enums import ParseMode

from src.config import CommonConfig
from src.reminder_discovery import chosen_option_is_go, _UTC
from src.reminder_repository import DueReminder, ReminderRepository
from src.voter_handle import VoterHandle

POLL_INTERVAL_SECONDS = 300
RETENTION_DAYS = 7

_REMINDER_TEXT = "<b>Reminder</b>\n\nYou're signed up for: <b>{topic_name}</b>"


class ReminderScheduler:
    """Polls for due reminders, sends them, and prunes old rows.

    One instance is constructed inside AutoPollManagerBot and its lifecycle
    (start/stop) is managed by the outer main() function.

    Args:
        common:      Shared bot config (used for vote_option text in revocation check).
        repo:        ReminderRepository for DB access.
        handles:     Shared dict of registered VoterHandles (keyed by telegram_user_id).
        manager_app: The manager Pyrogram Client used to send DMs.
    """

    def __init__(
        self,
        common: CommonConfig,
        repo: ReminderRepository,
        handles: Dict[int, VoterHandle],
        manager_app: Client,
    ) -> None:
        self._common = common
        self._repo = repo
        self._handles = handles
        self._manager_app = manager_app
        self.log = logging.getLogger("forum-poll-voter.manager.reminders.scheduler")
        self._stopping: bool = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Spawn the poller loop as a background asyncio.Task."""
        self._stopping = False
        self._task = asyncio.get_running_loop().create_task(self._run())
        self.log.info("ReminderScheduler started (interval=%ds).", POLL_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """Cancel the background task and wait for it to finish.

        Safe to call multiple times; idempotent after the first call.
        """
        self._stopping = True
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self.log.info("ReminderScheduler stopped.")

    async def _run(self) -> None:
        """Poller loop — runs until _stopping is set or the task is cancelled.

        Intentionally ticks immediately on startup (before the first sleep) so
        any overdue reminders are processed right away rather than waiting a
        full POLL_INTERVAL_SECONDS after launch.
        """
        while not self._stopping:
            try:
                await self._tick()
            except Exception:
                self.log.exception("Reminder tick failed; continuing")
            if not self._stopping:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        """One tick: process due rows, then prune old rows (always)."""
        try:
            rows = self._repo.fetch_due()
            for row in rows:
                await self._process_row(row)
        finally:
            self._repo.delete_old(RETENTION_DAYS)

    async def _process_row(self, row: DueReminder) -> None:
        """Evaluate one candidate reminder row and send or mark-without-sending."""
        # Python-side lead-time filter
        event_utc = datetime.strptime(row.event_datetime, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC)
        now_utc = datetime.now(_UTC)
        if event_utc - now_utc > timedelta(hours=row.reminder_lead_hours):
            # Not yet within the lead window — skip until a later tick
            self.log.debug(
                "Reminder id=%d not yet due (event=%s, lead=%dh).",
                row.id,
                row.event_datetime,
                row.reminder_lead_hours,
            )
            return

        # Look up the voter handle for this user
        handle = self._handles.get(row.telegram_user_id)
        if handle is None:
            self.log.debug(
                "No VoterHandle for telegram_user_id=%d — skipping reminder id=%d.",
                row.telegram_user_id,
                row.id,
            )
            return

        # Revocation check: fetch the poll message and verify the Go vote is still present
        still_voted = await self._check_vote_still_cast(handle, row)
        if not still_voted:
            # Vote was revoked (or message gone) — stamp reminded_at without sending
            self.log.info(
                "Reminder id=%d: vote revoked or poll gone — marking without sending.",
                row.id,
            )
            self._repo.mark_reminded(row.id)
            return

        # Send the reminder DM
        text = _REMINDER_TEXT.format(topic_name=html.escape(row.topic_name))
        try:
            await self._manager_app.send_message(
                row.telegram_user_id,
                text,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            self.log.exception(
                "Failed to send reminder id=%d to telegram_user_id=%d — will retry next tick.",
                row.id,
                row.telegram_user_id,
            )
            # Leave reminded_at NULL so the row is retried on the next tick
            return

        self._repo.mark_reminded(row.id)
        self.log.info(
            "Sent reminder id=%d to telegram_user_id=%d for '%s'.",
            row.id,
            row.telegram_user_id,
            row.topic_name,
        )

    async def _check_vote_still_cast(self, handle: VoterHandle, row: DueReminder) -> bool:
        """Return True iff the poll message still has the Go option chosen.

        Returns False if the message is missing, the poll is None/gone, or
        the chosen option no longer matches the configured vote_option text.
        Any exception from get_messages is treated as False (revocation assumed).
        """
        try:
            msg = await handle.client.get_messages(row.chat_id, row.poll_message_id)
        except Exception:
            self.log.warning(
                "get_messages failed for reminder id=%d (chat_id=%d, msg_id=%d).",
                row.id,
                row.chat_id,
                row.poll_message_id,
            )
            return False

        if msg is None:
            return False

        poll = getattr(msg, "poll", None)
        return chosen_option_is_go(poll, self._common.group.vote_option)
