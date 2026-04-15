import logging
import sys

from pyrogram import compose

from src.auto_poll_notifier_bot import AutoPollNotifierBot
from src.auto_poll_voter_bot import AutoPollVoterBot
from src.config import load_config_from_template
from src.database import apply_migrations
from src.event_info_parser import EventInfoParser
from src.health_check import HealthCheckServer
from src.user_repository import get_enabled_users

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
# Disable Flask/Werkzeug request logging
logging.getLogger('werkzeug').setLevel(logging.ERROR)

log = logging.getLogger("forum-poll-voter")

if __name__ == "__main__":
    common = load_config_from_template("config.yaml.j2")

    # Apply any pending DB migrations before loading users
    apply_migrations(common.database.path)

    users = get_enabled_users(common.database.path)
    if not users:
        log.error("No enabled users found in the database. Add users to '%s' and restart.", common.database.path)
        sys.exit(1)

    log.info("Loaded %d enabled user(s) from database.", len(users))

    # Start health check server
    health_server = HealthCheckServer(config=common)
    health_server.start()

    # Initialize notifier if BOT_TOKEN is configured
    notifier = None
    if common.notification and common.notification.bot_token:
        notifier = AutoPollNotifierBot(bot_token=common.notification.bot_token)
        log.info("Notification bot initialized")

    event_info_parser = EventInfoParser()

    bots = [
        AutoPollVoterBot(
            common=common,
            user=user,
            event_info_parser=event_info_parser,
            notifier=notifier,
        )
        for user in users
    ]

    for bot in bots:
        health_server.register_client(bot.app)

    try:
        health_server.set_status(True, "Bot running")
        compose([bot.app for bot in bots])
    except Exception as e:
        log.error(f"Bot failed: {e}")
        health_server.set_status(False, f"Bot failed: {str(e)}")
        raise
