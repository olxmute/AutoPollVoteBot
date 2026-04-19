import sqlite3
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class UserRecord:
    id: int
    session_name: str
    session_string: str
    event_schedule: str
    vote_delay_seconds: int
    telegram_user_id: Optional[int]
    enabled: bool


class UserRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def get_enabled_users(self) -> List[UserRecord]:
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT id, session_name, session_string, event_schedule, vote_delay_seconds, "
                "telegram_user_id FROM users WHERE enabled = 1"
            )
            return [
                UserRecord(
                    id=row[0],
                    session_name=row[1],
                    session_string=row[2],
                    event_schedule=row[3],
                    vote_delay_seconds=row[4],
                    telegram_user_id=row[5],
                    enabled=True,
                )
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()

    def set_telegram_user_id(self, user_id: int, telegram_user_id: int) -> None:
        """Set the telegram_user_id for the given DB user row.

        Raises sqlite3.IntegrityError if another row already has the same telegram_user_id
        (enforced by the partial unique index).
        """
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE users SET telegram_user_id = ? WHERE id = ?",
                (telegram_user_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def set_enabled(self, telegram_user_id: int, enabled: bool) -> int:
        """Enable or disable voting for the user identified by telegram_user_id.

        Returns the number of rows affected (0 if no matching row, 1 on success).

        Raises ValueError if telegram_user_id is None, since NULL equality in SQL
        silently matches 0 rows.
        """
        if telegram_user_id is None:
            raise ValueError("telegram_user_id must not be None")
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "UPDATE users SET enabled = ? WHERE telegram_user_id = ?",
                (1 if enabled else 0, telegram_user_id),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()
