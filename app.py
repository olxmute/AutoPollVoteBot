import logging

from src.auto_poll_notifier_bot import AutoPollNotifierBot
from src.auto_poll_voter_bot import AutoPollVoterBot
from src.config import load_config_from_template
from src.event_info_parser import EventInfoParser
from src.health_check import HealthCheckServer

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
# Disable Flask/Werkzeug request logging
logging.getLogger('werkzeug').setLevel(logging.ERROR)

log = logging.getLogger("forum-poll-voter")

if __name__ == "__main__":
    # Start health check server
    config = load_config_from_template("config.yaml.j2")

    health_server = HealthCheckServer(config=config)
    health_server.start()

    # Initialize notifier if BOT_TOKEN is configured
    notifier = None
    if config.notification and config.notification.bot_token:
        notifier = AutoPollNotifierBot(bot_token=config.notification.bot_token)
        log.info("Notification bot initialized")

    event_info_parser = EventInfoParser()
    bot = AutoPollVoterBot(
        config=config,
        event_info_parser=event_info_parser,
        health_server=health_server,
        notifier=notifier
    )

    try:
        health_server.set_status(True, "Bot running")
        bot.run()
    except Exception as e:
        log.error(f"Bot failed: {e}")
        health_server.set_status(False, f"Bot failed: {str(e)}")
        raise
