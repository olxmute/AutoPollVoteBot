-- depends:

CREATE TABLE IF NOT EXISTS users
(
    id
    INTEGER
    PRIMARY
    KEY
    AUTOINCREMENT,
    session_name
    TEXT
    UNIQUE
    NOT
    NULL,
    session_string
    TEXT
    NOT
    NULL,
    event_schedule
    TEXT
    NOT
    NULL,
    vote_delay_seconds
    INTEGER
    NOT
    NULL
    DEFAULT
    5,
    enabled
    BOOLEAN
    NOT
    NULL
    DEFAULT
    1
);
