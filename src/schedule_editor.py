"""ScheduleEditor — inline-keyboard UI for managing a user's voting schedule.

Owns the /schedule command and all sch:* callback routes. Constructed with
a UserRepository and a reference to the shared _handles dict so it sees
newly-registered voters automatically.
"""
import logging
import sqlite3
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.schedule_dsl import parse_schedule_dsl, serialize_schedule_dsl
from src.user_repository import UserRepository
from src.voter_handle import VoterHandle

# Fixed allow-lists — no arbitrary user input ever reaches the DB.
_VALID_TYPES = {"Game", "Training"}

# Day display order for the picker row
_DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class ScheduleEditor:
    """Inline-keyboard UI for viewing, adding, and removing schedule entries.

    Registered in the manager bot via ``register_handlers(app)``.
    Auth is handled via the shared ``_handles`` dict — the same object
    maintained by ``AutoPollManagerBot``.
    """

    def __init__(self, repo: UserRepository, handles: Dict[int, VoterHandle]) -> None:
        self.repo = repo
        self._handles = handles
        self.log = logging.getLogger("forum-poll-voter.manager.schedule")

    def register_handlers(self, app: Client) -> None:
        """Register the /schedule command and all sch:* callback handlers."""
        app.add_handler(
            MessageHandler(
                self._on_schedule_cmd,
                filters.command("schedule") & filters.private,
            )
        )
        app.add_handler(
            CallbackQueryHandler(
                self._on_callback,
                filters.regex(r"^sch:") & filters.private,
            )
        )

    # ------------------------------------------------------------------
    # Auth helper
    # ------------------------------------------------------------------

    def _lookup_handle(self, from_user_id: Optional[int]) -> Optional[VoterHandle]:
        """Return VoterHandle for the given user ID, or None if not registered."""
        return self._handles.get(from_user_id)

    # ------------------------------------------------------------------
    # /schedule command
    # ------------------------------------------------------------------

    async def _on_schedule_cmd(self, client: Client, message: Message) -> None:
        """Handle /schedule command: show the main schedule screen."""
        handle = self._lookup_handle(message.from_user.id if message.from_user else None)
        if handle is None:
            await message.reply_text("You're not registered. Contact the administrator.")
            return
        text, markup = self._build_main_screen(handle)
        await message.reply_text(text, reply_markup=markup)

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _build_main_screen(self, handle: VoterHandle):
        """Return (text, markup) for the main schedule screen.

        On parse error: text is the error message, markup has only Close.
        On empty schedule: text says "(empty)", markup has Add + Close.
        Otherwise: text lists entries, markup has Add + Remove + Close.
        """
        try:
            events = parse_schedule_dsl(handle.user.event_schedule)
        except Exception:
            self.log.exception(
                "Failed to parse event_schedule for session '%s'",
                handle.user.session_name,
            )
            text = "Error reading your schedule. Contact admin."
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("✖ Close", callback_data="sch:close")],
            ])
            return text, markup

        if not events:
            text = "(empty — tap Add to create one)"
            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("➕ Add", callback_data="sch:add"),
                    InlineKeyboardButton("✖ Close", callback_data="sch:close"),
                ],
            ])
            return text, markup

        lines = []
        for i, e in enumerate(events, start=1):
            entry = f"{e['type']} {e['day']}"
            if e.get("start_time"):
                entry += f" {e['start_time']}"
            lines.append(f"{i}. {entry}")
        text = "Your schedule:\n" + "\n".join(lines)
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ Add", callback_data="sch:add"),
                InlineKeyboardButton("❌ Remove", callback_data="sch:rm"),
                InlineKeyboardButton("✖ Close", callback_data="sch:close"),
            ],
        ])
        return text, markup

    # ------------------------------------------------------------------
    # Callback dispatcher
    # ------------------------------------------------------------------

    async def _on_callback(self, client: Client, query: CallbackQuery) -> None:
        """Dispatch all sch:* callback_data to the appropriate handler."""
        try:
            handle = self._lookup_handle(query.from_user.id if query.from_user else None)
            if handle is None:
                await query.answer("You're not registered.", show_alert=True)
                return

            data = query.data or ""
            tokens = data.split(":")

            # Dispatch by token shape
            # sch:main
            if tokens == ["sch", "main"]:
                text, markup = self._build_main_screen(handle)
                await query.message.edit_text(text, reply_markup=markup)
                await query.answer()
                return

            # sch:close
            if tokens == ["sch", "close"]:
                await query.message.edit_reply_markup(None)
                await query.answer()
                return

            # sch:add — show type picker
            if tokens == ["sch", "add"]:
                await self._render_add_type(query)
                return

            # sch:add:t:<Type> — type chosen; show day picker
            if len(tokens) == 4 and tokens[:3] == ["sch", "add", "t"]:
                event_type = tokens[3]
                if event_type not in _VALID_TYPES:
                    await query.answer("Internal error, try again.", show_alert=True)
                    return
                await self._render_add_day(query, event_type)
                return

            # sch:add:d:<Type>:<day> — day chosen; append and save
            if len(tokens) == 5 and tokens[:3] == ["sch", "add", "d"]:
                event_type = tokens[3]
                day = tokens[4]
                if event_type not in _VALID_TYPES or day not in _DAY_ORDER:
                    await query.answer("Internal error, try again.", show_alert=True)
                    return
                await self._handle_add_day(query, handle, event_type, day)
                return

            # sch:rm — show remove list
            if tokens == ["sch", "rm"]:
                await self._render_remove_list(query, handle)
                return

            # sch:rm:<index> — delete entry at index
            if len(tokens) == 3 and tokens[:2] == ["sch", "rm"]:
                try:
                    index = int(tokens[2])
                except ValueError:
                    await query.answer("Internal error, try again.", show_alert=True)
                    return
                await self._handle_remove_index(query, handle, index)
                return

            # Unrecognized shape → error
            await query.answer("Internal error, try again.", show_alert=True)

        except Exception:
            self.log.exception("Unexpected error in schedule callback handler")
            try:
                await query.answer("Internal error, try again.", show_alert=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Add flow helpers
    # ------------------------------------------------------------------

    async def _render_add_type(self, query: CallbackQuery) -> None:
        """Show the type picker screen: Game | Training | Back."""
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🏐 Game", callback_data="sch:add:t:Game"),
                InlineKeyboardButton("🏃 Training", callback_data="sch:add:t:Training"),
            ],
            [InlineKeyboardButton("⬅ Back", callback_data="sch:main")],
        ])
        await query.message.edit_text("Choose event type:", reply_markup=markup)
        await query.answer()

    async def _render_add_day(self, query: CallbackQuery, event_type: str) -> None:
        """Show the day picker screen: 7 weekday buttons + Back."""
        day_buttons = [
            InlineKeyboardButton(
                day.capitalize(),
                callback_data=f"sch:add:d:{event_type}:{day}",
            )
            for day in _DAY_ORDER
        ]
        # Lay out 4 buttons in first row, 3 in second
        markup = InlineKeyboardMarkup([
            day_buttons[:4],
            day_buttons[4:],
            [InlineKeyboardButton("⬅ Back", callback_data="sch:add")],
        ])
        await query.message.edit_text(
            f"Choose day for {event_type}:", reply_markup=markup
        )
        await query.answer()

    async def _handle_add_day(
        self,
        query: CallbackQuery,
        handle: VoterHandle,
        event_type: str,
        day: str,
    ) -> None:
        """Append new entry, save, and re-render main (or alert on failure)."""
        try:
            events = parse_schedule_dsl(handle.user.event_schedule)
        except Exception:
            self.log.exception(
                "Could not parse event_schedule for session '%s' during add",
                handle.user.session_name,
            )
            await query.answer("Save failed, try again.", show_alert=True)
            return

        events.append({"type": event_type, "day": day})
        saved = self._save(handle, events)
        if saved:
            text, markup = self._build_main_screen(handle)
            await query.message.edit_text(text, reply_markup=markup)
            await query.answer()
        else:
            await query.answer("Save failed, try again.", show_alert=True)

    # ------------------------------------------------------------------
    # Remove flow helpers
    # ------------------------------------------------------------------

    async def _render_remove_list(
        self, query: CallbackQuery, handle: VoterHandle, answer: bool = True
    ) -> None:
        """Show the remove screen: one button per event + Back.

        Calls ``query.answer()`` on the success path unless *answer* is False
        (used when the caller has already issued an alert).
        On parse error, always calls ``query.answer`` with an alert.
        """
        try:
            events = parse_schedule_dsl(handle.user.event_schedule)
        except Exception:
            self.log.exception(
                "Could not parse event_schedule for session '%s' during remove render",
                handle.user.session_name,
            )
            await query.answer("Internal error, try again.", show_alert=True)
            return

        rows = []
        if not events:
            text = "Nothing left to remove."
        else:
            text = "Tap an entry to remove it:"
            for i, e in enumerate(events):
                label = f"{i + 1}. {e['type']} {e['day']}"
                if e.get("start_time"):
                    label += f" {e['start_time']}"
                rows.append([InlineKeyboardButton(label, callback_data=f"sch:rm:{i}")])

        rows.append([InlineKeyboardButton("⬅ Back", callback_data="sch:main")])
        markup = InlineKeyboardMarkup(rows)
        await query.message.edit_text(text, reply_markup=markup)
        if answer:
            await query.answer()

    async def _handle_remove_index(
        self,
        query: CallbackQuery,
        handle: VoterHandle,
        index: int,
    ) -> None:
        """Delete the entry at the given index, save, and re-render the remove list."""
        try:
            events = parse_schedule_dsl(handle.user.event_schedule)
        except Exception:
            self.log.exception(
                "Could not parse event_schedule for session '%s' during remove",
                handle.user.session_name,
            )
            await query.answer("Internal error, try again.", show_alert=True)
            return

        if not (0 <= index < len(events)):
            await query.answer("Entry no longer exists.", show_alert=True)
            await self._render_remove_list(query, handle, answer=False)
            return

        events.pop(index)
        saved = self._save(handle, events)
        if not saved:
            await query.answer("Save failed, try again.", show_alert=True)
            await self._render_remove_list(query, handle, answer=False)
        else:
            await self._render_remove_list(query, handle)

    # ------------------------------------------------------------------
    # Persistence helper
    # ------------------------------------------------------------------

    def _save(self, handle: VoterHandle, events: list) -> bool:
        """Serialize events and persist to DB; mutate handle.user.event_schedule.

        Returns True on success, False on any failure (logs the reason).
        Does NOT mutate handle.user.event_schedule on failure.
        """
        tg_id = handle.user.telegram_user_id
        if tg_id is None:
            self.log.warning(
                "telegram_user_id not set for session '%s' (save); backfill incomplete",
                handle.user.session_name,
            )
            return False
        new_dsl = serialize_schedule_dsl(events)
        try:
            rows = self.repo.set_event_schedule(tg_id, new_dsl)
        except sqlite3.Error:
            self.log.exception("DB write failed on schedule save")
            return False
        if rows == 0:
            self.log.warning(
                "set_event_schedule affected 0 rows for tg_id=%d", tg_id
            )
            return False
        handle.user.event_schedule = new_dsl
        return True
