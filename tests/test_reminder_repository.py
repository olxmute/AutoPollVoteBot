import sqlite3
from datetime import datetime, timezone

import pytest

from src.reminder_repository import DueReminder, ReminderRepository

_UTC = timezone.utc

# A datetime safely in the future for tests that need "future" events
_FUTURE = datetime(2099, 6, 15, 18, 0, 0, tzinfo=_UTC)
# A datetime safely in the past for tests that need "past" events
_PAST = datetime(2000, 1, 1, 10, 0, 0, tzinfo=_UTC)


def _insert_user(
    db_path: str,
    telegram_user_id: int,
    reminders_enabled: int = 1,
    reminder_lead_hours: int = 27,
) -> int:
    """Helper: insert a minimal enabled user row and return its id."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO users "
            "(session_name, session_string, event_schedule, vote_delay_seconds, "
            "enabled, telegram_user_id, reminders_enabled, reminder_lead_hours) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"sess_{telegram_user_id}",
                "string",
                "Game wed",
                5,
                1,
                telegram_user_id,
                reminders_enabled,
                reminder_lead_hours,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _insert_reminder(
    db_path: str,
    telegram_user_id: int = 42,
    chat_id: int = 100,
    topic_id: int = 200,
    poll_message_id: int = 300,
    topic_name: str = "Game 2099-06-15, Sun, 20:00-22:00",
    event_datetime: str = "2099-06-15 16:00:00",  # UTC
    reminded_at: str | None = None,
) -> int:
    """Helper: insert a reminder row directly and return its id."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO reminders "
            "(telegram_user_id, chat_id, topic_id, poll_message_id, topic_name, "
            "event_datetime, reminded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                telegram_user_id,
                chat_id,
                topic_id,
                poll_message_id,
                topic_name,
                event_datetime,
                reminded_at,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _fetch_reminder_row(db_path: str, reminder_id: int) -> dict:
    """Return all columns of a reminder row as a dict."""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests for upsert
# ---------------------------------------------------------------------------

