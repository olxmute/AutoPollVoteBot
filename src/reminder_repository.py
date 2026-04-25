import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List


@dataclass
class DueReminder:
    id: int
    telegram_user_id: int
    chat_id: int
    topic_id: int
    poll_message_id: int
    topic_name: str
    event_datetime: str
    reminder_lead_hours: int


class ReminderRepository:
    """SQLite-backed store for reminder rows; one row per (user, chat, topic)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def upsert(
        self,
        telegram_user_id: int,
        chat_id: int,
        topic_id: int,
        poll_message_id: int,
        topic_name: str,
        event_datetime_utc: datetime,
    ) -> None:
        """Insert or refresh a reminder row.

        On conflict (same telegram_user_id, chat_id, topic_id), refreshes
        poll_message_id, topic_name, and event_datetime only if the row has
        not yet been sent (reminded_at IS NULL).  Already-sent rows are left
        untouched so they are not re-fired.
        """
        event_str = event_datetime_utc.strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO reminders
                    (telegram_user_id, chat_id, topic_id, poll_message_id, topic_name, event_datetime)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (telegram_user_id, chat_id, topic_id) DO UPDATE SET
                    poll_message_id = excluded.poll_message_id,
                    topic_name      = excluded.topic_name,
                    event_datetime  = excluded.event_datetime
                WHERE reminders.reminded_at IS NULL
                """,
                (telegram_user_id, chat_id, topic_id, poll_message_id, topic_name, event_str),
            )
            conn.commit()
        finally:
            conn.close()

    def fetch_due(self) -> List[DueReminder]:
        """Return all unsent reminder rows for enabled users with future events.

        SQL gates on:
        - reminded_at IS NULL   (not yet sent)
        - reminders_enabled = 1 (user has reminders on)
        - event_datetime > now  (event hasn't passed yet)

        The lead-hours cutoff is NOT applied here — it is returned as a column
        so the caller can apply it in Python for full testability.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                SELECT r.id, r.telegram_user_id, r.chat_id, r.topic_id, r.poll_message_id,
                       r.topic_name, r.event_datetime, u.reminder_lead_hours
                FROM reminders r
                JOIN users u ON u.telegram_user_id = r.telegram_user_id
                WHERE r.reminded_at IS NULL
                  AND u.reminders_enabled = 1
                  AND datetime(r.event_datetime) > datetime('now')
                """
            )
            return [
                DueReminder(
                    id=row[0],
                    telegram_user_id=row[1],
                    chat_id=row[2],
                    topic_id=row[3],
                    poll_message_id=row[4],
                    topic_name=row[5],
                    event_datetime=row[6],
                    reminder_lead_hours=row[7],
                )
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()

    def mark_reminded(self, reminder_id: int) -> int:
        """Stamp reminded_at = now for the given reminder row.

        Returns the number of rows updated (1 on hit, 0 if the id is unknown).
        """
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "UPDATE reminders SET reminded_at = datetime('now') WHERE id = ?",
                (reminder_id,),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def delete_old(self, retention_days: int = 7) -> int:
        """Delete reminder rows whose event_datetime is older than retention_days.

        Returns the number of rows deleted.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "DELETE FROM reminders WHERE datetime(event_datetime) < datetime('now', ?)",
                (f"-{retention_days} days",),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()
