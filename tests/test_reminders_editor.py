"""Tests for RemindersEditor — /reminders command UI.

All Pyrogram types are mocked — no disk or network access.
"""
import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.auto_poll_manager_bot import VoterHandle
from src.reminders_editor import RemindersEditor
from src.user_repository import UserRecord


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_user_record(
    tg_id: Optional[int] = 111,
    enabled: bool = True,
    db_id: int = 1,
    event_schedule: str = "Game wed",
    reminders_enabled: bool = True,
    reminder_lead_hours: int = 27,
) -> UserRecord:
    return UserRecord(
        id=db_id,
        session_name="alice",
        session_string="SESS",
        event_schedule=event_schedule,
        vote_delay_seconds=5,
        telegram_user_id=tg_id,
        enabled=enabled,
        reminders_enabled=reminders_enabled,
        reminder_lead_hours=reminder_lead_hours,
    )


def _make_voter_handle(user: Optional[UserRecord] = None) -> VoterHandle:
    if user is None:
        user = _make_user_record()
    client = MagicMock()
    return VoterHandle(user=user, client=client)


def _make_repo():
    repo = MagicMock()
    repo.set_reminders_enabled = MagicMock(return_value=1)
    repo.set_reminder_lead_hours = MagicMock(return_value=1)
    return repo


def _make_editor(handles=None, repo=None) -> RemindersEditor:
    if handles is None:
        handles = {}
    if repo is None:
        repo = _make_repo()
    return RemindersEditor(repo=repo, handles=handles)


def _make_message(from_user_id: Optional[int] = 111) -> MagicMock:
    msg = MagicMock(spec=["from_user", "reply_text", "reply_to_message_id", "reply_to_message", "text"])
    if from_user_id is None:
        msg.from_user = None
    else:
        msg.from_user = MagicMock()
        msg.from_user.id = from_user_id
    msg.reply_text = AsyncMock()
    msg.reply_to_message_id = None
    msg.reply_to_message = None
    msg.text = ""
    return msg


def _make_query(from_user_id: Optional[int] = 111, data: str = "rem:main") -> MagicMock:
    query = MagicMock(spec=["from_user", "data", "answer", "message"])
    if from_user_id is None:
        query.from_user = None
    else:
        query.from_user = MagicMock()
        query.from_user.id = from_user_id
    query.data = data
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()
    query.message.edit_reply_markup = AsyncMock()
    # reply_text on the query.message returns a mock with an id (simulates prompt message)
    prompt_msg = MagicMock()
    prompt_msg.id = 999
    query.message.reply_text = AsyncMock(return_value=prompt_msg)
    return query


def _collect_button_texts(markup) -> list:
    texts = []
    if markup and hasattr(markup, "inline_keyboard"):
        for row in markup.inline_keyboard:
            for btn in row:
                texts.append(btn.text)
    return texts



# ---------------------------------------------------------------------------
# Tests: register_handlers
# ---------------------------------------------------------------------------

class TestRegisterHandlers:
    def test_register_handlers_adds_three_handlers(self):
        """register_handlers adds message, callback, and reply handlers."""
        from pyrogram.handlers import CallbackQueryHandler, MessageHandler
        editor = _make_editor()
        app = MagicMock()
        editor.register_handlers(app)
        assert app.add_handler.call_count == 3
        calls = app.add_handler.call_args_list
        handler_types = [type(c.args[0]) for c in calls]
        # Two MessageHandlers (command + reply) and one CallbackQueryHandler
        assert handler_types.count(MessageHandler) == 2
        assert CallbackQueryHandler in handler_types


# ---------------------------------------------------------------------------
# Tests: main screen rendering
# ---------------------------------------------------------------------------

