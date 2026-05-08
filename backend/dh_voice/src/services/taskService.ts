import { v4 as uuidv4 } from 'uuid';
import { queryOne, queryAll, execute } from '../config/db-helpers.js';
import { Task, TaskPriority } from '../types/index.js';
import { parseIsoToUnix, nowUnix } from './dateParser.js';

export interface CreateTaskInput {
  userId: string;
  title: string;
  description?: string | null;
  due_date?: string | null;
  priority?: TaskPriority;
  original_voice_input?: string | null;
  parse_confidence?: number | null;
  ambiguous_fields?: string[] | null;
}

export interface UpdateTaskInput {
  title?: string;
  description?: string | null;
  due_date?: string | null;
  priority?: TaskPriority;
}

export function createTask(input: CreateTaskInput): Task {
  const now = nowUnix();
  const id = uuidv4();
  const dueUnix = input.due_date ? parseIsoToUnix(input.due_date) : null;
  const ambiguous = input.ambiguous_fields ? JSON.stringify(input.ambiguous_fields) : null;

  execute(
    `INSERT INTO tasks
      (id, user_id, title, description, due_date, priority, status, original_voice_input,
       parse_confidence, ambiguous_fields, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)`,
    [
      id, input.userId, input.title, input.description ?? null,
      dueUnix, input.priority ?? 'medium',
      input.original_voice_input ?? null,
      input.parse_confidence ?? null, ambiguous, now, now,
    ]
  );

  logHistory(id, 'created', null, now);
  return getTaskById(id)!;
}

export function getTaskById(id: string): Task | null {
  return queryOne<Task>('SELECT * FROM tasks WHERE id = ?', [id]);
}

export function listTasks(
  userId: string,
  filters: { status?: string; from?: string; to?: string; overdue?: boolean; priority?: string }
): Task[] {
  const conditions: string[] = ['user_id = ?'];
  const params: (string | number | null)[] = [userId];

  if (filters.status) { conditions.push('status = ?'); params.push(filters.status); }
  if (filters.priority) { conditions.push('priority = ?'); params.push(filters.priority); }
  if (filters.from) { conditions.push('due_date >= ?'); params.push(parseIsoToUnix(filters.from)); }
  if (filters.to)   { conditions.push('due_date <= ?'); params.push(parseIsoToUnix(filters.to)); }
  if (filters.overdue) {
    conditions.push("status = 'pending' AND due_date IS NOT NULL AND due_date < ?");
    params.push(nowUnix());
  }

  return queryAll<Task>(
    `SELECT * FROM tasks WHERE ${conditions.join(' AND ')}
     ORDER BY
       CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
       CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
       due_date ASC, created_at DESC`,
    params
  );
}

export function searchTasks(userId: string, query: string, activeOnly = true): Task[] {
  if (!query.trim()) return [];

  const keywords = query
    .toLowerCase()
    .split(/\s+/)
    .filter((w) => w.length > 2 && !STOP_WORDS.has(w));

  if (keywords.length === 0) return [];

  const conditions: string[] = ['user_id = ?'];
  const params: (string | number | null)[] = [userId];

  if (activeOnly) {
    conditions.push("status NOT IN ('completed', 'cancelled')");
  }

  for (const kw of keywords) {
    conditions.push('LOWER(title) LIKE ?');
    params.push(`%${kw}%`);
  }

  return queryAll<Task>(
    `SELECT * FROM tasks WHERE ${conditions.join(' AND ')} ORDER BY updated_at DESC LIMIT 10`,
    params
  );
}

export function updateTask(id: string, input: UpdateTaskInput): Task | null {
  const now = nowUnix();
  const sets: string[] = ['updated_at = ?'];
  const params: (string | number | null)[] = [now];

  if (input.title !== undefined)       { sets.push('title = ?');       params.push(input.title); }
  if (input.description !== undefined) { sets.push('description = ?'); params.push(input.description); }
  if (input.priority !== undefined)    { sets.push('priority = ?');    params.push(input.priority); }
  if (input.due_date !== undefined) {
    sets.push('due_date = ?');
    params.push(input.due_date ? parseIsoToUnix(input.due_date) : null);
  }

  params.push(id);
  execute(`UPDATE tasks SET ${sets.join(', ')} WHERE id = ?`, params);
  logHistory(id, 'updated', { fields: Object.keys(input) }, now);
  return getTaskById(id);
}

export function completeTask(id: string): Task | null {
  const now = nowUnix();
  execute(
    `UPDATE tasks SET status = 'completed', completed_at = ?, updated_at = ? WHERE id = ?`,
    [now, now, id]
  );
  logHistory(id, 'completed', null, now);
  return getTaskById(id);
}

export function cancelTask(id: string): Task | null {
  const now = nowUnix();
  execute(`UPDATE tasks SET status = 'cancelled', updated_at = ? WHERE id = ?`, [now, id]);
  logHistory(id, 'cancelled', null, now);
  return getTaskById(id);
}

export function delayTask(id: string, newDueDate: string, reason?: string): Task | null {
  const now = nowUnix();
  const task = getTaskById(id);
  if (!task) return null;

  const newDueUnix = parseIsoToUnix(newDueDate);
  const originalDue = task.original_due_date ?? task.due_date;

  execute(
    `UPDATE tasks
     SET status = 'delayed', due_date = ?, original_due_date = ?,
         delay_count = delay_count + 1, delay_reason = ?, updated_at = ?
     WHERE id = ?`,
    [newDueUnix, originalDue, reason ?? null, now, id]
  );
  logHistory(id, 'delayed', { new_due_date: newDueDate, reason: reason ?? null }, now);
  return getTaskById(id);
}

export function getTaskHistory(taskId: string): unknown[] {
  return queryAll('SELECT * FROM task_history WHERE task_id = ? ORDER BY timestamp ASC', [taskId]);
}

function logHistory(taskId: string, action: string, metadata: object | null, timestamp: number): void {
  execute(
    'INSERT INTO task_history (id, task_id, action, metadata, timestamp) VALUES (?, ?, ?, ?, ?)',
    [uuidv4(), taskId, action, metadata ? JSON.stringify(metadata) : null, timestamp]
  );
}

export function assertOwner(task: Task | null, userId: string): task is Task {
  return task !== null && task.user_id === userId;
}

export function serializeTask(task: Task) {
  return {
    ...task,
    due_date: task.due_date ? new Date(task.due_date * 1000).toISOString() : null,
    original_due_date: task.original_due_date ? new Date(task.original_due_date * 1000).toISOString() : null,
    completed_at: task.completed_at ? new Date(task.completed_at * 1000).toISOString() : null,
    created_at: new Date(task.created_at * 1000).toISOString(),
    updated_at: new Date(task.updated_at * 1000).toISOString(),
    ambiguous_fields: task.ambiguous_fields ? JSON.parse(task.ambiguous_fields as string) : [],
  };
}

const STOP_WORDS = new Set([
  'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was',
  'been', 'has', 'have', 'had', 'not', 'but', 'can', 'will', 'its',
]);
