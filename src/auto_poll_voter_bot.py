import asyncio
import logging
from datetime import date
from typing import List, Optional

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message, ForumTopic, PollOption

from src.config import CommonConfig, ScheduledEvent
from src.schedule_dsl import parse_schedule_dsl
from src.user_repository import UserRecord

SAVED_MESSAGES_CHAT = "me"


class AutoPollVoterBot:
    def __init__(
            self,
            common: CommonConfig,
            user: UserRecord,
            event_info_parser,
            notifier=None,
    ):
        self.common = common
        self.user = user
        self.log = logging.getLogger(f"forum-poll-voter.{user.session_name}")
        self.schedule: List[ScheduledEvent] = [
            ScheduledEvent(**event) for event in parse_schedule_dsl(user.event_schedule)
        ]
        self.app = Client(
            name=user.session_name,
            api_id=common.pyrogram.api_id,
            api_hash=common.pyrogram.api_hash,
            session_string=user.session_string,
        )
        self.event_info_parser = event_info_parser
        self.notifier = notifier
        self._register_handlers()

    def _register_handlers(self) -> None:
        chat_filter = filters.chat(self.common.group.chat_id)
        forum_filter = filters.create(self.forum_filter)
        poll_filter = filters.poll
        self.app.add_handler(MessageHandler(self.log_incoming_message, filters.chat(SAVED_MESSAGES_CHAT)))
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

    def topic_name_matches(self, name: str) -> bool:
        """
        Accept topic names that:
          - parse into a valid EventInfo,
          - have event_date in the future (strictly greater than today).
          - match at least one scheduled event (type, day, and optionally start_time).
        """
        try:
            event_info = self.event_info_parser.parse_line(name)
        except Exception as exc:
            self.log.warning("Topic name didn't parse as event info: %r -> %s", name, exc)
            return False

        # Check date is in the future
        if event_info.event_date <= date.today():
            self.log.info("Topic '%s' is in past; skipping.", name)
            return False

        # Check if event matches any scheduled event
        event_type = event_info.event_type.lower()
        weekday = event_info.weekday.lower()

        for scheduled in self.schedule:
            if scheduled.type.lower() == event_type and scheduled.day.lower() == weekday:
                # If start_time is configured, it must match
                if scheduled.start_time is not None:
                    if event_info.start_time != scheduled.start_time:
                        self.log.info(
                            "Topic '%s' matches type and day but start_time differs (expected %s, got %s); skipping.",
                            name, scheduled.start_time, event_info.start_time
                        )
                        continue

                self.log.info("Topic '%s' matches schedule (type=%s, day=%s, start_time=%s).",
                         name, scheduled.type, scheduled.day, scheduled.start_time or "any")
                return True

        self.log.info("Topic '%s' doesn't match any scheduled event; skipping.", name)
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

        if not self.topic_name_matches(topic_name):
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

            # Send notification after successful vote
            await self.send_vote_notification(topic_name)
        except Exception as e:
            self.log.error("Voting failed on poll %s: %s", message.id, e)

    async def on_forum_message(self, client, message: Message):
        """
        Triggered for every new message in the specified forum-enabled chat.
        """
        try:
            await self.vote_in_thread_poll(message)
        except Exception as e:
            self.log.exception("Handler crashed: %s", e)

    async def log_incoming_message(self, client, message: Message) -> None:
        """Log "/ping" messages arriving in Saved Messages"""
        if message.text and message.text.strip() == "/ping":
            chat_id = getattr(message.chat, "id", None) if message.chat else None
            self.log.info("Received /ping in Saved Messages (%s)", chat_id)
            await client.send_message(SAVED_MESSAGES_CHAT, "pong")

    async def get_current_user_id(self):
        me = await self.app.get_me()
        return me.id

    async def send_vote_notification(self, topic_name: str) -> None:
        """
        Send a notification message about the vote via Telegram Bot API.

        Args:
            topic_name: The name of the forum topic where the vote occurred
        """
        if not self.notifier:
            return

        try:
            user_id = await self.get_current_user_id()
            message = f"<b>Vote Notification</b>\n\nEvent: {topic_name}"

            # Run synchronous requests call in a thread to avoid blocking
            await asyncio.to_thread(self.notifier.send_message, user_id, message)
        except Exception as e:
            self.log.error("Failed to send vote notification: %s", e)
