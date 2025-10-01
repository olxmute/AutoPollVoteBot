import asyncio
import logging
from datetime import date
from typing import List, Optional

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message, ForumTopic, PollOption

from src.config import AppConfig

log = logging.getLogger("forum-poll-voter")


class AutoPollVoterBot:
    def __init__(
            self,
            config: AppConfig,
            event_info_parser,
            workdir: str = ".",
    ):
        self.config = config
        self.app = Client(
            name=config.pyrogram.session_name,
            api_id=config.pyrogram.api_id,
            api_hash=config.pyrogram.api_hash,
            workdir=workdir,
        )
        self.event_info_parser = event_info_parser
        self._register_handlers()

    def _register_handlers(self) -> None:
        chat_filter = filters.chat(self.config.group.chat_id)
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
            log.warning("Topic name didn't parse as event info: %r -> %s", name, exc)
            return False

        # Check date is in the future
        if event_info.event_date <= date.today():
            log.info("Topic '%s' is in past; skipping.", name)
            return False

        # Check if event matches any scheduled event
        event_type = event_info.event_type.lower()
        weekday = event_info.weekday.lower()

        for scheduled in self.config.event.schedule:
            if scheduled.type.lower() == event_type and scheduled.day.lower() == weekday:
                # If start_time is configured, it must match
                if scheduled.start_time is not None:
                    if event_info.start_time != scheduled.start_time:
                        log.info(
                            "Topic '%s' matches type and day but start_time differs (expected %s, got %s); skipping.",
                            name, scheduled.start_time, event_info.start_time
                        )
                        continue

                log.info("Topic '%s' matches schedule (type=%s, day=%s, start_time=%s).",
                         name, scheduled.type, scheduled.day, scheduled.start_time or "any")
                return True

        log.info("Topic '%s' doesn't match any scheduled event; skipping.", name)
        return False

    def choose_option(self, options: List[PollOption]) -> Optional[int]:
        """
        Decide which option to vote for.
        """
        for i, option in enumerate(options):
            if self.config.group.vote_option.lower() in (option.text or "").lower():
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
            log.warning("Could not fetch topic name for thread %s: %s", thread_id, e)
            return None

    async def vote_in_thread_poll(self, message: Message) -> None:
        """
        Find the first message in the thread, ensure it's a poll, then vote.
        """
        if not message.chat or not message.message_thread_id:
            return

        # 1) Read the topic name and enforce your conditions
        topic_name = await self.get_topic_name(message.chat.id, message.message_thread_id)
        if not topic_name:
            log.info("Skipping; unknown topic name (thread %s).", message.message_thread_id)
            return

        if not self.topic_name_matches(topic_name):
            return

        log.info("Topic matched: '%s' (thread %s).", topic_name, message.message_thread_id)

        # 2) Decide which option(s) to vote for
        options = message.poll.options or []
        choice_index = self.choose_option(options)
        if choice_index is None:
            log.warning("No choice indices computed; skipping vote.")
            return

        # 3) Vote (wait 5 seconds before sending the vote request)
        try:
            await asyncio.sleep(5)
            # skip voting if already voted
            await self.app.vote_poll(message.chat.id, message.id, choice_index)
            log.info(
                "Voted in poll (message %s) with options %s in topic '%s'.",
                message.id,
                choice_index,
                topic_name,
            )
        except Exception as e:
            log.error("Voting failed on poll %s: %s", message.id, e)

    async def on_forum_message(self, client, message: Message):
        """
        Triggered for every new message in the specified forum-enabled chat.
        """
        try:
            await self.vote_in_thread_poll(message)
        except Exception as e:
            log.exception("Handler crashed: %s", e)

    def run(self) -> None:
        # Run the client
        self.app.run()
