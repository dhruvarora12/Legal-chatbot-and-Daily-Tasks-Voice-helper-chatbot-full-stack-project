import Groq from 'groq-sdk';
import { ParsedTask, VoiceIntent, IntentTaskData, TaskPriority } from '../types/index.js';
import { extractDateHints, DateHint } from './dateParser.js';

let groqClient: Groq | null = null;

function getGroq(): Groq {
  if (!groqClient) {
    groqClient = new Groq({ apiKey: process.env.GROQ_API_KEY });
  }
  return groqClient;
}

// Use 70B for intent detection — better reasoning, still free on Groq
const INTENT_MODEL = 'llama-3.3-70b-versatile';
// Use 8B for simple create-only parsing — faster for known-intent flows
const FAST_MODEL = 'llama-3.1-8b-instant';

const INTENT_SYSTEM_PROMPT = `You are a task management voice assistant. Determine the user's intent and extract all data needed to perform the action.

OUTPUT: Return ONLY valid JSON with this exact schema — no commentary, no markdown.
{
  "intent": "create" | "complete" | "cancel" | "delay" | "query",
  "confidence": number,
  "task_ref": string | null,
  "task_data": {
    "title": string | null,
    "description": string | null,
    "due_date": string | null,
    "priority": "high" | "medium" | "low",
    "ambiguous_fields": string[],
    "multiple_tasks": boolean
  } | null,
  "query_filters": {
    "status": "pending" | "completed" | "cancelled" | "delayed" | null,
    "date_range": "today" | "this_week" | "overdue" | null,
    "search_term": string | null
  } | null
}

INTENT RULES:
- create: adding/reminding/scheduling a new task
- complete: marking an existing task done/finished/completed
- cancel: cancelling/removing/abandoning a task
- delay: postponing/pushing/rescheduling a task
- query: listing/showing/finding/checking tasks

TASK_REF RULES (for complete/cancel/delay):
- Extract 2–5 key identifying words from the task name the user mentions
- Omit generic words: "the", "a", "task", "it", "that"
- Example: "finish the client proposal" → "client proposal"

TITLE RULES (for create/delay with context):
- Remove filler phrases: "remind me to", "don't forget to", "I need to", "make sure to", "please", "can you"
- Use imperative form: "Submit report" not "submitting the report"
- Remove time/date references from title (they go in due_date)
- Example: "Remind me to fix the login bug by tomorrow evening" → "Fix the login bug"

PRIORITY INFERENCE:
- high: "urgent", "ASAP", "critical", "immediately", "emergency", "right now", "as soon as possible"
- low: "whenever", "no rush", "low priority", "eventually", "someday", "when you get a chance", "not urgent"
- medium: everything else (default)

DUE DATE RULES:
- Use the pre-parsed dates provided (they are already resolved to absolute dates)
- If no date mentioned or vague ("soon", "later", "eventually") → null, add "due_date" to ambiguous_fields
- NEVER invent a date

EXAMPLES:

Input: "Remind me to submit the quarterly report by next Friday"
Output: {"intent":"create","confidence":0.95,"task_ref":null,"task_data":{"title":"Submit quarterly report","description":null,"due_date":"__NEXT_FRIDAY__","priority":"medium","ambiguous_fields":[],"multiple_tasks":false},"query_filters":null}

Input: "Mark the client proposal as done"
Output: {"intent":"complete","confidence":0.93,"task_ref":"client proposal","task_data":null,"query_filters":null}

Input: "Urgently fix the production bug — it's breaking checkout"
Output: {"intent":"create","confidence":0.92,"task_ref":null,"task_data":{"title":"Fix production bug","description":"Breaking checkout","due_date":null,"priority":"high","ambiguous_fields":["due_date"],"multiple_tasks":false},"query_filters":null}

Input: "Push the design review to next Tuesday"
Output: {"intent":"delay","confidence":0.9,"task_ref":"design review","task_data":{"title":null,"description":null,"due_date":"__NEXT_TUESDAY__","priority":"medium","ambiguous_fields":[],"multiple_tasks":false},"query_filters":null}

Input: "What tasks are overdue?"
Output: {"intent":"query","confidence":0.97,"task_ref":null,"task_data":null,"query_filters":{"status":"pending","date_range":"overdue","search_term":null}}

Input: "Show me everything due this week"
Output: {"intent":"query","confidence":0.96,"task_ref":null,"task_data":null,"query_filters":{"status":"pending","date_range":"this_week","search_term":null}}

Input: "Cancel the gym session"
Output: {"intent":"cancel","confidence":0.91,"task_ref":"gym session","task_data":null,"query_filters":null}

Input: "I need to buy groceries and also call the dentist"
Output: {"intent":"create","confidence":0.75,"task_ref":null,"task_data":{"title":"Buy groceries","description":null,"due_date":null,"priority":"medium","ambiguous_fields":["due_date"],"multiple_tasks":true},"query_filters":null}

Input: "Add a low-priority task to clean up the docs folder"
Output: {"intent":"create","confidence":0.9,"task_ref":null,"task_data":{"title":"Clean up docs folder","description":null,"due_date":null,"priority":"low","ambiguous_fields":["due_date"],"multiple_tasks":false},"query_filters":null}`;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function parseVoiceIntent(text: string): Promise<VoiceIntent> {
  const today = new Date().toISOString().split('T')[0];
  const dayOfWeek = new Date().toLocaleDateString('en-US', { weekday: 'long' });
  const dateHints = extractDateHints(text);

  try {
    const userContent = buildIntentContent(text, today, dayOfWeek, dateHints);
    const completion = await getGroq().chat.completions.create({
      model: INTENT_MODEL,
      messages: [
        { role: 'system', content: INTENT_SYSTEM_PROMPT },
        { role: 'user', content: userContent },
      ],
      response_format: { type: 'json_object' },
      temperature: 0.1,
      max_tokens: 400,
    });

    const raw = completion.choices[0]?.message?.content ?? '{}';
    return normalizeIntent(JSON.parse(raw), text);
  } catch {
    return fallbackIntent(text, dateHints);
  }
}