class TestMainScreen:
    def test_main_screen_shows_on_status(self):
        """Main screen for enabled reminders shows 'ON' and 'Disable' button."""
        user = _make_user_record(tg_id=111, reminders_enabled=True, reminder_lead_hours=27)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        msg = _make_message(from_user_id=111)

        asyncio.run(editor._on_reminders_cmd(None, msg))

        msg.reply_text.assert_awaited_once()
        call_args = msg.reply_text.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        markup = call_args.kwargs.get("reply_markup") or (call_args.args[1] if len(call_args.args) > 1 else None)

        assert "ON" in text
        assert "27" in text

        button_texts = _collect_button_texts(markup)
        assert any("Disable" in t for t in button_texts), f"No Disable button: {button_texts}"
        assert any("Change timing" in t for t in button_texts), f"No Change timing button: {button_texts}"
        assert any("Close" in t for t in button_texts), f"No Close button: {button_texts}"

    def test_main_screen_shows_off_status(self):
        """Main screen for disabled reminders shows 'OFF' and 'Enable' button."""
        # reminder_lead_hours=10 is intentionally below the 27h UI minimum to verify the
        # screen renders whatever value is stored in the DB (the UI minimum is enforced only
        # on input, not on display).
        user = _make_user_record(tg_id=111, reminders_enabled=False, reminder_lead_hours=10)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        msg = _make_message(from_user_id=111)

        asyncio.run(editor._on_reminders_cmd(None, msg))

        msg.reply_text.assert_awaited_once()
        call_args = msg.reply_text.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        markup = call_args.kwargs.get("reply_markup") or (call_args.args[1] if len(call_args.args) > 1 else None)

        assert "OFF" in text
        assert "10" in text

        button_texts = _collect_button_texts(markup)
        assert any("Enable" in t for t in button_texts), f"No Enable button: {button_texts}"
        assert not any(t == "Disable" for t in button_texts), f"Unexpected Disable button: {button_texts}"

    def test_main_screen_shows_reminder_timing(self):
        """Main screen text shows the configured hours-before-event for the reminder."""
        user = _make_user_record(tg_id=111, reminders_enabled=True, reminder_lead_hours=36)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        msg = _make_message(from_user_id=111)

        asyncio.run(editor._on_reminders_cmd(None, msg))

        call_args = msg.reply_text.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "36" in text
        assert "hours before the event" in text

    def test_command_unregistered_rejected(self):
        """Unknown sender gets 'not registered' reply, no keyboard."""
        handles = {}
        editor = _make_editor(handles=handles)
        msg = _make_message(from_user_id=999)

        asyncio.run(editor._on_reminders_cmd(None, msg))

        msg.reply_text.assert_awaited_once()
        text = msg.reply_text.call_args.args[0]
        assert "not registered" in text.lower() or "administrator" in text.lower()


# ---------------------------------------------------------------------------
# Tests: callback auth
# ---------------------------------------------------------------------------

class TestCallbackAuth:
    def test_callback_unregistered_rejected(self):
        """Unknown from_user.id → query.answer alert, no edit."""
        handles = {}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=999, data="rem:main")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is True
        query.message.edit_text.assert_not_called()
        query.message.edit_reply_markup.assert_not_called()

    def test_callback_no_from_user_rejected(self):
        """None from_user → query.answer alert."""
        handles = {}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=None, data="rem:main")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is True


# ---------------------------------------------------------------------------
# Tests: rem:main callback
# ---------------------------------------------------------------------------

class TestMainCallback:
    def test_main_callback_redraws_via_edit_text(self):
        """rem:main callback re-renders via edit_text."""
        user = _make_user_record(tg_id=111, reminders_enabled=True, reminder_lead_hours=27)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="rem:main")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_text.assert_awaited_once()
        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is False

    def test_main_callback_shows_correct_status(self):
        """rem:main shows current ON/OFF status in the edited text."""
        user = _make_user_record(tg_id=111, reminders_enabled=False, reminder_lead_hours=12)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="rem:main")

        asyncio.run(editor._on_callback(None, query))

        edit_call = query.message.edit_text.call_args
        text = edit_call.args[0] if edit_call.args else edit_call.kwargs.get("text", "")
        assert "OFF" in text
        assert "12" in text


# ---------------------------------------------------------------------------
# Tests: rem:close callback
# ---------------------------------------------------------------------------

class TestCloseCallback:
    def test_close_callback_removes_keyboard(self):
        """rem:close → edit_reply_markup(None) called."""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="rem:close")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_reply_markup.assert_awaited_once_with(None)
        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is False

    def test_close_callback_does_not_edit_text(self):
        """rem:close does not change message text."""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="rem:close")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_text.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: rem:toggle callback
# ---------------------------------------------------------------------------

