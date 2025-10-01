import logging

from src.auto_poll_voter_bot import AutoPollVoterBot
from src.config import load_config_from_template
from src.event_info_parser import EventInfoParser

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("forum-poll-voter")


def create_bot() -> AutoPollVoterBot:
    config = load_config_from_template("config.yaml.j2")
    event_info_parser = EventInfoParser()
    return AutoPollVoterBot(
        config=config,
        event_info_parser=event_info_parser,
        workdir=".",
    )


if __name__ == "__main__":
    bot = create_bot()
    bot.run()
