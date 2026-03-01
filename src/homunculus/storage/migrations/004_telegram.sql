ALTER TABLE contacts ADD COLUMN telegram_chat_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_telegram_chat_id ON contacts(telegram_chat_id);