class TestToggleCallback:
    def test_toggle_flips_on_to_off_and_persists(self):
        """rem:toggle when ON → calls repo.set_reminders_enabled(False), mutates user, re-renders."""
        user = _make_user_record(tg_id=111, reminders_enabled=True)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="rem:toggle")

        asyncio.run(editor._on_callback(None, query))

        repo.set_reminders_enabled.assert_called_once_with(111, False)
        assert handle.user.reminders_enabled is False
        query.message.edit_text.assert_awaited_once()

        # Check the re-rendered screen says OFF
        edit_call = query.message.edit_text.call_args
        text = edit_call.args[0] if edit_call.args else edit_call.kwargs.get("text", "")
        assert "OFF" in text

    def test_toggle_flips_off_to_on_and_persists(self):
        """rem:toggle when OFF → calls repo.set_reminders_enabled(True), mutates user, re-renders."""
        user = _make_user_record(tg_id=111, reminders_enabled=False)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="rem:toggle")

        asyncio.run(editor._on_callback(None, query))

        repo.set_reminders_enabled.assert_called_once_with(111, True)
        assert handle.user.reminders_enabled is True
        query.message.edit_text.assert_awaited_once()

        edit_call = query.message.edit_text.call_args
        text = edit_call.args[0] if edit_call.args else edit_call.kwargs.get("text", "")
        assert "ON" in text

    def test_toggle_zero_rows_alerts_no_mutation(self):
        """If repo returns 0 rows, alert is shown and in-memory not mutated."""
        user = _make_user_record(tg_id=111, reminders_enabled=True)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        repo.set_reminders_enabled.return_value = 0
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="rem:toggle")

        asyncio.run(editor._on_callback(None, query))

        # Still enabled — no mutation
        assert handle.user.reminders_enabled is True
        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is True
        query.message.edit_text.assert_not_called()

    def test_toggle_no_tg_id_alerts(self):
        """rem:toggle with telegram_user_id=None → alert, no repo call."""
        user = _make_user_record(tg_id=None, reminders_enabled=True)
        handle = _make_voter_handle(user)
        # Handle keyed by from_user.id (111) but user.telegram_user_id is None
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="rem:toggle")

        asyncio.run(editor._on_callback(None, query))

        repo.set_reminders_enabled.assert_not_called()
        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is True


# ---------------------------------------------------------------------------
# Tests: rem:lead callback (ForceReply prompt)
# ---------------------------------------------------------------------------

class TestLeadCallback:
    def test_lead_sends_force_reply_and_stashes_id(self):
        """rem:lead → sends ForceReply prompt, stashes its message id."""
        from pyrogram.types import ForceReply
        user = _make_user_record(tg_id=111, reminder_lead_hours=27)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="rem:lead")
        # The prompt message returned by reply_text has id=999 (set in _make_query)

        asyncio.run(editor._on_callback(None, query))

        query.message.reply_text.assert_awaited_once()
        call_args = query.message.reply_text.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "27" in text  # current hours shown
        assert "Reply with" in text or "reply" in text.lower()
        assert "minimum" in text.lower()  # minimum lead hours shown

        markup = call_args.kwargs.get("reply_markup") or (
            call_args.args[1] if len(call_args.args) > 1 else None
        )
        assert isinstance(markup, ForceReply), f"Expected ForceReply, got {type(markup)}"

        # Pending entry stashed
        assert editor._pending_lead_input.get(111) == 999

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is False

    def test_lead_overwrites_previous_pending_entry(self):
        """Calling rem:lead again overwrites the previously stashed message id."""
        user = _make_user_record(tg_id=111, reminder_lead_hours=27)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)

        # First call — prompt id 999
        query1 = _make_query(from_user_id=111, data="rem:lead")
        asyncio.run(editor._on_callback(None, query1))
        assert editor._pending_lead_input.get(111) == 999

        # Second call — different prompt id
        query2 = _make_query(from_user_id=111, data="rem:lead")
        prompt2 = MagicMock()
        prompt2.id = 1234
        query2.message.reply_text = AsyncMock(return_value=prompt2)
        asyncio.run(editor._on_callback(None, query2))

        assert editor._pending_lead_input.get(111) == 1234


# ---------------------------------------------------------------------------
# Tests: free-text lead-time reply handler
# ---------------------------------------------------------------------------

