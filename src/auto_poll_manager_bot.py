import asyncio
import logging
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from src.config import CommonConfig
from src.schedule_editor import ScheduleEditor
from src.user_repository import UserRepository
from src.voter_handle import VoterHandle  # re-exported for backward compatibility

# Re-export VoterHandle so existing ``from src.auto_poll_manager_bot import VoterHandle`` still works.
__all__ = ["AutoPollManagerBot", "VoterHandle"]


class AutoPollManagerBot:
    """Telegram bot that lets registered users manage their autovoting config.

    Commands (DM-only, registered users only):
      /enable  — resume autovoting
      /disable — pause autovoting
      /status  — report voter liveness and current voting state

    The bot's ``app.send_message`` can be used by voter bots directly when
    sending vote notifications (replacing the old REST-based AutoPollNotifierBot).
    """

    def __init__(
        self,
        common: CommonConfig,
        repo: UserRepository,
    ) -> None:
        self.repo = repo
        self.log = logging.getLogger("forum-poll-voter.manager")
        self._handles: Dict[int, VoterHandle] = {}

        self.app = Client(
            name="manager",
            api_id=common.pyrogram.api_id,
            api_hash=common.pyrogram.api_hash,
            bot_token=common.manager.bot_token,
            in_memory=True,
        )
        self.app.add_handler(
            MessageHandler(self._handle_enable, filters.command("enable") & filters.private)
        )
        self.app.add_handler(
            MessageHandler(self._handle_disable, filters.command("disable") & filters.private)
        )
        self.app.add_handler(
            MessageHandler(self._handle_status, filters.command("status") & filters.private)
        )
        self._schedule_editor = ScheduleEditor(repo, self._handles)
        self._schedule_editor.register_handlers(self.app)

    async def _handle_enable(self, _client: Client, message: Message) -> None:
        """Handle /enable command: resume autovoting for the sender."""
        await self._set_voting(message, True)

    async def _handle_disable(self, _client: Client, message: Message) -> None:
        """Handle /disable command: pause autovoting for the sender."""
        await self._set_voting(message, False)

    async def _set_voting(self, message: Message, enabled: bool) -> None:
        """Shared /enable and /disable body: flip the flag in DB and in-memory."""
        action = "enable" if enabled else "disable"
        from_user_id = message.from_user.id if message.from_user else None
        self.log.info("/%s requested by user_id=%s", action, from_user_id)
        try:
            handle = await self._get_handle_or_reject(message)
            if handle is None:
                return
            telegram_user_id = handle.user.telegram_user_id
            if telegram_user_id is None:
                self.log.warning(
                    "telegram_user_id not set for session '%s' (%s); "
                    "backfill has not completed.",
                    handle.user.session_name,
                    action,
                )
                await message.reply_text("Internal error, try again.")
                return
            rows = self.repo.set_enabled(telegram_user_id, enabled)
            if rows == 0:
                self.log.warning(
                    "set_enabled returned 0 rows for telegram_user_id=%d (%s); "
                    "in-memory state updated anyway.",
                    telegram_user_id,
                    action,
                )
            handle.user.enabled = enabled
            self.log.info(
                "Autovoting %s for session '%s' (telegram_user_id=%d).",
                action + "d",
                handle.user.session_name,
                telegram_user_id,
            )
        except Exception:
            self.log.exception("Unexpected error in /%s handler.", action)
            await message.reply_text("Internal error, try again.")
            return
        await message.reply_text(
            f"Autovoting {'enabled' if enabled else 'disabled'}."
        )

    async def _handle_status(self, _client: Client, message: Message) -> None:
        """Handle /status command: report voter liveness and voting state."""
        from_user_id = message.from_user.id if message.from_user else None
        self.log.info("/status requested by user_id=%s", from_user_id)
        try:
            handle = await self._get_handle_or_reject(message)
            if handle is None:
                return

            # Probe voter client liveness.
            if not handle.client.is_connected:
                voter_line = "Voter: down (disconnected)"
            else:
                try:
                    await asyncio.wait_for(handle.client.get_me(), timeout=3)
                    voter_line = "Voter: up"
                except asyncio.TimeoutError:
                    voter_line = "Voter: down (timeout)"
                except Exception as e:
                    voter_line = f"Voter: down ({type(e).__name__})"

            voting_line = f"Voting: {'enabled' if handle.user.enabled else 'disabled'}"
            self.log.info(
                "Status for session '%s': %s; %s.",
                handle.user.session_name,
                voter_line,
                voting_line,
            )
            await message.reply_text(f"{voter_line}\n{voting_line}")
        except Exception:
            self.log.exception("Unexpected error in /status handler.")
            await message.reply_text("Internal error, try again.")

    def register_voter(self, telegram_user_id: int, handle: VoterHandle) -> None:
        """Register a voter handle keyed by the Telegram user ID.

        Raises ``ValueError`` if ``telegram_user_id`` is already registered
        (each session must map to exactly one voter).
        """
        if telegram_user_id in self._handles:
            raise ValueError(
                f"telegram_user_id {telegram_user_id} is already registered in the manager."
            )
        self._handles[telegram_user_id] = handle
        self.log.info("Registered voter for telegram_user_id=%d.", telegram_user_id)

    async def _get_handle_or_reject(self, message: Message) -> Optional[VoterHandle]:
        """Look up the sender in the registry.

        Returns the ``VoterHandle`` on a hit; on a miss replies with the
        standard "not registered" message and returns ``None``.
        """
        from_user_id = message.from_user.id if message.from_user else None
        if from_user_id is None or from_user_id not in self._handles:
            await message.reply_text(
                "You're not registered. Contact the administrator."
            )
            return None
        return self._handles[from_user_id]
