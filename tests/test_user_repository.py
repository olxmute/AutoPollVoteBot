import sqlite3

import pytest

from src.user_repository import UserRecord, UserRepository


def _insert_user(db_path: str, session_name: str = "test_session",
                 session_string: str = "sess_abc", event_schedule: str = "Game wed",
                 vote_delay_seconds: int = 5, enabled: int = 1,
                 telegram_user_id: int | None = None) -> int:
    """Helper: insert a user row directly and return its id."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO users (session_name, session_string, event_schedule, "
            "vote_delay_seconds, enabled, telegram_user_id) VALUES (?, ?, ?, ?, ?, ?)",
            (session_name, session_string, event_schedule, vote_delay_seconds,
             enabled, telegram_user_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


class TestGetEnabledUsers:
    def test_returns_enabled_user_with_defaults(self, tmp_db):
        _insert_user(tmp_db)
        repo = UserRepository(tmp_db)
        users = repo.get_enabled_users()
        assert len(users) == 1
        u = users[0]
        assert isinstance(u, UserRecord)
        assert u.session_name == "test_session"
        assert u.session_string == "sess_abc"
        assert u.event_schedule == "Game wed"
        assert u.vote_delay_seconds == 5
        assert u.telegram_user_id is None
        assert u.enabled is True
        assert u.reminders_enabled is True
        assert u.reminder_lead_hours == 27

    def test_excludes_disabled_user(self, tmp_db):
        _insert_user(tmp_db, session_name="active", enabled=1)
        _insert_user(tmp_db, session_name="inactive", enabled=0)
        repo = UserRepository(tmp_db)
        users = repo.get_enabled_users()
        assert len(users) == 1
        assert users[0].session_name == "active"

    def test_empty_when_no_enabled_users(self, tmp_db):
        _insert_user(tmp_db, enabled=0)
        repo = UserRepository(tmp_db)
        assert repo.get_enabled_users() == []

    def test_telegram_user_id_hydrated_when_set(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=123456)
        repo = UserRepository(tmp_db)
        users = repo.get_enabled_users()
        assert users[0].telegram_user_id == 123456


class TestSetTelegramUserId:
    def test_sets_telegram_user_id(self, tmp_db):
        user_id = _insert_user(tmp_db)
        repo = UserRepository(tmp_db)
        repo.set_telegram_user_id(user_id, 99999)
        users = repo.get_enabled_users()
        assert users[0].telegram_user_id == 99999

    def test_upsert_overwrites_existing_telegram_user_id(self, tmp_db):
        user_id = _insert_user(tmp_db, telegram_user_id=111)
        repo = UserRepository(tmp_db)
        repo.set_telegram_user_id(user_id, 222)
        users = repo.get_enabled_users()
        assert users[0].telegram_user_id == 222

    def test_raises_integrity_error_on_duplicate_telegram_user_id(self, tmp_db):
        user_id_1 = _insert_user(tmp_db, session_name="user1")
        user_id_2 = _insert_user(tmp_db, session_name="user2")
        repo = UserRepository(tmp_db)
        repo.set_telegram_user_id(user_id_1, 77777)
        with pytest.raises(sqlite3.IntegrityError):
            repo.set_telegram_user_id(user_id_2, 77777)

    def test_set_telegram_user_id_nonexistent_user_id_does_nothing(self, tmp_db):
        """set_telegram_user_id with a non-existent user_id silently does nothing."""
        _insert_user(tmp_db, session_name="real_user")
        repo = UserRepository(tmp_db)
        # This should not raise; row 9999 does not exist
        repo.set_telegram_user_id(9999, 55555)
        # The real user's telegram_user_id should remain unchanged
        users = repo.get_enabled_users()
        assert users[0].telegram_user_id is None


class TestSetEventSchedule:
    def test_set_event_schedule_updates_row(self, tmp_db):
        """Insert a user with telegram_user_id set, call method, verify DB row and return == 1."""
        _insert_user(tmp_db, telegram_user_id=42, event_schedule="Game wed")
        repo = UserRepository(tmp_db)
        rows = repo.set_event_schedule(42, "Training tue")
        assert rows == 1
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT event_schedule FROM users WHERE telegram_user_id = 42"
        ).fetchone()
        conn.close()
        assert row[0] == "Training tue"

    def test_set_event_schedule_missing_user_returns_0(self, tmp_db):
        """Call with unknown telegram_user_id, verify returns 0 (no raise)."""
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        rows = repo.set_event_schedule(9999, "Game sat")
        assert rows == 0

    def test_set_event_schedule_none_telegram_id_raises(self, tmp_db):
        """Verify ValueError raised when telegram_user_id=None."""
        repo = UserRepository(tmp_db)
        with pytest.raises(ValueError, match="telegram_user_id must not be None"):
            repo.set_event_schedule(None, "Game wed")

    def test_set_event_schedule_reflected_in_get_enabled_users(self, tmp_db):
        """Subsequent get_enabled_users() reflects the updated event_schedule."""
        _insert_user(tmp_db, telegram_user_id=42, event_schedule="Game wed")
        repo = UserRepository(tmp_db)
        repo.set_event_schedule(42, "Training tue; Game sat")
        users = repo.get_enabled_users()
        assert len(users) == 1
        assert users[0].event_schedule == "Training tue; Game sat"


class TestSetEnabled:
    def test_returns_one_for_known_user(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        rows = repo.set_enabled(42, False)
        assert rows == 1

    def test_returns_zero_for_unknown_telegram_user_id(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        rows = repo.set_enabled(9999, False)
        assert rows == 0

    def test_disable_persists(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42, enabled=1)
        repo = UserRepository(tmp_db)
        repo.set_enabled(42, False)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT enabled FROM users WHERE telegram_user_id = 42").fetchone()
        conn.close()
        assert row[0] == 0

    def test_enable_persists(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42, enabled=0)
        repo = UserRepository(tmp_db)
        repo.set_enabled(42, True)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT enabled FROM users WHERE telegram_user_id = 42").fetchone()
        conn.close()
        assert row[0] == 1

    def test_set_enabled_idempotent_already_enabled(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42, enabled=1)
        repo = UserRepository(tmp_db)
        rows = repo.set_enabled(42, True)
        assert rows == 1  # row was matched and updated

    def test_set_disabled_idempotent_already_disabled(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42, enabled=0)
        repo = UserRepository(tmp_db)
        rows = repo.set_enabled(42, False)
        assert rows == 1  # row was matched and updated

    def test_set_enabled_none_raises_value_error(self, tmp_db):
        """set_enabled(None, ...) must raise ValueError (NULL equality silently matches 0 rows)."""
        repo = UserRepository(tmp_db)
        with pytest.raises(ValueError, match="telegram_user_id must not be None"):
            repo.set_enabled(None, True)


class TestSetRemindersEnabled:
    def test_returns_one_for_known_user(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        rows = repo.set_reminders_enabled(42, False)
        assert rows == 1

    def test_returns_zero_for_unknown_telegram_user_id(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        rows = repo.set_reminders_enabled(9999, False)
        assert rows == 0

    def test_disable_reminders_persists(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        repo.set_reminders_enabled(42, False)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT reminders_enabled FROM users WHERE telegram_user_id = 42"
        ).fetchone()
        conn.close()
        assert row[0] == 0

    def test_enable_reminders_persists(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        repo.set_reminders_enabled(42, False)
        repo.set_reminders_enabled(42, True)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT reminders_enabled FROM users WHERE telegram_user_id = 42"
        ).fetchone()
        conn.close()
        assert row[0] == 1

    def test_set_reminders_enabled_none_raises_value_error(self, tmp_db):
        repo = UserRepository(tmp_db)
        with pytest.raises(ValueError, match="telegram_user_id must not be None"):
            repo.set_reminders_enabled(None, True)


class TestSetReminderLeadHours:
    def test_returns_one_for_known_user(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        rows = repo.set_reminder_lead_hours(42, 48)
        assert rows == 1

    def test_returns_zero_for_unknown_telegram_user_id(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        rows = repo.set_reminder_lead_hours(9999, 48)
        assert rows == 0

    def test_persists_updated_hours(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        repo.set_reminder_lead_hours(42, 36)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT reminder_lead_hours FROM users WHERE telegram_user_id = 42"
        ).fetchone()
        conn.close()
        assert row[0] == 36

    def test_persists_arbitrary_integer_hours(self, tmp_db):
        _insert_user(tmp_db, telegram_user_id=42)
        repo = UserRepository(tmp_db)
        rows = repo.set_reminder_lead_hours(42, 1)
        assert rows == 1

    def test_raises_value_error_for_none_telegram_user_id(self, tmp_db):
        repo = UserRepository(tmp_db)
        with pytest.raises(ValueError, match="telegram_user_id must not be None"):
            repo.set_reminder_lead_hours(None, 10)