class TestLeadTimeReply:
    def _setup(self, tg_id=111, reminders_enabled=True, reminder_lead_hours=27, repo=None):
        user = _make_user_record(tg_id=tg_id, reminders_enabled=reminders_enabled, reminder_lead_hours=reminder_lead_hours)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        if repo is None:
            repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        # Pre-stash a pending entry with message id 500
        editor._pending_lead_input[111] = 500
        return editor, handle, repo

    def _make_reply_message(self, from_user_id=111, text="36", reply_to_id=500, reprompt_id=700):
        msg = MagicMock()
        msg.from_user = MagicMock()
        msg.from_user.id = from_user_id
        msg.text = text
        msg.reply_to_message_id = reply_to_id
        # reply_text returns a mock with .id so _reprompt_lead can rotate pending
        reprompt_msg = MagicMock()
        reprompt_msg.id = reprompt_id
        msg.reply_text = AsyncMock(return_value=reprompt_msg)
        return msg

    def test_valid_reply_persists_and_mutates(self):
        """Valid integer reply → persists, mutates handle, clears pending, re-renders screen."""
        editor, handle, repo = self._setup()
        msg = self._make_reply_message(text="36", reply_to_id=500)

        asyncio.run(editor._on_lead_time_reply(None, msg))

        repo.set_reminder_lead_hours.assert_called_once_with(111, 36)
        assert handle.user.reminder_lead_hours == 36
        assert 111 not in editor._pending_lead_input

        # Two reply_text calls: confirmation + main screen
        assert msg.reply_text.call_count == 2
        first_call_text = msg.reply_text.call_args_list[0].args[0]
        assert "36" in first_call_text and "updated" in first_call_text.lower()

        # Second call re-renders main screen (has markup)
        second_call_kwargs = msg.reply_text.call_args_list[1]
        markup = second_call_kwargs.kwargs.get("reply_markup") or (
            second_call_kwargs.args[1] if len(second_call_kwargs.args) > 1 else None
        )
        assert markup is not None

    def test_garbage_text_alerts_keeps_pending(self):
        """Non-integer reply → ForceReply re-prompt sent, pending id rotated to new prompt."""
        from pyrogram.types import ForceReply
        editor, handle, _ = self._setup()
        msg = self._make_reply_message(text="abc", reply_to_id=500, reprompt_id=700)

        asyncio.run(editor._on_lead_time_reply(None, msg))

        msg.reply_text.assert_awaited_once()
        call_args = msg.reply_text.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "not a whole number" in text.lower() or "whole number" in text.lower()
        # Must use ForceReply markup
        markup = call_args.kwargs.get("reply_markup") or (
            call_args.args[1] if len(call_args.args) > 1 else None
        )
        assert isinstance(markup, ForceReply), f"Expected ForceReply, got {type(markup)}"
        # Pending id rotated to the new prompt's id (700), NOT the original 500
        assert editor._pending_lead_input.get(111) == 700

    def test_below_minimum_rejected_keeps_pending(self):
        """26 hours (below 27h minimum) → ForceReply re-prompt sent, pending id rotated, DB not touched."""
        from pyrogram.types import ForceReply
        repo = _make_repo()
        editor, handle, repo = self._setup(repo=repo)
        msg = self._make_reply_message(text="26", reply_to_id=500, reprompt_id=700)

        asyncio.run(editor._on_lead_time_reply(None, msg))

        msg.reply_text.assert_awaited_once()
        call_args = msg.reply_text.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "27" in text or "minimum" in text.lower() or "cancellation" in text.lower()
        # Must use ForceReply markup
        markup = call_args.kwargs.get("reply_markup") or (
            call_args.args[1] if len(call_args.args) > 1 else None
        )
        assert isinstance(markup, ForceReply), f"Expected ForceReply, got {type(markup)}"
        # Pending id rotated to new prompt (700), NOT the original 500
        assert editor._pending_lead_input.get(111) == 700
        # DB not touched
        repo.set_reminder_lead_hours.assert_not_called()

    def test_above_maximum_rejected_keeps_pending(self):
        """721 hours (above 720h maximum) → ForceReply re-prompt sent, pending id rotated, DB not touched."""
        from pyrogram.types import ForceReply
        repo = _make_repo()
        editor, handle, repo = self._setup(repo=repo)
        msg = self._make_reply_message(text="721", reply_to_id=500, reprompt_id=700)

        asyncio.run(editor._on_lead_time_reply(None, msg))

        msg.reply_text.assert_awaited_once()
        call_args = msg.reply_text.call_args
        text = call_args.args[0] if call_args.args else call_args.kwargs.get("text", "")
        assert "720" in text or "most" in text.lower() or "30 days" in text.lower()
        # Must use ForceReply markup
        markup = call_args.kwargs.get("reply_markup") or (
            call_args.args[1] if len(call_args.args) > 1 else None
        )
        assert isinstance(markup, ForceReply), f"Expected ForceReply, got {type(markup)}"
        # Pending id rotated to new prompt (700)
        assert editor._pending_lead_input.get(111) == 700
        # DB not touched
        repo.set_reminder_lead_hours.assert_not_called()

    def test_at_maximum_is_accepted(self):
        """Exactly 720 hours (maximum) → persists, mutates handle, clears pending."""
        editor, handle, repo = self._setup()
        msg = self._make_reply_message(text="720", reply_to_id=500)

        asyncio.run(editor._on_lead_time_reply(None, msg))

        repo.set_reminder_lead_hours.assert_called_once_with(111, 720)
        assert handle.user.reminder_lead_hours == 720
        assert 111 not in editor._pending_lead_input

    def test_reprompt_lead_failure_leaves_pending_at_original_id(self):
        """If reply_text raises inside _reprompt_lead, pending keeps the original prompt id.

        The user can still reply to the original ForceReply message and the gate accepts it.
        """
        editor, handle, _ = self._setup()
        msg = self._make_reply_message(text="abc", reply_to_id=500, reprompt_id=700)
        # Make reply_text raise so _reprompt_lead's assignment never executes
        msg.reply_text = AsyncMock(side_effect=RuntimeError("Telegram send error"))

        with pytest.raises(RuntimeError, match="Telegram send error"):
            asyncio.run(editor._on_lead_time_reply(None, msg))

        # Pending must still point at the original id (500), not rotated to 700
        assert editor._pending_lead_input.get(111) == 500

    def test_reply_not_to_our_prompt_is_ignored(self):
        """Reply to a different message id → silently ignored."""
        editor, handle, repo = self._setup()
        msg = self._make_reply_message(text="36", reply_to_id=9999)  # not our prompt

        asyncio.run(editor._on_lead_time_reply(None, msg))

        repo.set_reminder_lead_hours.assert_not_called()
        msg.reply_text.assert_not_called()
        # Pending still stashed
        assert editor._pending_lead_input.get(111) == 500

    def test_no_pending_entry_is_ignored(self):
        """User replies with text but has no pending lead-time input → ignored."""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        # No pending entry set

        msg = self._make_reply_message(text="36", reply_to_id=500)
        asyncio.run(editor._on_lead_time_reply(None, msg))

        repo.set_reminder_lead_hours.assert_not_called()
        msg.reply_text.assert_not_called()

    def test_unregistered_user_reply_silently_ignored(self):
        """Reply from unregistered user → silently ignored."""
        handles = {}  # user 111 not registered
        editor = _make_editor(handles=handles)
        msg = self._make_reply_message(from_user_id=999, text="36", reply_to_id=500)

        asyncio.run(editor._on_lead_time_reply(None, msg))

        msg.reply_text.assert_not_called()

    def test_from_user_none_silently_ignored(self):
        """Message with from_user=None → silently ignored (no AttributeError)."""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        editor._pending_lead_input[111] = 500

        msg = _make_message(from_user_id=None)
        msg.text = "36"
        msg.reply_to_message_id = 500

        asyncio.run(editor._on_lead_time_reply(None, msg))

        repo.set_reminder_lead_hours.assert_not_called()
        msg.reply_text.assert_not_called()

    def test_db_returns_zero_rows_alerts(self):
        """If set_reminder_lead_hours returns 0 → error message, no mutation, pending cleared."""
        repo = _make_repo()
        repo.set_reminder_lead_hours.return_value = 0
        editor, handle, _ = self._setup(repo=repo)
        original_hours = handle.user.reminder_lead_hours
        msg = self._make_reply_message(text="36", reply_to_id=500)

        asyncio.run(editor._on_lead_time_reply(None, msg))

        assert handle.user.reminder_lead_hours == original_hours
        msg.reply_text.assert_awaited_once()
        text = msg.reply_text.call_args.args[0]
        assert "save failed" in text.lower() or "try again" in text.lower()
        # Pending cleared — mirrors the tg_id-is-None path so the user is not stuck
        assert editor._pending_lead_input.get(111) is None

    def test_no_tg_id_alerts_on_valid_input(self):
        """Valid input but telegram_user_id=None → error, no repo call, pending cleared."""
        user = _make_user_record(tg_id=None, reminder_lead_hours=27)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        editor._pending_lead_input[111] = 500

        msg = self._make_reply_message(from_user_id=111, text="36", reply_to_id=500)
        asyncio.run(editor._on_lead_time_reply(None, msg))

        repo.set_reminder_lead_hours.assert_not_called()
        msg.reply_text.assert_awaited_once()
        text = msg.reply_text.call_args.args[0]
        assert "internal error" in text.lower()
        # Pending cleared so user is not stuck in an error loop
        assert 111 not in editor._pending_lead_input

    def test_lead_time_at_minimum_is_accepted(self):
        """Exactly 27 hours (minimum) → persists, mutates handle, clears pending, re-renders screen."""
        from pyrogram.types import InlineKeyboardMarkup
        editor, handle, repo = self._setup()
        msg = self._make_reply_message(text="27", reply_to_id=500)

        asyncio.run(editor._on_lead_time_reply(None, msg))

        repo.set_reminder_lead_hours.assert_called_once_with(111, 27)
        assert handle.user.reminder_lead_hours == 27
        assert 111 not in editor._pending_lead_input
        # Two reply_text calls: confirmation + main screen
        assert msg.reply_text.call_count == 2
        first_text = msg.reply_text.call_args_list[0].args[0]
        assert "27" in first_text and "updated" in first_text.lower()
        # Second call re-renders main screen with inline keyboard
        second_call = msg.reply_text.call_args_list[1]
        markup = second_call.kwargs.get("reply_markup") or (
            second_call.args[1] if len(second_call.args) > 1 else None
        )
        assert isinstance(markup, InlineKeyboardMarkup), (
            f"Expected InlineKeyboardMarkup on main screen re-render, got {type(markup)}"
        )

    def test_invalid_then_valid_reply_is_accepted(self):
        """Regression: invalid reply rotates pending id; next reply to rotated id is accepted."""
        from pyrogram.types import ForceReply
        editor, handle, repo = self._setup()

        # First reply: invalid (below minimum) → reprompt, pending rotates to 700
        msg_invalid = self._make_reply_message(text="10", reply_to_id=500, reprompt_id=700)
        asyncio.run(editor._on_lead_time_reply(None, msg_invalid))

        # Pending should have rotated to the new prompt id
        assert editor._pending_lead_input.get(111) == 700
        # A ForceReply message was sent
        msg_invalid.reply_text.assert_awaited_once()
        reprompt_markup = msg_invalid.reply_text.call_args.kwargs.get("reply_markup") or (
            msg_invalid.reply_text.call_args.args[1] if len(msg_invalid.reply_text.call_args.args) > 1 else None
        )
        assert isinstance(reprompt_markup, ForceReply)

        # Second reply: valid (30h), reply_to_message_id matches the rotated id (700)
        msg_valid = self._make_reply_message(text="30", reply_to_id=700)
        asyncio.run(editor._on_lead_time_reply(None, msg_valid))

        repo.set_reminder_lead_hours.assert_called_once_with(111, 30)
        assert handle.user.reminder_lead_hours == 30
        assert 111 not in editor._pending_lead_input
        # Two reply_text calls on msg_valid: confirmation + main screen
        assert msg_valid.reply_text.call_count == 2


# ---------------------------------------------------------------------------
# Tests: unrecognized callback data
# ---------------------------------------------------------------------------

class TestUnrecognizedCallback:
    def test_unknown_callback_data_rejected(self):
        """Unknown rem: callback → alert 'Internal error, try again.'"""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="rem:unknown:garbage")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "internal error" in text.lower()
        assert show_alert is True
        query.message.edit_text.assert_not_called()
