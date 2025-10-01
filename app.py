import logging
import os

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
    health_port = int(os.getenv('PORT', '8080'))
    health_server = HealthCheckServer(port=health_port)
    health_server.start()

    config = load_config_from_template("config.yaml.j2")
    event_info_parser = EventInfoParser()
    bot = AutoPollVoterBot(config=config, event_info_parser=event_info_parser)

    try:
        health_server.set_status(True, "Bot running")
        bot.run()
    except Exception as e:
        log.error(f"Bot failed: {e}")
        health_server.set_status(False, f"Bot failed: {str(e)}")
        raise
