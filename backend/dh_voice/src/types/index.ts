export interface User {
  id: string;
  email: string;
  name: string;
  password_hash: string;
  created_at: number;
}

export type TaskStatus = 'pending' | 'completed' | 'cancelled' | 'delayed';
export type TaskPriority = 'high' | 'medium' | 'low';

export interface Task {
  id: string;
  user_id: string;
  title: string;
  description: string | null;
  due_date: number | null;
  status: TaskStatus;
  priority: TaskPriority;
  original_voice_input: string | null;
  parse_confidence: number | null;
  ambiguous_fields: string | null;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
  original_due_date: number | null;
  delay_count: number;
  delay_reason: string | null;
}

export interface TaskHistory {
  id: string;
  task_id: string;
  action: string;
  metadata: string | null;
  timestamp: number;
}

export interface ParsedTask {
  title: string;
  description: string | null;
  due_date: string | null;
  priority: TaskPriority;
  confidence: {
    title: number;
    due_date: number;
  };
  ambiguous_fields: string[];
  multiple_tasks: boolean;
}

export interface IntentTaskData {
  title: string | null;
  description: string | null;
  due_date: string | null;
  priority: TaskPriority;
  ambiguous_fields: string[];
  multiple_tasks: boolean;
}

export interface VoiceQueryFilters {
  status?: string | null;
  date_range?: 'today' | 'this_week' | 'overdue' | null;
  search_term?: string | null;
}

export interface VoiceIntent {
  intent: 'create' | 'complete' | 'cancel' | 'delay' | 'query';
  confidence: number;
  task_ref: string | null;
  task_data: IntentTaskData | null;
  query_filters: VoiceQueryFilters | null;
  raw_text: string;
}

export interface JwtPayload {
  userId: string;
  email: string;
}

declare module 'fastify' {
  interface FastifyRequest {
    user: {
      userId: string;
      email: string;
    };
  }
}
