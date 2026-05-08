import { getDb, persist } from './database.js';

type SqlParam = string | number | null | undefined;

export function queryOne<T>(sql: string, params: SqlParam[] = []): T | null {
  const db = getDb();
  const stmt = db.prepare(sql);
  stmt.bind(params as (string | number | null)[]);
  const row = stmt.step() ? (stmt.getAsObject() as unknown as T) : null;
  stmt.free();
  return row;
}

export function queryAll<T>(sql: string, params: SqlParam[] = []): T[] {
  const db = getDb();
  const stmt = db.prepare(sql);
  stmt.bind(params as (string | number | null)[]);
  const rows: T[] = [];
  while (stmt.step()) {
    rows.push(stmt.getAsObject() as unknown as T);
  }
  stmt.free();
  return rows;
}

export function execute(sql: string, params: SqlParam[] = []): void {
  getDb().run(sql, params as (string | number | null)[]);
  persist();
}
