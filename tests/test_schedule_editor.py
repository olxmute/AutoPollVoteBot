"""Tests for ScheduleEditor (Task 4: main screen + /schedule command).

All Pyrogram types are mocked — no disk or network access.
"""
import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from src.auto_poll_manager_bot import VoterHandle
from src.schedule_editor import ScheduleEditor
from src.user_repository import UserRecord


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_user_record(
    tg_id: Optional[int] = 111,
    enabled: bool = True,
    db_id: int = 1,
    event_schedule: str = "Game wed",
) -> UserRecord:
    return UserRecord(
        id=db_id,
        session_name="alice",
        session_string="SESS",
        event_schedule=event_schedule,
        vote_delay_seconds=5,
        telegram_user_id=tg_id,
        enabled=enabled,
        reminders_enabled=True,
        reminder_lead_hours=27,
    )


def _make_voter_handle(user: Optional[UserRecord] = None) -> VoterHandle:
    if user is None:
        user = _make_user_record()
    client = MagicMock()
    return VoterHandle(user=user, client=client)


def _make_repo():
    repo = MagicMock()
    repo.set_event_schedule = MagicMock(return_value=1)
    return repo


def _make_editor(handles=None, repo=None) -> ScheduleEditor:
    if handles is None:
        handles = {}
    if repo is None:
        repo = _make_repo()
    return ScheduleEditor(repo=repo, handles=handles)


def _make_message(from_user_id: Optional[int] = 111) -> MagicMock:
    msg = MagicMock(spec=["from_user", "reply_text"])
    if from_user_id is None:
        msg.from_user = None
    else:
        msg.from_user = MagicMock()
        msg.from_user.id = from_user_id
    msg.reply_text = AsyncMock()
    return msg


def _make_query(from_user_id: Optional[int] = 111, data: str = "sch:main") -> MagicMock:
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
    return query


# ---------------------------------------------------------------------------
# Tests: register_handlers
# ---------------------------------------------------------------------------

class TestRegisterHandlers:
    def test_register_handlers_adds_message_and_callback_handlers(self):
        from pyrogram.handlers import CallbackQueryHandler, MessageHandler
        editor = _make_editor()
        app = MagicMock()
        editor.register_handlers(app)
        assert app.add_handler.call_count == 2
        calls = app.add_handler.call_args_list
        handler_types = [type(c.args[0]) for c in calls]
        assert MessageHandler in handler_types
        assert CallbackQueryHandler in handler_types


# ---------------------------------------------------------------------------
# Tests: /schedule command — _on_schedule_cmd
# ---------------------------------------------------------------------------

class TestScheduleCommand:
    def test_schedule_command_renders_main_with_events(self):
        """Registered user with non-empty DSL gets text with Add+Remove+Close."""
        user = _make_user_record(tg_id=111, event_schedule="Game wed")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        msg = _make_message(from_user_id=111)

        asyncio.run(editor._on_schedule_cmd(None, msg))

        msg.reply_text.assert_awaited_once()
        call_kwargs = msg.reply_text.call_args
        text = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("text", "")
        markup = call_kwargs.kwargs.get("reply_markup") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)

        assert "Game" in text
        assert "wed" in text

        # Verify inline keyboard has Add, Remove, Close buttons
        button_texts = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_texts.append(btn.text)
        assert any("Add" in t for t in button_texts), f"No Add button found: {button_texts}"
        assert any("Remove" in t for t in button_texts), f"No Remove button found: {button_texts}"
        assert any("Close" in t for t in button_texts), f"No Close button found: {button_texts}"

    def test_schedule_command_renders_main_empty(self):
        """Registered user with empty DSL gets Add+Close (no Remove)."""
        user = _make_user_record(tg_id=111, event_schedule="")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        msg = _make_message(from_user_id=111)

        asyncio.run(editor._on_schedule_cmd(None, msg))

        msg.reply_text.assert_awaited_once()
        call_kwargs = msg.reply_text.call_args
        text = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("text", "")
        markup = call_kwargs.kwargs.get("reply_markup") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)

        assert "empty" in text.lower() or "add" in text.lower()

        button_texts = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_texts.append(btn.text)
        assert any("Add" in t for t in button_texts), f"No Add button found: {button_texts}"
        assert any("Close" in t for t in button_texts), f"No Close button found: {button_texts}"
        # No Remove button when schedule is empty
        assert not any("Remove" in t for t in button_texts), f"Unexpected Remove button: {button_texts}"

    def test_schedule_command_renders_parse_error(self):
        """Malformed DSL → 'Error reading your schedule.' + Close only."""
        # A DSL that parses but then breaks at ScheduledEvent level isn't the same as
        # a DSL that parse_schedule_dsl raises on. Use a DSL where parse_schedule_dsl itself raises.
        # parse_schedule_dsl raises ValueError for entries with < 2 parts, e.g. ";" produces empty
        # but "x" (single token) raises ValueError. We use "x" to trigger the exception.
        user = _make_user_record(tg_id=111, event_schedule="x")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        msg = _make_message(from_user_id=111)

        asyncio.run(editor._on_schedule_cmd(None, msg))

        msg.reply_text.assert_awaited_once()
        call_kwargs = msg.reply_text.call_args
        text = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("text", "")
        markup = call_kwargs.kwargs.get("reply_markup") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)

        assert "error" in text.lower() or "contact admin" in text.lower()

        button_texts = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_texts.append(btn.text)
        assert any("Close" in t for t in button_texts), f"No Close button found: {button_texts}"
        assert not any("Add" in t for t in button_texts), f"Unexpected Add button in error state: {button_texts}"
        assert not any("Remove" in t for t in button_texts), f"Unexpected Remove button in error state: {button_texts}"

    def test_schedule_command_unregistered_rejected(self):
        """Unknown sender gets 'not registered' reply, no keyboard."""
        handles = {}
        editor = _make_editor(handles=handles)
        msg = _make_message(from_user_id=999)

        asyncio.run(editor._on_schedule_cmd(None, msg))

        msg.reply_text.assert_awaited_once()
        text = msg.reply_text.call_args.args[0]
        assert "not registered" in text.lower() or "administrator" in text.lower()


