import sqlite3
from dataclasses import dataclass
from typing import List


@dataclass
class UserRecord:
    id: int
    session_name: str
    session_string: str
    event_schedule: str
    vote_delay_seconds: int


def get_enabled_users(db_path: str) -> List[UserRecord]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT id, session_name, session_string, event_schedule, vote_delay_seconds "
            "FROM users WHERE enabled = 1"
        )
        return [UserRecord(*row) for row in cursor.fetchall()]
    finally:
        conn.close()