export async function parseVoiceInput(text: string): Promise<ParsedTask> {
  const intent = await parseVoiceIntent(text);
  return intentToParseResult(intent, text);
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function buildIntentContent(
  text: string,
  today: string,
  dayOfWeek: string,
  dateHints: DateHint[]
): string {
  const hintLines =
    dateHints.length > 0
      ? `Pre-resolved dates (use exactly as-is): ${dateHints
          .map((h) => `"${h.text}" → ${h.isoDate}`)
          .join(', ')}\n`
      : '';
  return `Today: ${today} (${dayOfWeek})\n${hintLines}User said: "${text}"`;
}

function normalizeIntent(raw: Record<string, unknown>, originalText: string): VoiceIntent {
  const intent = validateIntent(raw.intent as string);
  const taskData = normalizeTaskData(raw.task_data as Record<string, unknown> | null);

  return {
    intent,
    confidence: clamp((raw.confidence as number) ?? 0.5),
    task_ref: (raw.task_ref as string | null) ?? null,
    task_data: taskData,
    query_filters: normalizeQueryFilters(raw.query_filters as Record<string, unknown> | null),
    raw_text: originalText,
  };
}

function validateIntent(raw: string): VoiceIntent['intent'] {
  const valid = ['create', 'complete', 'cancel', 'delay', 'query'] as const;
  return valid.includes(raw as VoiceIntent['intent']) ? (raw as VoiceIntent['intent']) : 'create';
}

function normalizeTaskData(raw: Record<string, unknown> | null): IntentTaskData | null {
  if (!raw) return null;
  return {
    title: (raw.title as string | null) ?? null,
    description: (raw.description as string | null) ?? null,
    due_date: (raw.due_date as string | null) ?? null,
    priority: validatePriority(raw.priority as string),
    ambiguous_fields: Array.isArray(raw.ambiguous_fields) ? (raw.ambiguous_fields as string[]) : [],
    multiple_tasks: (raw.multiple_tasks as boolean) ?? false,
  };
}

function normalizeQueryFilters(
  raw: Record<string, unknown> | null
): VoiceIntent['query_filters'] {
  if (!raw) return null;
  const dateRange = raw.date_range as string | null;
  const validDateRange = ['today', 'this_week', 'overdue'].includes(dateRange ?? '')
    ? (dateRange as 'today' | 'this_week' | 'overdue')
    : null;
  return {
    status: (raw.status as string | null) ?? null,
    date_range: validDateRange,
    search_term: (raw.search_term as string | null) ?? null,
  };
}

function validatePriority(raw: string): TaskPriority {
  return ['high', 'medium', 'low'].includes(raw) ? (raw as TaskPriority) : 'medium';
}

function intentToParseResult(intent: VoiceIntent, originalText: string): ParsedTask {
  const d = intent.task_data;
  return {
    title: d?.title ?? originalText.slice(0, 500),
    description: d?.description ?? null,
    due_date: d?.due_date ?? null,
    priority: d?.priority ?? 'medium',
    confidence: {
      title: intent.confidence,
      due_date: d?.due_date ? Math.min(intent.confidence, 0.95) : 0,
    },
    ambiguous_fields: d?.ambiguous_fields ?? [],
    multiple_tasks: d?.multiple_tasks ?? false,
  };
}

function fallbackIntent(text: string, dateHints: DateHint[]): VoiceIntent {
  // Detect intent from keywords when Groq is unavailable
  const lower = text.toLowerCase();
  let intent: VoiceIntent['intent'] = 'create';

  if (/\b(done|finished|completed?|mark.+as.+done|check.+off)\b/.test(lower)) intent = 'complete';
  else if (/\b(cancel|remove|delete|drop|abandon)\b/.test(lower)) intent = 'cancel';
  else if (/\b(delay|push|postpone|reschedul|move.+to)\b/.test(lower)) intent = 'delay';
  else if (/\b(show|list|what|which|find|get|display|view)\b/.test(lower)) intent = 'query';

  const title = text
    .replace(/^(remind me to|don't forget to|i need to|make sure to|please|can you)\s+/i, '')
    .replace(/\s+by\s+.+$/i, '')
    .replace(/\s+before\s+.+$/i, '')
    .trim()
    .slice(0, 500) || text.slice(0, 500);

  const due_date = dateHints[0]?.isoDate ?? null;
  const priority: TaskPriority =
    /\b(urgent(ly)?|asap|critical(ly)?|immediately|emergency|right now|as soon as possible)\b/i.test(text)
      ? 'high'
      : /\b(whenever|no rush|low.?priority|eventually|someday|not urgent)\b/i.test(text)
        ? 'low'
        : 'medium';

  const task_ref =
    intent !== 'create'
      ? text
          .replace(/^(cancel|complete|mark|finish|done|delay|push|postpone)\s+(the\s+)?/i, '')
          .replace(/\s+(as done|as finished|to .+)$/i, '')
          .trim()
          .slice(0, 100)
      : null;

  return {
    intent,
    confidence: 0.4,
    task_ref,
    task_data: ['create', 'delay'].includes(intent)
      ? { title, description: null, due_date, priority, ambiguous_fields: due_date ? [] : ['due_date'], multiple_tasks: false }
      : null,
    query_filters: intent === 'query'
      ? { status: 'pending', date_range: null, search_term: null }
      : null,
    raw_text: text,
  };
}

function clamp(n: number): number {
  return Math.min(1, Math.max(0, n));
}