# ---------------------------------------------------------------------------
# Tests: callback dispatcher — auth
# ---------------------------------------------------------------------------

class TestCallbackAuth:
    def test_callback_unregistered_rejected(self):
        """Unknown from_user.id on sch:main → query.answer alert, no edit."""
        handles = {}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=999, data="sch:main")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_kwargs = query.answer.call_args
        show_alert = answer_kwargs.kwargs.get("show_alert", False) or (
            len(answer_kwargs.args) > 1 and answer_kwargs.args[1]
        )
        assert show_alert is True
        query.message.edit_text.assert_not_called()
        query.message.edit_reply_markup.assert_not_called()

    def test_callback_no_from_user_rejected(self):
        """None from_user on sch:main → query.answer alert."""
        handles = {}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=None, data="sch:main")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_kwargs = query.answer.call_args
        show_alert = answer_kwargs.kwargs.get("show_alert", False)
        assert show_alert is True


# ---------------------------------------------------------------------------
# Tests: callback dispatcher — malformed data
# ---------------------------------------------------------------------------

class TestCallbackMalformedData:
    def test_callback_malformed_data_rejected(self):
        """Unknown callback data → alert 'Internal error, try again.'"""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:unknown:garbage")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "internal error" in text.lower()
        assert show_alert is True
        query.message.edit_text.assert_not_called()

    def test_callback_single_token_rejected(self):
        """'sch:' alone (tokens=['sch','']) → alert."""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is True


# ---------------------------------------------------------------------------
# Tests: sch:close
# ---------------------------------------------------------------------------

class TestCloseCallback:
    def test_close_callback_removes_keyboard(self):
        """sch:close → edit_reply_markup(None) called."""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:close")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_reply_markup.assert_awaited_once_with(None)
        query.answer.assert_awaited_once()
        # Should NOT alert (show_alert=False or not passed)
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is False

    def test_close_callback_does_not_edit_text(self):
        """sch:close only removes keyboard, doesn't change message text."""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:close")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_text.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: sch:main
# ---------------------------------------------------------------------------

class TestMainCallback:
    def test_main_callback_redraws_via_edit_text(self):
        """sch:main callback re-renders schedule via edit_text."""
        user = _make_user_record(tg_id=111, event_schedule="Game wed")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:main")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_text.assert_awaited_once()
        # Should not show an alert
        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is False

    def test_main_callback_redraws_with_correct_content(self):
        """sch:main redraws with the current schedule content."""
        user = _make_user_record(tg_id=111, event_schedule="Training tue")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:main")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_text.assert_awaited_once()
        edit_call = query.message.edit_text.call_args
        text = edit_call.args[0] if edit_call.args else edit_call.kwargs.get("text", "")
        assert "Training" in text
        assert "tue" in text

    def test_main_callback_empty_schedule_no_remove_button(self):
        """sch:main with empty DSL shows Add+Close, no Remove."""
        user = _make_user_record(tg_id=111, event_schedule="")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:main")

        asyncio.run(editor._on_callback(None, query))

        edit_call = query.message.edit_text.call_args
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        button_texts = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_texts.append(btn.text)
        assert any("Add" in t for t in button_texts)
        assert not any("Remove" in t for t in button_texts)