class TestUpsert:
    def test_fresh_insert(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = ReminderRepository(tmp_db)
        repo.upsert(42, 100, 200, 300, "Game 2099-06-15, Sun, 20:00-22:00", _FUTURE)

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM reminders").fetchall()
        conn.close()

        assert len(rows) == 1
        row = dict(rows[0])
        assert row["telegram_user_id"] == 42
        assert row["chat_id"] == 100
        assert row["topic_id"] == 200
        assert row["poll_message_id"] == 300
        assert row["topic_name"] == "Game 2099-06-15, Sun, 20:00-22:00"
        assert row["event_datetime"] == "2099-06-15 18:00:00"  # UTC
        assert row["reminded_at"] is None

    def test_conflict_refreshes_non_sent_row(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = ReminderRepository(tmp_db)
        repo.upsert(42, 100, 200, 301, "Old topic", _FUTURE)

        new_dt = datetime(2099, 7, 20, 14, 0, 0, tzinfo=_UTC)
        repo.upsert(42, 100, 200, 999, "New topic", new_dt)

        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM reminders").fetchall()
        conn.close()

        assert len(rows) == 1  # no duplicate created
        row = dict(rows[0])
        assert row["poll_message_id"] == 999           # poll_message_id refreshed
        assert row["topic_name"] == "New topic"        # topic_name refreshed
        assert row["event_datetime"] == "2099-07-20 14:00:00"  # event_datetime refreshed
        assert row["reminded_at"] is None  # reminded_at still None

    def test_conflict_on_sent_row_leaves_all_columns_unchanged(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        original_dt = "2099-06-15 16:00:00"
        original_topic = "Game 2099-06-15, Sun, 20:00-22:00"
        original_poll_id = 300
        reminder_id = _insert_reminder(
            tmp_db,
            telegram_user_id=42,
            chat_id=100,
            topic_id=200,
            poll_message_id=original_poll_id,
            topic_name=original_topic,
            event_datetime=original_dt,
            reminded_at="2099-06-15 15:00:00",  # already sent
        )

        new_dt = datetime(2099, 7, 20, 14, 0, 0, tzinfo=_UTC)
        repo = ReminderRepository(tmp_db)
        repo.upsert(42, 100, 200, 999, "New topic", new_dt)

        row = _fetch_reminder_row(tmp_db, reminder_id)
        assert row["poll_message_id"] == original_poll_id
        assert row["topic_name"] == original_topic
        assert row["event_datetime"] == original_dt
        assert row["reminded_at"] == "2099-06-15 15:00:00"

    def test_different_topic_id_creates_separate_row(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = ReminderRepository(tmp_db)
        repo.upsert(42, 100, 200, 300, "Topic A", _FUTURE)
        repo.upsert(42, 100, 201, 301, "Topic B", _FUTURE)

        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
        conn.close()
        assert count == 2


# ---------------------------------------------------------------------------
# Tests for fetch_due
# ---------------------------------------------------------------------------

class TestFetchDue:
    def test_returns_due_row(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42, reminder_lead_hours=27)
        _insert_reminder(tmp_db, telegram_user_id=42)

        repo = ReminderRepository(tmp_db)
        due = repo.fetch_due()

        assert len(due) == 1
        r = due[0]
        assert isinstance(r, DueReminder)
        assert r.telegram_user_id == 42
        assert r.chat_id == 100
        assert r.topic_id == 200
        assert r.poll_message_id == 300
        assert r.topic_name == "Game 2099-06-15, Sun, 20:00-22:00"
        assert r.event_datetime == "2099-06-15 16:00:00"
        assert r.reminder_lead_hours == 27

    def test_excludes_row_with_reminded_at_set(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        _insert_reminder(tmp_db, telegram_user_id=42, reminded_at="2099-06-15 14:00:00")

        repo = ReminderRepository(tmp_db)
        due = repo.fetch_due()

        assert due == []

    def test_excludes_row_when_reminders_disabled(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42, reminders_enabled=0)
        _insert_reminder(tmp_db, telegram_user_id=42)

        repo = ReminderRepository(tmp_db)
        due = repo.fetch_due()

        assert due == []

    def test_excludes_past_event(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        _insert_reminder(
            tmp_db,
            telegram_user_id=42,
            event_datetime="2000-01-01 10:00:00",  # past
        )

        repo = ReminderRepository(tmp_db)
        due = repo.fetch_due()

        assert due == []

    def test_returned_row_carries_user_lead_hours(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42, reminder_lead_hours=48)
        _insert_reminder(tmp_db, telegram_user_id=42)

        repo = ReminderRepository(tmp_db)
        due = repo.fetch_due()

        assert len(due) == 1
        assert due[0].reminder_lead_hours == 48

    def test_multiple_users_with_mixed_eligibility(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=10, reminders_enabled=1)
        _insert_user(tmp_db, telegram_user_id=20, reminders_enabled=0)
        _insert_user(tmp_db, telegram_user_id=30, reminders_enabled=1)

        _insert_reminder(tmp_db, telegram_user_id=10, topic_id=1)
        _insert_reminder(tmp_db, telegram_user_id=20, topic_id=2)  # disabled
        _insert_reminder(tmp_db, telegram_user_id=30, topic_id=3,
                         reminded_at="2099-01-01 00:00:00")         # already sent

        repo = ReminderRepository(tmp_db)
        due = repo.fetch_due()

        assert len(due) == 1
        assert due[0].telegram_user_id == 10


# ---------------------------------------------------------------------------
# Tests for mark_reminded
# ---------------------------------------------------------------------------

class TestMarkReminded:
    def test_sets_reminded_at_and_returns_one(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        reminder_id = _insert_reminder(tmp_db, telegram_user_id=42)

        repo = ReminderRepository(tmp_db)
        result = repo.mark_reminded(reminder_id)

        assert result == 1
        row = _fetch_reminder_row(tmp_db, reminder_id)
        assert row["reminded_at"] is not None

    def test_returns_zero_for_unknown_id(self, tmp_db):
        repo = ReminderRepository(tmp_db)
        result = repo.mark_reminded(99999)
        assert result == 0

    def test_marked_row_no_longer_returned_by_fetch_due(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        reminder_id = _insert_reminder(tmp_db, telegram_user_id=42)

        repo = ReminderRepository(tmp_db)
        assert len(repo.fetch_due()) == 1
        repo.mark_reminded(reminder_id)
        assert repo.fetch_due() == []


# ---------------------------------------------------------------------------
# Tests for delete_old
# ---------------------------------------------------------------------------

class TestDeleteOld:
    def test_removes_old_rows_and_returns_count(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        # Insert two past rows (older than default 7 days)
        _insert_reminder(tmp_db, telegram_user_id=42, topic_id=1,
                         event_datetime="2000-01-01 10:00:00")
        _insert_reminder(tmp_db, telegram_user_id=42, topic_id=2,
                         event_datetime="2000-06-15 10:00:00")

        repo = ReminderRepository(tmp_db)
        deleted = repo.delete_old(7)

        assert deleted == 2
        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
        conn.close()
        assert count == 0

    def test_keeps_recent_rows(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        _insert_reminder(tmp_db, telegram_user_id=42, topic_id=1,
                         event_datetime="2099-06-15 16:00:00")  # future

        repo = ReminderRepository(tmp_db)
        deleted = repo.delete_old(7)

        assert deleted == 0
        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
        conn.close()
        assert count == 1

    def test_respects_custom_retention_days(self, tmp_db):
        """A row from 3 days ago should be kept with 7-day retention but removed with 1-day."""
        import sqlite3 as _sqlite3
        _insert_user(tmp_db, telegram_user_id=42)

        # Insert a row dated 3 days ago using SQLite's datetime modifier
        conn = _sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO reminders "
            "(telegram_user_id, chat_id, topic_id, poll_message_id, topic_name, event_datetime) "
            "VALUES (42, 100, 200, 300, 'Test', datetime('now', '-3 days'))"
        )
        conn.commit()
        conn.close()

        repo = ReminderRepository(tmp_db)

        # 7-day retention → keep it
        deleted = repo.delete_old(7)
        assert deleted == 0

        # 1-day retention → remove it
        deleted = repo.delete_old(1)
        assert deleted == 1

    def test_returns_zero_when_nothing_to_delete(self, tmp_db):
        repo = ReminderRepository(tmp_db)
        deleted = repo.delete_old(7)
        assert deleted == 0
