import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING, List, Optional

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, PollOption

if TYPE_CHECKING:
    from pyrogram.types import ForumTopic

    from src.auto_poll_manager_bot import AutoPollManagerBot
    from src.reminder_discovery import ReminderDiscovery

from src.config import CommonConfig, ScheduledEvent
from src.event_info_parser import EventInfo
from src.google_calendar_url import build_add_event_url
from src.schedule_dsl import parse_schedule_dsl
from src.user_repository import UserRecord


class AutoPollVoterBot:
    def __init__(
            self,
            common: CommonConfig,
            user: UserRecord,
            event_info_parser,
            manager: "AutoPollManagerBot",
            reminder_discovery: Optional["ReminderDiscovery"] = None,
    ):
        self.common = common
        self.user = user
        self.log = logging.getLogger(f"forum-poll-voter.{user.session_name}")
        self.app = Client(
            name=user.session_name,
            api_id=common.pyrogram.api_id,
            api_hash=common.pyrogram.api_hash,
            session_string=user.session_string,
        )
        self.event_info_parser = event_info_parser
        self.manager = manager
        self.reminder_discovery = reminder_discovery
        self._register_handlers()

    def _register_handlers(self) -> None:
        chat_filter = filters.chat(self.common.group.chat_id)
        forum_filter = filters.create(self.forum_filter)
        poll_filter = filters.poll
        self.app.add_handler(
            MessageHandler(
                self.on_forum_message,
                chat_filter & forum_filter & poll_filter,
            )
        )

    @staticmethod
    def forum_filter(_, __, message: Message) -> bool:
        # True if the message belongs to a topic (forum thread)
        return bool(getattr(message, "is_topic_message", False))

    def parse_topic(self, name: str) -> Optional[EventInfo]:
        """
        Parse a forum topic name into an EventInfo. Returns None and logs a
        warning on parse failure.
        """
        try:
            return self.event_info_parser.parse_line(name)
        except Exception as exc:
            self.log.warning("Topic name didn't parse as event info: %r -> %s", name, exc)
            return None

    def matches_schedule(self, event_info: EventInfo) -> bool:
        """
        True iff the event is in the future AND its (type, day) matches one of
        the user's scheduled entries.

        Re-parses self.user.event_schedule on every call so that schedule edits
        made via ScheduleEditor are immediately visible without a restart.
        """
        try:
            events = [ScheduledEvent(**e) for e in parse_schedule_dsl(self.user.event_schedule)]
        except Exception as exc:
            self.log.exception("Could not parse event_schedule: %r -> %s", self.user.event_schedule, exc)
            return False

        if event_info.event_date <= date.today():
            self.log.info("Event %s is in past; skipping.", event_info.event_date)
            return False

        event_type = event_info.event_type.lower()
        weekday = event_info.weekday.lower()

        for scheduled in events:
            if scheduled.type.lower() == event_type and scheduled.day.lower() == weekday:
                self.log.info("Event matches schedule (type=%s, day=%s).", scheduled.type, scheduled.day)
                return True

        self.log.info("Event (type=%s, day=%s) doesn't match any scheduled entry; skipping.",
                      event_info.event_type, event_info.weekday)
        return False

    def choose_option(self, options: List[PollOption]) -> Optional[int]:
        """
        Decide which option to vote for.
        """
        for i, option in enumerate(options):
            if self.common.group.vote_option.lower() in (option.text or "").lower():
                return i
        # fallback: pick the first option
        return 0 if options else None

    async def get_topic_name(self, chat_id, thread_id) -> Optional[str]:
        """
        Fetch the forum topic by thread_id to read its name.
        """
        try:
            topic: ForumTopic = await self.app.get_forum_topic(chat_id, thread_id)
            return topic.name
        except Exception as e:
            self.log.warning("Could not fetch topic name for thread %s: %s", thread_id, e)
            return None

    async def vote_in_thread_poll(self, message: Message) -> None:
        """
        Ensure the message is a poll, then vote if not voted yet.
        """
        if not message.chat or not message.message_thread_id:
            return

        # 1) Read the topic name and enforce your conditions
        topic_name = await self.get_topic_name(message.chat.id, message.message_thread_id)
        if not topic_name:
            self.log.info("Skipping; unknown topic name (thread %s).", message.message_thread_id)
            return

        event_info = self.parse_topic(topic_name)
        if event_info is None:
            return
        if not self.matches_schedule(event_info):
            return

        self.log.info("Topic matched: '%s' (thread %s).", topic_name, message.message_thread_id)

        # 2) Skip voting if already voted
        if message.poll.chosen_option_id is not None:
            self.log.info("Already voted; skipping vote. " + str(message.poll.chosen_option_id))
            return

        # 3) Decide which option(s) to vote for
        options = message.poll.options or []
        choice_index = self.choose_option(options)
        if choice_index is None:
            self.log.warning("No choice indices computed; skipping vote.")
            return

        # 4) Vote (wait for configured delay before sending the vote request)
        try:
            await asyncio.sleep(self.user.vote_delay_seconds)
            await self.app.vote_poll(message.chat.id, message.id, choice_index)
            self.log.info(
                "Voted in poll (message %s) with options %s in topic '%s'.",
                message.id,
                choice_index,
                topic_name,
            )
        except Exception as e:
            self.log.error("Voting failed on poll %s: %s", message.id, e)
            return

        # Record reminder for successful vote (isolated so a DB error does not
        # suppress the vote notification that follows)
        if self.reminder_discovery is not None:
            try:
                await self.reminder_discovery.record_from_vote(
                    topic_name,
                    message.chat.id,
                    message.message_thread_id,
                    message.id,
                )
            except Exception as e:
                self.log.error("Failed to record reminder after vote on poll %s: %s", message.id, e)

        # Send notification after successful vote
        await self.send_vote_notification(topic_name, event_info)

    async def on_forum_message(self, client, message: Message):
        """
        Triggered for every new message in the specified forum-enabled chat.
        """
        if not self.user.enabled:
            self.log.debug("Autovoting disabled; ignoring.")
            return
        try:
            await self.vote_in_thread_poll(message)
        except Exception as e:
            self.log.exception("Handler crashed: %s", e)

    async def send_vote_notification(self, topic_name: str, event_info: EventInfo) -> None:
        """
        Send a notification message about the vote via the manager bot.

        The message includes an inline ``URL`` button that opens Google
        Calendar's new-event sheet prefilled with the event title and times.

        Args:
            topic_name: The name of the forum topic where the vote occurred
            event_info: Parsed event details used to build the calendar URL
        """
        if self.user.telegram_user_id is None:
            self.log.warning(
                "Skipping notification: telegram_user_id not set (backfill didn't run)."
            )
            return

        text = f"<b>Vote Notification</b>\n\nEvent: {topic_name}"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📅 Add to Google Calendar", url=build_add_event_url(event_info))]]
        )
        try:
            await self.manager.app.send_message(
                chat_id=self.user.telegram_user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception as e:
            self.log.error("Failed to send vote notification: %s", e)