# ---------------------------------------------------------------------------
# Tests: Add flow (Task 5)
# ---------------------------------------------------------------------------

class TestAddFlow:
    def test_add_callback_shows_type_picker(self):
        """sch:add → edit_text called with Game and Training buttons."""
        user = _make_user_record(tg_id=111, event_schedule="")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:add")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_text.assert_awaited_once()
        edit_call = query.message.edit_text.call_args
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        button_texts = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_texts.append(btn.text)
        assert any("Game" in t for t in button_texts), f"No Game button: {button_texts}"
        assert any("Training" in t for t in button_texts), f"No Training button: {button_texts}"
        assert any("Back" in t for t in button_texts), f"No Back button: {button_texts}"

    def test_add_type_callback_shows_day_picker(self):
        """sch:add:t:Game → edit_text with 7 day buttons."""
        user = _make_user_record(tg_id=111, event_schedule="")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:add:t:Game")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_text.assert_awaited_once()
        edit_call = query.message.edit_text.call_args
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        button_texts = []
        button_datas = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_texts.append(btn.text)
                    button_datas.append(btn.callback_data)
        # 7 day buttons + 1 Back = 8 total buttons
        day_buttons = [d for d in button_datas if "sch:add:d:Game:" in d]
        assert len(day_buttons) == 7, f"Expected 7 day buttons, got: {day_buttons}"
        assert any("Back" in t for t in button_texts), f"No Back button: {button_texts}"

    def test_add_type_unknown_rejected(self):
        """sch:add:t:Unknown → alert 'Internal error, try again.'"""
        user = _make_user_record(tg_id=111)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:add:t:Unknown")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "internal error" in text.lower()
        assert show_alert is True
        query.message.edit_text.assert_not_called()

    def test_add_day_callback_saves_and_redraws_main(self):
        """sch:add:d:Game:wed → repo.set_event_schedule called, handle mutated, main re-rendered."""
        user = _make_user_record(tg_id=111, event_schedule="Training tue")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:add:d:Game:wed")

        asyncio.run(editor._on_callback(None, query))

        # Repo must be called with expected DSL
        repo.set_event_schedule.assert_called_once_with(111, "Training tue; Game wed")
        # In-memory state mutated
        assert handle.user.event_schedule == "Training tue; Game wed"
        # Main screen re-rendered via edit_text
        query.message.edit_text.assert_awaited_once()

    def test_add_day_callback_db_failure(self):
        """repo raises sqlite3.Error → query.answer alert with correct text, no mutation, no re-render."""
        import sqlite3 as sqlite3_mod
        user = _make_user_record(tg_id=111, event_schedule="Training tue")
        original_schedule = user.event_schedule
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        repo.set_event_schedule.side_effect = sqlite3_mod.Error("disk full")
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:add:d:Game:wed")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        alert_text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "Save failed, try again." in alert_text
        assert show_alert is True
        # In-memory NOT mutated
        assert handle.user.event_schedule == original_schedule
        # No re-render
        query.message.edit_text.assert_not_called()

    def test_save_without_telegram_user_id_returns_false(self):
        """_save with telegram_user_id=None → returns False, warns, DB not touched."""
        user = _make_user_record(tg_id=None, event_schedule="Game wed")
        handle = _make_voter_handle(user)
        repo = _make_repo()
        editor = _make_editor(repo=repo)

        result = editor._save(handle, [{"type": "Game", "day": "wed"}])

        assert result is False
        repo.set_event_schedule.assert_not_called()
        # In-memory not mutated (it was "Game wed" before)
        assert handle.user.event_schedule == "Game wed"

    def test_save_returns_false_when_rows_zero(self):
        """_save returns False (and does NOT mutate in-memory) when DB UPDATE affects 0 rows."""
        user = _make_user_record(tg_id=111, event_schedule="Game wed")
        original_schedule = user.event_schedule
        handle = _make_voter_handle(user)
        repo = _make_repo()
        repo.set_event_schedule.return_value = 0  # simulate missing row
        editor = _make_editor(repo=repo)

        result = editor._save(handle, [{"type": "Game", "day": "wed"}])

        assert result is False
        # In-memory must NOT be mutated — split-brain would occur otherwise
        assert handle.user.event_schedule == original_schedule

    def test_add_on_empty_schedule_produces_single_entry_dsl(self):
        """Starting from empty DSL, add 'Game wed' → DSL is exactly 'Game wed'."""
        user = _make_user_record(tg_id=111, event_schedule="")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:add:d:Game:wed")

        asyncio.run(editor._on_callback(None, query))

        repo.set_event_schedule.assert_called_once_with(111, "Game wed")
        assert handle.user.event_schedule == "Game wed"

    def test_add_day_unknown_day_rejected(self):
        """sch:add:d:Game:xyz → alert 'Internal error, try again.', no mutation."""
        user = _make_user_record(tg_id=111, event_schedule="Game wed")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:add:d:Game:xyz")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "internal error" in text.lower()
        assert show_alert is True
        repo.set_event_schedule.assert_not_called()
        assert handle.user.event_schedule == "Game wed"  # unchanged

    def test_add_day_unknown_type_rejected(self):
        """sch:add:d:UnknownType:wed → alert, no mutation."""
        user = _make_user_record(tg_id=111, event_schedule="Game wed")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:add:d:UnknownType:wed")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert show_alert is True
        repo.set_event_schedule.assert_not_called()
        assert handle.user.event_schedule == "Game wed"  # unchanged


