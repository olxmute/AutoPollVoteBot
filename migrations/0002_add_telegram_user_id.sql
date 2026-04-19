-- depends: 0001_create_users

ALTER TABLE users ADD COLUMN telegram_user_id INTEGER;
CREATE UNIQUE INDEX idx_users_telegram_user_id
    ON users(telegram_user_id) WHERE telegram_user_id IS NOT NULL;
