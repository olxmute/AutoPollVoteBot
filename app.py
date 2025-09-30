import asyncio
import configparser
import logging
from typing import List, Optional

from pyrogram import Client, filters
from pyrogram.types import Message, ForumTopic, PollOption

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("forum-poll-voter")

# ---------- Config ----------
cfg = configparser.ConfigParser()
cfg.read("config.ini")

API_ID = int(cfg["pyrogram"]["api_id"])
API_HASH = cfg["pyrogram"]["api_hash"]
SESSION_NAME = cfg["pyrogram"].get("session_name", "user")
TARGET_CHAT = int(cfg["group"]["chat_id"])
VOTE_OPTION = cfg["group"]["vote_option"]

# ---------- App ----------
app = Client(
    name=SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    workdir="."
)


# ---------- Your business rules ----------
def topic_name_matches(name: str) -> bool:
    """
    TODO: Replace this with your real conditions.
    Example: accept topics whose name starts with 'Vote: ' and contains 'Round 1'
    """
    return True


def choose_option(options: List[PollOption]) -> Optional[int]:
    """
    Decide which option to vote for.
    """
    for i, option in enumerate(options):
        if VOTE_OPTION.lower() in (option.text or "").lower():
            return i
    # fallback: pick the first option
    return 0 if options else None


async def get_topic_name(chat_id, thread_id) -> Optional[str]:
    """
    Fetch the forum topic by thread_id to read its name.
    """
    try:
        topic: ForumTopic = await app.get_forum_topic(chat_id, thread_id)
        return topic.name
    except Exception as e:
        log.warning("Could not fetch topic name for thread %s: %s", thread_id, e)
        return None


async def vote_in_thread_poll(message: Message) -> None:
    """
    Find the first message in the thread, ensure it's a poll, then vote.
    """
    if not message.chat or not message.message_thread_id:
        return

    # 1) Read the topic name and enforce your conditions
    topic_name = await get_topic_name(message.chat.id, message.message_thread_id)
    if not topic_name:
        log.info("Skipping; unknown topic name (thread %s).", message.message_thread_id)
        return

    if not topic_name_matches(topic_name):
        log.info("Topic '%s' doesn't match rules; skipping.", topic_name)
        return

    log.info("Topic matched: '%s' (thread %s).", topic_name, message.message_thread_id)

    # 2) Decide which option(s) to vote for
    options = message.poll.options or []
    choice_indices = choose_option(options)
    if choice_indices is None:
        log.warning("No choice indices computed; skipping vote.")
        return

    # 3) Vote (wait 5 seconds before sending the vote request)
    try:
        await asyncio.sleep(5)
        await app.vote_poll(message.chat.id, message.id, choice_indices)
        log.info(
            "Voted in poll (message %s) with options %s in topic '%s'.",
            message.id, choice_indices, topic_name
        )
    except Exception as e:
        log.error("Voting failed on poll %s: %s", message.id, e)


def forum_filter(_, __, message: Message) -> bool:
    # True if the message belongs to a topic (forum thread)
    return bool(getattr(message, "is_topic_message", False))


# ---------- Event handlers ----------
# We only care about messages in the target group, and only those that belong to a forum topic.
@app.on_message(filters.chat(TARGET_CHAT) & filters.create(forum_filter) & filters.poll)
async def on_forum_message(_, message: Message):
    print(f"handled message: ${str(message)}")
    """
    Triggered for every new message in the specified forum-enabled chat.
    """
    try:
        await vote_in_thread_poll(message)
    except Exception as e:
        log.exception("Handler crashed: %s", e)


if __name__ == "__main__":
    # Run the client
    app.run()

# async def main():
#     async with app:
#         async for dialog in app.get_dialogs():
#             chat = dialog.chat
#             print(f"{chat.id:<15}  {chat.type:<10}  {chat.title or chat.first_name or ''}")
#
# if __name__ == '__main__':
#     app.start()
#     for dialog in app.get_dialogs():
#         chat = dialog.chat
#         print(f"{chat.id:<15}  {chat.type:<10}  {chat.title or chat.first_name or ''}")
#     app.stop()
