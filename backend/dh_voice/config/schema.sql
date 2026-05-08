-- Reference schema. Migrations run automatically via src/config/database.ts on startup.

CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  created_at    INTEGER NOT NULL        -- Unix timestamp (seconds)
);

CREATE TABLE IF NOT EXISTS tasks (
  id                   TEXT PRIMARY KEY,
  user_id              TEXT NOT NULL REFERENCES users(id),
  title                TEXT NOT NULL,
  description          TEXT,
  due_date             INTEGER,          -- Unix timestamp, nullable
  status               TEXT NOT NULL DEFAULT 'pending',
                                         -- pending | completed | cancelled | delayed
  original_voice_input TEXT,            -- raw transcribed text
  parse_confidence     REAL,            -- 0.0-1.0 from NLP service
  ambiguous_fields     TEXT,            -- JSON array, e.g. ["due_date"]
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL,
  completed_at         INTEGER,
  original_due_date    INTEGER,          -- set when first delayed, never overwritten
  delay_count          INTEGER NOT NULL DEFAULT 0,
  delay_reason         TEXT
);

CREATE TABLE IF NOT EXISTS task_history (
  id        TEXT PRIMARY KEY,
  task_id   TEXT NOT NULL REFERENCES tasks(id),
  action    TEXT NOT NULL,               -- created | completed | cancelled | delayed | updated
  metadata  TEXT,                        -- JSON blob
  timestamp INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_id  ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_history_task   ON task_history(task_id);
