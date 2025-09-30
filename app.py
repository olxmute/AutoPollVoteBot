import configparser
import logging

from src.auto_poll_voter_bot import AutoPollVoterBot
from src.event_info_parser import EventInfoParser

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
ALLOWED_EVENT_TYPES = [
    event_type.strip().lower()
    for event_type in cfg["event"]["type"].split(",")
    if event_type.strip()
]
ALLOWED_EVENT_DAYS = [
    day.strip().lower()
    for day in cfg["event"]["days"].split(",")
    if day.strip()
]


def create_bot() -> AutoPollVoterBot:
    event_info_parser = EventInfoParser()
    return AutoPollVoterBot(
        api_id=API_ID,
        api_hash=API_HASH,
        session_name=SESSION_NAME,
        target_chat=TARGET_CHAT,
        vote_option=VOTE_OPTION,
        allowed_event_types=ALLOWED_EVENT_TYPES,
        allowed_event_days=ALLOWED_EVENT_DAYS,
        event_info_parser=event_info_parser,
        workdir=".",
    )


if __name__ == "__main__":
    bot = create_bot()
    bot.run()
