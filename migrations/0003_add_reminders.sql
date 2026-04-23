-- depends: 0002_add_telegram_user_id

ALTER TABLE users ADD COLUMN reminders_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN reminder_lead_hours INTEGER NOT NULL DEFAULT 27;

CREATE TABLE IF NOT EXISTS reminders
(
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    chat_id          INTEGER NOT NULL,
    topic_id         INTEGER NOT NULL,
    poll_message_id  INTEGER NOT NULL,
    topic_name       TEXT    NOT NULL,
    event_datetime   TEXT    NOT NULL, -- ISO-8601 UTC, seconds precision
    reminded_at      TEXT,             -- NULL = not yet sent
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (telegram_user_id, chat_id, topic_id)
);
CREATE INDEX idx_reminders_due ON reminders (reminded_at, event_datetime);