# ---------------------------------------------------------------------------
# Tests: unique-day filter in add-day picker
# ---------------------------------------------------------------------------


def _collect_button_datas(markup):
    datas = []
    if markup and hasattr(markup, "inline_keyboard"):
        for row in markup.inline_keyboard:
            for btn in row:
                datas.append(btn.callback_data)
    return datas


class TestUniqueDayPicker:
    def test_day_picker_excludes_taken_day_for_same_type(self):
        """Schedule 'Game wed' → picking Game shows 6 days, no wed."""
        user = _make_user_record(tg_id=111, event_schedule="Game wed")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:add:t:Game")

        asyncio.run(editor._on_callback(None, query))

        edit_call = query.message.edit_text.call_args
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        datas = _collect_button_datas(markup)
        day_datas = [d for d in datas if d.startswith("sch:add:d:Game:")]
        assert len(day_datas) == 6, f"Expected 6 day buttons, got: {day_datas}"
        assert "sch:add:d:Game:wed" not in day_datas

    def test_day_picker_includes_day_taken_for_other_type(self):
        """Schedule 'Game wed' → picking Training still offers wed."""
        user = _make_user_record(tg_id=111, event_schedule="Game wed")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:add:t:Training")

        asyncio.run(editor._on_callback(None, query))

        edit_call = query.message.edit_text.call_args
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        datas = _collect_button_datas(markup)
        day_datas = [d for d in datas if d.startswith("sch:add:d:Training:")]
        assert len(day_datas) == 7, f"Expected 7 day buttons, got: {day_datas}"
        assert "sch:add:d:Training:wed" in day_datas

    def test_day_picker_all_days_taken_shows_explanation(self):
        """All 7 Games scheduled → picker shows no day buttons, only Back, with explanation text."""
        dsl = "; ".join(f"Game {d}" for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
        user = _make_user_record(tg_id=111, event_schedule=dsl)
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:add:t:Game")

        asyncio.run(editor._on_callback(None, query))

        edit_call = query.message.edit_text.call_args
        text = edit_call.args[0] if edit_call.args else edit_call.kwargs.get("text", "")
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        datas = _collect_button_datas(markup)

        day_datas = [d for d in datas if d.startswith("sch:add:d:Game:")]
        assert day_datas == [], f"Expected no day buttons, got: {day_datas}"
        assert "already scheduled" in text.lower()
        # Back button still present
        assert "sch:add" in datas

    def test_day_picker_parse_failure_alerts(self):
        """Malformed event_schedule → alert, no edit_text."""
        user = _make_user_record(tg_id=111, event_schedule="x")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:add:t:Game")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "internal error" in text.lower()
        assert show_alert is True
        query.message.edit_text.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Remove flow (Task 6)
# ---------------------------------------------------------------------------

class TestRemoveFlow:
    def test_remove_callback_shows_list(self):
        """sch:rm with 3 events → edit_text with 3 numbered rows + Back."""
        user = _make_user_record(
            tg_id=111,
            event_schedule="Game wed; Training tue; Game sat",
        )
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:rm")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_text.assert_awaited_once()
        edit_call = query.message.edit_text.call_args
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        button_datas = []
        button_texts = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_texts.append(btn.text)
                    button_datas.append(btn.callback_data)

        # 3 remove-entry buttons + 1 Back
        rm_buttons = [d for d in button_datas if d.startswith("sch:rm:")]
        assert len(rm_buttons) == 3, f"Expected 3 remove buttons, got: {rm_buttons}"
        assert any("Back" in t for t in button_texts), f"No Back button: {button_texts}"

        # Check numbered labels appear in order
        numbered = [t for t in button_texts if t.startswith("1.") or t.startswith("2.") or t.startswith("3.")]
        assert len(numbered) == 3, f"Expected 3 numbered buttons, got: {numbered}"
        assert any("1." in t and "Game" in t and "wed" in t for t in button_texts)
        assert any("2." in t and "Training" in t and "tue" in t for t in button_texts)
        assert any("3." in t and "Game" in t and "sat" in t for t in button_texts)

    def test_remove_index_deletes_and_redraws_remove(self):
        """sch:rm:1 removes index 1, saves new DSL, re-renders remove list (not main)."""
        user = _make_user_record(
            tg_id=111,
            event_schedule="Game wed; Training tue; Game sat",
        )
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:rm:1")

        asyncio.run(editor._on_callback(None, query))

        # Repo called with index-1 entry ("Training tue") removed
        repo.set_event_schedule.assert_called_once_with(111, "Game wed; Game sat")
        assert handle.user.event_schedule == "Game wed; Game sat"

        # Re-rendered remove list (not main — edit_text is called)
        query.message.edit_text.assert_awaited_once()
        edit_call = query.message.edit_text.call_args
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        button_datas = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_datas.append(btn.callback_data)

        # After removal: 2 remove buttons remaining
        rm_buttons = [d for d in button_datas if d.startswith("sch:rm:")]
        assert len(rm_buttons) == 2, f"Expected 2 remove buttons after delete, got: {rm_buttons}"

    def test_remove_last_entry_shows_empty_state(self):
        """Removing the only entry shows 'Nothing left to remove.' + Back only."""
        user = _make_user_record(tg_id=111, event_schedule="Game wed")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:rm:0")

        asyncio.run(editor._on_callback(None, query))

        repo.set_event_schedule.assert_called_once_with(111, "")
        assert handle.user.event_schedule == ""

        edit_call = query.message.edit_text.call_args
        text = edit_call.args[0] if edit_call.args else edit_call.kwargs.get("text", "")
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        assert "nothing" in text.lower() or "empty" in text.lower() or "remove" in text.lower()

        button_datas = []
        button_texts = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_texts.append(btn.text)
                    button_datas.append(btn.callback_data)

        rm_buttons = [d for d in button_datas if d.startswith("sch:rm:")]
        assert len(rm_buttons) == 0, f"Expected no remove buttons: {rm_buttons}"
        assert any("Back" in t for t in button_texts), f"No Back button: {button_texts}"

    def test_remove_invalid_index_rejected(self):
        """sch:rm:99 → alert 'Entry no longer exists.', no mutation, re-render list.

        query.answer must be called exactly once (with show_alert=True) — the
        fix ensures _render_remove_list does NOT issue a second answer() call.
        """
        user = _make_user_record(tg_id=111, event_schedule="Game wed; Training tue")
        original_schedule = user.event_schedule
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:rm:99")

        asyncio.run(editor._on_callback(None, query))

        # Alert issued exactly once (no double-answer bug)
        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        alert_text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "Entry no longer exists." in alert_text
        assert show_alert is True

        # No mutation
        repo.set_event_schedule.assert_not_called()
        assert handle.user.event_schedule == original_schedule

        # Remove list re-rendered
        query.message.edit_text.assert_awaited_once()
        edit_call = query.message.edit_text.call_args
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        button_datas = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_datas.append(btn.callback_data)
        rm_buttons = [d for d in button_datas if d.startswith("sch:rm:")]
        assert len(rm_buttons) == 2, f"Expected 2 remove buttons on re-render: {rm_buttons}"

    def test_remove_back_returns_to_main(self):
        """The Back button on the remove screen (sch:main) re-renders main via sch:main handler."""
        user = _make_user_record(tg_id=111, event_schedule="Game wed")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:main")

        asyncio.run(editor._on_callback(None, query))

        query.message.edit_text.assert_awaited_once()
        edit_call = query.message.edit_text.call_args
        text = edit_call.args[0] if edit_call.args else edit_call.kwargs.get("text", "")
        # Main screen shows schedule content
        assert "Game" in text or "schedule" in text.lower()

        # Back button itself is sch:main callback_data in the remove list
        # (verified by checking the rendered remove list has the correct callback_data)
        user2 = _make_user_record(tg_id=222, event_schedule="Game wed")
        handle2 = _make_voter_handle(user2)
        handles2 = {222: handle2}
        editor2 = _make_editor(handles=handles2)
        query2 = _make_query(from_user_id=222, data="sch:rm")

        asyncio.run(editor2._on_callback(None, query2))

        edit_call2 = query2.message.edit_text.call_args
        markup2 = edit_call2.kwargs.get("reply_markup") or (
            edit_call2.args[1] if len(edit_call2.args) > 1 else None
        )
        back_datas = []
        if markup2 and hasattr(markup2, "inline_keyboard"):
            for row in markup2.inline_keyboard:
                for btn in row:
                    if "Back" in btn.text:
                        back_datas.append(btn.callback_data)
        assert back_datas == ["sch:main"], f"Back button should be sch:main, got: {back_datas}"

    def test_remove_db_failure_alerts_and_rerenders(self):
        """repo raises → alert with correct text, handle.user.event_schedule unchanged, remove list re-rendered.

        query.answer must be called exactly once (with show_alert=True) — the
        fix ensures _render_remove_list does NOT issue a second answer() call.
        """
        import sqlite3 as sqlite3_mod
        user = _make_user_record(tg_id=111, event_schedule="Game wed; Training tue")
        original_schedule = user.event_schedule
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        repo.set_event_schedule.side_effect = sqlite3_mod.Error("disk full")
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:rm:0")

        asyncio.run(editor._on_callback(None, query))

        # In-memory NOT mutated
        assert handle.user.event_schedule == original_schedule

        # Alert issued exactly once (no double-answer bug)
        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        alert_text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "Save failed, try again." in alert_text
        assert show_alert is True

        # Remove list still re-rendered (user sees current DB state)
        query.message.edit_text.assert_awaited_once()
        edit_call = query.message.edit_text.call_args
        markup = edit_call.kwargs.get("reply_markup") or (
            edit_call.args[1] if len(edit_call.args) > 1 else None
        )
        button_datas = []
        if markup and hasattr(markup, "inline_keyboard"):
            for row in markup.inline_keyboard:
                for btn in row:
                    button_datas.append(btn.callback_data)
        # Original 2 entries remain
        rm_buttons = [d for d in button_datas if d.startswith("sch:rm:")]
        assert len(rm_buttons) == 2, f"Expected 2 remove buttons: {rm_buttons}"


# ---------------------------------------------------------------------------
# Tests: parse-failure paths
# ---------------------------------------------------------------------------

class TestParseFailurePaths:
    def test_handle_add_day_parse_failure_alerts(self):
        """If event_schedule is unparseable when adding, alert shown and no save."""
        user = _make_user_record(tg_id=111, event_schedule="x")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:add:d:Game:wed")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        alert_text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "Save failed, try again." in alert_text
        assert show_alert is True
        repo.set_event_schedule.assert_not_called()
        query.message.edit_text.assert_not_called()

    def test_render_remove_list_parse_failure_alerts(self):
        """If event_schedule is unparseable when rendering remove list, alert shown."""
        user = _make_user_record(tg_id=111, event_schedule="x")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        editor = _make_editor(handles=handles)
        query = _make_query(from_user_id=111, data="sch:rm")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        alert_text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "Internal error, try again." in alert_text
        assert show_alert is True
        query.message.edit_text.assert_not_called()

    def test_handle_remove_index_parse_failure_alerts(self):
        """If event_schedule is unparseable when removing by index, alert shown and no save."""
        user = _make_user_record(tg_id=111, event_schedule="x")
        handle = _make_voter_handle(user)
        handles = {111: handle}
        repo = _make_repo()
        editor = _make_editor(handles=handles, repo=repo)
        query = _make_query(from_user_id=111, data="sch:rm:0")

        asyncio.run(editor._on_callback(None, query))

        query.answer.assert_awaited_once()
        answer_call = query.answer.call_args
        alert_text = answer_call.args[0] if answer_call.args else answer_call.kwargs.get("text", "")
        show_alert = answer_call.kwargs.get("show_alert", False)
        assert "Internal error, try again." in alert_text
        assert show_alert is True
        repo.set_event_schedule.assert_not_called()
        query.message.edit_text.assert_not_called()
