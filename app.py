import asyncio
import logging
import sqlite3

from src.auto_poll_manager_bot import AutoPollManagerBot, VoterHandle
from src.auto_poll_voter_bot import AutoPollVoterBot
from src.config import load_config_from_template
from src.database import apply_migrations
from src.event_info_parser import EventInfoParser
from src.health_check import HealthCheckServer
from src.reminder_discovery import ReminderDiscovery
from src.reminder_repository import ReminderRepository
from src.user_repository import UserRepository

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
# Disable Flask/Werkzeug request logging
logging.getLogger('werkzeug').setLevel(logging.ERROR)

log = logging.getLogger("forum-poll-voter")


async def main(common, repo, health_server):
    """
    Orchestrate startup, run forever, and shut down cleanly.

    Start order:
      1. Construct all Pyrogram clients inside the running asyncio loop.
      2. Start all voter clients.
      3. Backfill telegram_user_id and register each voter in the manager.
      4. Start manager bot.
      5. Health server set to healthy.
      6. Block on an Event until cancelled (SIGINT/Ctrl+C triggers finally).

    Shutdown order (finally block):
      manager first, then voters (reverse of start).
    """
    started_voters = []
    manager_started = False

    event_info_parser = EventInfoParser()

    # Construct the reminder repository.
    reminder_repo = ReminderRepository(common.database.path)

    # Construct the manager bot inside the running event loop.
    manager_bot = AutoPollManagerBot(common, repo, reminder_repo)

    # Load enabled users after migrations have already run.
    users = repo.get_enabled_users()
    if not users:
        raise RuntimeError(
            f"No enabled users found in the database. Add users to '{common.database.path}' and restart."
        )

    log.info("Loaded %d enabled user(s) from database.", len(users))

    # Construct one voter per enabled user inside the running event loop.
    voter_bots = [
        AutoPollVoterBot(
            common=common,
            user=user,
            event_info_parser=event_info_parser,
            manager=manager_bot,
            reminder_discovery=ReminderDiscovery(
                user=user,
                event_info_parser=event_info_parser,
                reminders=reminder_repo,
            ),
        )
        for user in users
    ]

    # Register all clients (voters + manager) with the health server.
    for bot in voter_bots:
        health_server.register_client(bot.app)
    health_server.register_client(manager_bot.app)

    try:
        # 1. Start each voter client (track which ones started for safe cleanup).
        for bot in voter_bots:
            await bot.app.start()
            started_voters.append(bot)

        # 2. Backfill telegram_user_id and register voters.
        for bot in voter_bots:
            try:
                me = await bot.app.get_me()
            except Exception as e:
                log.error(
                    "Failed to get Telegram identity for session '%s': %s — cannot continue.",
                    bot.user.session_name, e,
                )
                raise

            try:
                repo.set_telegram_user_id(bot.user.id, me.id)
            except sqlite3.IntegrityError as e:
                log.error(
                    "Integrity error while backfilling telegram_user_id=%d for session '%s': %s "
                    "— two session strings for the same Telegram account?",
                    me.id, bot.user.session_name, e,
                )
                raise

            bot.user.telegram_user_id = me.id
            handle = VoterHandle(user=bot.user, client=bot.app)
            manager_bot.register_voter(me.id, handle)
            log.info("Backfilled telegram_user_id=%d for session '%s'.", me.id, bot.user.session_name)

        # 3. Start manager bot (registry is fully populated now).
        await manager_bot.app.start()
        manager_started = True

        # 4. Start reminder scheduler (after manager client is ready to send DMs).
        await manager_bot.start_scheduler()

        # 5. Signal health server that everything is up.
        health_server.set_status(True, "Bot running")

        # 6. Block until cancelled.
        await asyncio.Event().wait()

    finally:
        # Graceful shutdown: scheduler first, then manager, then voters.
        if manager_started:
            try:
                await manager_bot.stop_scheduler()
            except Exception as e:
                log.warning("Error stopping reminder scheduler: %s", e)
            try:
                await manager_bot.app.stop()
            except Exception as e:
                log.warning("Error stopping manager bot: %s", e)

        for bot in reversed(started_voters):
            try:
                await bot.app.stop()
            except Exception as e:
                log.warning("Error stopping voter bot '%s': %s", bot.user.session_name, e)


if __name__ == "__main__":
    common = load_config_from_template("config.yaml.j2")

    # Apply any pending DB migrations before loading users.
    apply_migrations(common.database.path)

    repo = UserRepository(common.database.path)

    # Start health check server.
    health_server = HealthCheckServer(config=common)
    health_server.start()

    try:
        asyncio.run(main(common, repo, health_server))
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down.")
    except asyncio.CancelledError:
        log.info("Cancelled.")
    except Exception as e:
        log.error("Bot failed: %s", e)
        health_server.set_status(False, f"Bot failed: {str(e)}")
        raise
