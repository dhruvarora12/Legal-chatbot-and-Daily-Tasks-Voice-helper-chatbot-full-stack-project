import initSqlJs, { Database, SqlJsStatic } from 'sql.js';
import path from 'path';
import fs from 'fs';

let SQL: SqlJsStatic | null = null;
let db: Database | null = null;
let dbPath: string;

export async function initDb(): Promise<void> {
  dbPath = path.resolve(process.env.DB_PATH ?? './data/tasks.db');
  const dir = path.dirname(dbPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  SQL = await initSqlJs();

  if (fs.existsSync(dbPath)) {
    const fileBuffer = fs.readFileSync(dbPath);
    db = new SQL.Database(fileBuffer);
  } else {
    db = new SQL.Database();
  }

  db.run('PRAGMA foreign_keys = ON;');
  runMigrations();
  persist();
}

export function getDb(): Database {
  if (!db) throw new Error('Database not initialised — call initDb() first');
  return db;
}

export function persist(): void {
  if (!db) return;
  const data = db.export();
  fs.writeFileSync(dbPath, Buffer.from(data));
}

function runMigrations(): void {
  db!.run(`
    CREATE TABLE IF NOT EXISTS users (
      id            TEXT PRIMARY KEY,
      email         TEXT UNIQUE NOT NULL,
      name          TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      created_at    INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
      id                   TEXT PRIMARY KEY,
      user_id              TEXT NOT NULL REFERENCES users(id),
      title                TEXT NOT NULL,
      description          TEXT,
      due_date             INTEGER,
      status               TEXT NOT NULL DEFAULT 'pending',
      original_voice_input TEXT,
      parse_confidence     REAL,
      ambiguous_fields     TEXT,
      created_at           INTEGER NOT NULL,
      updated_at           INTEGER NOT NULL,
      completed_at         INTEGER,
      original_due_date    INTEGER,
      delay_count          INTEGER NOT NULL DEFAULT 0,
      delay_reason         TEXT
    );

    CREATE TABLE IF NOT EXISTS task_history (
      id        TEXT PRIMARY KEY,
      task_id   TEXT NOT NULL REFERENCES tasks(id),
      action    TEXT NOT NULL,
      metadata  TEXT,
      timestamp INTEGER NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_user_id  ON tasks(user_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
    CREATE INDEX IF NOT EXISTS idx_history_task   ON task_history(task_id);
  `);

  // Additive migrations: ALTER TABLE for new columns on existing tables
  const alterations = [
    `ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'`,
    `CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)`,
  ];
  for (const sql of alterations) {
    try { db!.run(sql); } catch { /* column/index already exists — safe to ignore */ }
  }

  persist();
}
