"""RemindersEditor — inline-keyboard UI for managing per-user reminder settings.

Owns the /reminders command and all rem:* callback routes, plus the free-text
reply handler for the lead-time input.  Constructed with a UserRepository and
a reference to the shared _handles dict so it sees newly-registered voters
automatically.
"""
import logging
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.user_repository import UserRepository
from src.voter_handle import VoterHandle

# Cancellation cutoff is 26h before event; reminder must fire earlier.
MIN_LEAD_HOURS = 27
# Upper bound: 30 days; values beyond this would never fire (reminder_repository only fetches
# rows within the lead window, so a huge value means the row is always "not yet due").
MAX_LEAD_HOURS = 720


class RemindersEditor:
    """Inline-keyboard UI for viewing and editing a user's reminder settings.

    Registered in the manager bot via ``register_handlers(app)``.
    Auth is handled via the shared ``_handles`` dict — the same object
    maintained by ``AutoPollManagerBot``.
    """

    def __init__(self, repo: UserRepository, handles: Dict[int, VoterHandle]) -> None:
        self.repo = repo
        self._handles = handles
        self.log = logging.getLogger("forum-poll-voter.manager.reminders")
        # Maps from_user_id → message_id of the ForceReply prompt we sent.
        self._pending_lead_input: Dict[int, int] = {}

    def register_handlers(self, app: Client) -> None:
        """Register the /reminders command, rem:* callback, and lead-time reply handlers."""
        app.add_handler(
            MessageHandler(
                self._on_reminders_cmd,
                filters.command("reminders") & filters.private,
            )
        )
        app.add_handler(
            CallbackQueryHandler(
                self._on_callback,
                filters.regex(r"^rem:") & filters.private,
            )
        )
        app.add_handler(
            MessageHandler(
                self._on_lead_time_reply,
                filters.private & filters.reply & filters.text,
            )
        )

    # ------------------------------------------------------------------
    # Auth helper
    # ------------------------------------------------------------------

    def _lookup_handle(self, from_user_id: Optional[int]) -> Optional[VoterHandle]:
        """Return VoterHandle for the given user ID, or None if not registered."""
        return self._handles.get(from_user_id)

    # ------------------------------------------------------------------
    # /reminders command
    # ------------------------------------------------------------------

    async def _on_reminders_cmd(self, _client: Client, message: Message) -> None:
        """Handle /reminders command: show the main reminders screen."""
        from_user_id = message.from_user.id if message.from_user else None
        handle = self._lookup_handle(from_user_id)
        if handle is None:
            await message.reply_text("You're not registered. Contact the administrator.")
            return
        self.log.info(
            "/reminders opened by user_id=%s (session '%s').",
            from_user_id,
            handle.user.session_name,
        )
        text, markup = self._build_main_screen(handle)
        await message.reply_text(text, reply_markup=markup)

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _build_main_screen(self, handle: VoterHandle):
        """Return (text, markup) for the main reminders screen."""
        status = "ON" if handle.user.reminders_enabled else "OFF"
        hours = handle.user.reminder_lead_hours
        text = f"Reminders: {status}\n{hours} hours before the event"

        toggle_label = "Disable" if handle.user.reminders_enabled else "Enable"
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(toggle_label, callback_data="rem:toggle"),
                InlineKeyboardButton("Change timing", callback_data="rem:lead"),
            ],
            [
                InlineKeyboardButton("✖ Close", callback_data="rem:close"),
            ],
        ])
        return text, markup

    # ------------------------------------------------------------------
    # Callback dispatcher
    # ------------------------------------------------------------------

    async def _on_callback(self, _client: Client, query: CallbackQuery) -> None:
        """Dispatch all rem:* callback_data to the appropriate handler."""
        try:
            handle = self._lookup_handle(query.from_user.id if query.from_user else None)
            if handle is None:
                await query.answer("You're not registered.", show_alert=True)
                return

            data = query.data or ""
            tokens = data.split(":")

            # rem:main
            if tokens == ["rem", "main"]:
                text, markup = self._build_main_screen(handle)
                await query.message.edit_text(text, reply_markup=markup)
                await query.answer()
                return

            # rem:close
            if tokens == ["rem", "close"]:
                await query.message.edit_reply_markup(None)
                await query.answer()
                return

            # rem:toggle
            if tokens == ["rem", "toggle"]:
                await self._handle_toggle(query, handle)
                return

            # rem:lead
            if tokens == ["rem", "lead"]:
                await self._handle_lead(query, handle)
                return

            # Unrecognized shape → error
            await query.answer("Internal error, try again.", show_alert=True)

        except Exception:
            self.log.exception("Unexpected error in reminders callback handler")
            try:
                await query.answer("Internal error, try again.", show_alert=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Toggle handler
    # ------------------------------------------------------------------

    async def _handle_toggle(self, query: CallbackQuery, handle: VoterHandle) -> None:
        """Flip reminders_enabled, persist to DB, and re-render main screen."""
        tg_id = handle.user.telegram_user_id
        if tg_id is None:
            self.log.warning(
                "telegram_user_id not set for session '%s' (toggle); backfill incomplete",
                handle.user.session_name,
            )
            await query.answer("Internal error, try again.", show_alert=True)
            return

        new_enabled = not handle.user.reminders_enabled
        rows = self.repo.set_reminders_enabled(tg_id, new_enabled)
        if rows == 0:
            self.log.warning(
                "set_reminders_enabled affected 0 rows for tg_id=%d", tg_id
            )
            await query.answer("Save failed, try again.", show_alert=True)
            return

        handle.user.reminders_enabled = new_enabled
        self.log.info(
            "Reminders %s for session '%s' (tg_id=%d).",
            "enabled" if new_enabled else "disabled",
            handle.user.session_name,
            tg_id,
        )
        text, markup = self._build_main_screen(handle)
        await query.message.edit_text(text, reply_markup=markup)
        await query.answer()

    # ------------------------------------------------------------------
    # Lead-time prompt handler
    # ------------------------------------------------------------------

    async def _handle_lead(self, query: CallbackQuery, handle: VoterHandle) -> None:
        """Send a ForceReply prompt for the new lead time and stash the prompt message id."""
        from_user_id = query.from_user.id if query.from_user else None
        if from_user_id is None:
            await query.answer("Internal error, try again.", show_alert=True)
            return
        hours = handle.user.reminder_lead_hours
        prompt = await query.message.reply_text(
            f"Reply with how many hours before the event to send the reminder "
            f"(current: {hours}, minimum: {MIN_LEAD_HOURS}).",
            reply_markup=ForceReply(selective=True),
        )
        # Overwrite any previous pending entry for this user
        self._pending_lead_input[from_user_id] = prompt.id
        await query.answer()

    # ------------------------------------------------------------------
    # Lead-time re-prompt helper
    # ------------------------------------------------------------------

    async def _reprompt_lead(self, message: Message, from_user_id: int, error: str) -> None:
        """Send a new ForceReply prompt and rotate the tracked pending id.

        If reply_text raises, the assignment to _pending_lead_input never executes,
        so the pending entry keeps pointing at the previous prompt id — the user can
        still reply to the original ForceReply and the gate will accept it.
        """
        prompt = await message.reply_text(
            f"{error} Reply with hours before the event (minimum: {MIN_LEAD_HOURS}).",
            reply_markup=ForceReply(selective=True),
        )
        self._pending_lead_input[from_user_id] = prompt.id

    # ------------------------------------------------------------------
    # Free-text lead-time reply handler
    # ------------------------------------------------------------------

    async def _on_lead_time_reply(self, _client: Client, message: Message) -> None:
        """Handle a free-text reply to the lead-time ForceReply prompt."""
        from_user_id = message.from_user.id if message.from_user else None
        handle = self._lookup_handle(from_user_id)
        if handle is None:
            # Not registered — silently ignore (may be a reply to something else)
            return

        expected_id = self._pending_lead_input.get(from_user_id)
        if expected_id is None:
            return

        reply_to_id = message.reply_to_message_id
        if reply_to_id != expected_id:
            # Reply to something else — ignore silently
            return

        raw = (message.text or "").strip()
        try:
            hours = int(raw)
        except (ValueError, TypeError):
            await self._reprompt_lead(message, from_user_id, "Not a whole number.")
            return

        if hours < MIN_LEAD_HOURS:
            await self._reprompt_lead(
                message,
                from_user_id,
                f"Must be at least {MIN_LEAD_HOURS} hours (cancellation cutoff is 26h before event).",
            )
            return

        if hours > MAX_LEAD_HOURS:
            await self._reprompt_lead(
                message,
                from_user_id,
                f"Must be at most {MAX_LEAD_HOURS} hours (30 days).",
            )
            return

        tg_id = handle.user.telegram_user_id
        if tg_id is None:
            self.log.warning(
                "telegram_user_id not set for session '%s' (lead-time reply); backfill incomplete",
                handle.user.session_name,
            )
            self._pending_lead_input.pop(from_user_id, None)
            await message.reply_text("Internal error, try again.")
            return

        rows = self.repo.set_reminder_lead_hours(tg_id, hours)
        if rows == 0:
            self.log.warning(
                "set_reminder_lead_hours affected 0 rows for tg_id=%d", tg_id
            )
            self._pending_lead_input.pop(from_user_id, None)
            await message.reply_text("Save failed, try again.")
            return

        handle.user.reminder_lead_hours = hours
        self._pending_lead_input.pop(from_user_id, None)
        self.log.info(
            "Reminder lead time set to %d hours for session '%s' (tg_id=%d).",
            hours,
            handle.user.session_name,
            tg_id,
        )
        await message.reply_text(f"Reminder updated: {hours} hours before the event.")
        text, markup = self._build_main_screen(handle)
        await message.reply_text(text, reply_markup=markup)
