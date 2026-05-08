import { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import { z } from 'zod';
import { verifyAuth } from '../middleware/auth.js';
import { parseVoiceIntent } from '../services/nlp.js';
import {
  createTask, completeTask, cancelTask, delayTask, listTasks, searchTasks,
  assertOwner, serializeTask, getTaskById,
} from '../services/taskService.js';
import { nowUnix } from '../services/dateParser.js';
import { VoiceIntent } from '../types/index.js';

const VoiceBody = z.object({
  text: z.string().min(1).max(2000),
});

export async function voiceRoutes(fastify: FastifyInstance): Promise<void> {
  fastify.addHook('preHandler', verifyAuth);

  /**
   * POST /voice/parse
   * Analyse text and return structured intent + task data without executing anything.
   * Use this for confirmation UIs before committing an action.
   */
  fastify.post('/parse', async (request: FastifyRequest, reply: FastifyReply) => {
    const result = VoiceBody.safeParse(request.body);
    if (!result.success) {
      return reply.status(400).send({ error: 'Validation failed', details: result.error.flatten() });
    }

    const intent = await parseVoiceIntent(result.data.text);
    const warnings = buildWarnings(intent);

    return reply.send({ intent, warnings });
  });

  /**
   * POST /voice/action
   * The primary voice endpoint. Parses intent AND executes the action in one call.
   *
   * - create  → creates a new task
   * - complete → finds task by keywords and marks complete
   * - cancel  → finds task by keywords and cancels
   * - delay   → finds task by keywords and delays to new date
   * - query   → returns filtered task list
   *
   * If multiple tasks match for complete/cancel/delay, returns 422 with the list
   * so the client can confirm which one the user meant.
   */
  fastify.post('/action', async (request: FastifyRequest, reply: FastifyReply) => {
    const result = VoiceBody.safeParse(request.body);
    if (!result.success) {
      return reply.status(400).send({ error: 'Validation failed', details: result.error.flatten() });
    }

    const uid = request.user.userId;
    const intent = await parseVoiceIntent(result.data.text);

    switch (intent.intent) {
      case 'create':
        return handleCreate(intent, uid, result.data.text, reply);

      case 'complete':
      case 'cancel':
        return handleCompleteOrCancel(intent, uid, reply);

      case 'delay':
        return handleDelay(intent, uid, reply);

      case 'query':
        return handleQuery(intent, uid, reply);

      default:
        return reply.status(422).send({ error: 'Could not determine action', intent });
    }
  });
}

// ---------------------------------------------------------------------------
// Intent handlers
// ---------------------------------------------------------------------------

async function handleCreate(
  intent: VoiceIntent,
  userId: string,
  originalText: string,
  reply: FastifyReply
) {
  const d = intent.task_data;
  if (!d?.title) {
    return reply.status(422).send({
      error: 'Could not extract a task title from your input.',
      intent,
    });
  }

  if (d.multiple_tasks) {
    return reply.status(422).send({
      error: 'Multiple tasks detected. Please describe one task at a time, or use POST /voice/parse to review.',
      intent,
    });
  }

  const task = createTask({
    userId,
    title: d.title,
    description: d.description,
    due_date: d.due_date,
    priority: d.priority,
    original_voice_input: originalText,
    parse_confidence: intent.confidence,
    ambiguous_fields: d.ambiguous_fields,
  });

  return reply.status(201).send({
    action: 'created',
    task: serializeTask(task),
    intent,
    warnings: buildWarnings(intent),
  });
}

async function handleCompleteOrCancel(
  intent: VoiceIntent,
  userId: string,
  reply: FastifyReply
) {
  const taskRef = intent.task_ref;
  if (!taskRef) {
    return reply.status(422).send({
      error: 'No task reference found. Please say which task you want to complete or cancel.',
      intent,
    });
  }

  const matches = searchTasks(userId, taskRef);
  if (matches.length === 0) {
    return reply.status(404).send({
      error: `No active tasks found matching "${taskRef}". Check spelling or use GET /tasks to browse.`,
      intent,
    });
  }
  if (matches.length > 1) {
    return reply.status(422).send({
      error: `${matches.length} tasks match "${taskRef}". Please confirm which one you mean.`,
      matches: matches.map(serializeTask),
      intent,
    });
  }

  const target = matches[0];
  if (!assertOwner(target, userId)) {
    return reply.status(404).send({ error: 'Task not found', intent });
  }
  if (target.status === 'completed' || target.status === 'cancelled') {
    return reply.status(409).send({
      error: `Task is already ${target.status}.`,
      task: serializeTask(target),
      intent,
    });
  }

  const updated =
    intent.intent === 'complete' ? completeTask(target.id) : cancelTask(target.id);

  return reply.send({
    action: intent.intent === 'complete' ? 'completed' : 'cancelled',
    task: serializeTask(updated!),
    intent,
  });
}

async function handleDelay(intent: VoiceIntent, userId: string, reply: FastifyReply) {
  const newDueDate = intent.task_data?.due_date;
  if (!newDueDate) {
    return reply.status(422).send({
      error: 'New due date not found. Please say when you want to delay the task to.',
      intent,
    });
  }

  const taskRef = intent.task_ref;
  if (!taskRef) {
    return reply.status(422).send({
      error: 'No task reference found. Please say which task you want to delay.',
      intent,
    });
  }

  const matches = searchTasks(userId, taskRef);
  if (matches.length === 0) {
    return reply.status(404).send({
      error: `No active tasks found matching "${taskRef}".`,
      intent,
    });
  }
  if (matches.length > 1) {
    return reply.status(422).send({
      error: `${matches.length} tasks match "${taskRef}". Please confirm which one you mean.`,
      matches: matches.map(serializeTask),
      intent,
    });
  }

  const target = matches[0];
  if (!assertOwner(target, userId)) {
    return reply.status(404).send({ error: 'Task not found', intent });
  }
  if (target.status === 'completed' || target.status === 'cancelled') {
    return reply.status(409).send({
      error: `Cannot delay a ${target.status} task.`,
      task: serializeTask(target),
      intent,
    });
  }

  const updated = delayTask(target.id, newDueDate);
  return reply.send({ action: 'delayed', task: serializeTask(updated!), intent });
}

async function handleQuery(intent: VoiceIntent, userId: string, reply: FastifyReply) {
  const qf = intent.query_filters ?? {};
  const now = nowUnix();

  const tasks = listTasks(userId, {
    status: qf.status ?? undefined,
    overdue: qf.date_range === 'overdue',
    from: qf.date_range === 'today' ? new Date().toISOString().split('T')[0] : undefined,
    to:
      qf.date_range === 'today'
        ? new Date().toISOString().split('T')[0]
        : qf.date_range === 'this_week'
          ? new Date((now + 7 * 86400) * 1000).toISOString().split('T')[0]
          : undefined,
  }).filter((t) => {
    if (qf.search_term) {
      return t.title.toLowerCase().includes(qf.search_term.toLowerCase());
    }
    return true;
  });

  return reply.send({
    action: 'query',
    tasks: tasks.map(serializeTask),
    count: tasks.length,
    filters_applied: qf,
    intent,
  });
}

// ---------------------------------------------------------------------------
// Warnings
// ---------------------------------------------------------------------------

function buildWarnings(intent: VoiceIntent): string[] {
  const warnings: string[] = [];
  const d = intent.task_data;

  if (intent.confidence < 0.5) {
    warnings.push('Low confidence in intent detection — please verify before proceeding.');
  }
  if (d?.ambiguous_fields?.includes('due_date')) {
    warnings.push('No due date detected. The task will be created without one.');
  }
  if (d?.multiple_tasks) {
    warnings.push('Multiple tasks detected in your input. Only the first was processed.');
  }
  if (d?.ambiguous_fields?.includes('title')) {
    warnings.push('Task title was extracted with low confidence — consider editing it.');
  }

  return warnings;
}
