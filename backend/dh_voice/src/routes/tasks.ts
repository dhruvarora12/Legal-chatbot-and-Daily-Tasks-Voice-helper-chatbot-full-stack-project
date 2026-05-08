import { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import { z } from 'zod';
import { verifyAuth } from '../middleware/auth.js';
import { parseVoiceIntent } from '../services/nlp.js';
import {
  createTask, listTasks, searchTasks, getTaskById, updateTask,
  completeTask, cancelTask, delayTask, getTaskHistory,
  assertOwner, serializeTask,
} from '../services/taskService.js';

const PriorityEnum = z.enum(['high', 'medium', 'low']).optional();

const CreateTaskBody = z.object({
  title: z.string().min(1).max(500),
  description: z.string().max(2000).nullable().optional(),
  due_date: z.string().nullable().optional(),
  priority: PriorityEnum,
});

const UpdateTaskBody = z.object({
  title: z.string().min(1).max(500).optional(),
  description: z.string().max(2000).nullable().optional(),
  due_date: z.string().nullable().optional(),
  priority: PriorityEnum,
});

const DelayBody = z.object({
  new_due_date: z.string().min(1),
  reason: z.string().max(500).optional(),
});

const VoiceCreateBody = z.object({
  text: z.string().min(1).max(2000),
});

const ListQuery = z.object({
  status: z.enum(['pending', 'completed', 'cancelled', 'delayed']).optional(),
  priority: z.enum(['high', 'medium', 'low']).optional(),
  from: z.string().optional(),
  to: z.string().optional(),
  overdue: z.string().optional(),
  q: z.string().optional(),
});

export async function taskRoutes(fastify: FastifyInstance): Promise<void> {
  fastify.addHook('preHandler', verifyAuth);

  // List tasks (with optional full-text search via ?q=)
  fastify.get('/', async (request: FastifyRequest, reply: FastifyReply) => {
    const parsed = ListQuery.safeParse(request.query);
    if (!parsed.success) {
      return reply.status(400).send({ error: 'Invalid query params', details: parsed.error.flatten() });
    }
    const { q, status, priority, from, to, overdue } = parsed.data;

    const tasks = q
      ? searchTasks(request.user.userId, q, false)
      : listTasks(request.user.userId, { status, priority, from, to, overdue: overdue === 'true' });

    return reply.send({ tasks: tasks.map(serializeTask) });
  });

  // Create task from structured data
  fastify.post('/', async (request: FastifyRequest, reply: FastifyReply) => {
    const result = CreateTaskBody.safeParse(request.body);
    if (!result.success) {
      return reply.status(400).send({ error: 'Validation failed', details: result.error.flatten() });
    }
    const task = createTask({ userId: request.user.userId, ...result.data });
    return reply.status(201).send({ task: serializeTask(task) });
  });

  // Create task from voice text (parse + create — backwards-compatible alias)
  fastify.post('/voice', async (request: FastifyRequest, reply: FastifyReply) => {
    const result = VoiceCreateBody.safeParse(request.body);
    if (!result.success) {
      return reply.status(400).send({ error: 'Validation failed', details: result.error.flatten() });
    }

    const intent = await parseVoiceIntent(result.data.text);

    if (intent.intent !== 'create' && intent.confidence > 0.7) {
      return reply.status(422).send({
        error: `This looks like a "${intent.intent}" intent, not a creation. Use POST /api/voice/action to handle all intents automatically.`,
        intent,
      });
    }

    const d = intent.task_data;
    if (!d?.title) {
      return reply.status(422).send({ error: 'Could not extract a task title from your input.', intent });
    }
    if (d.multiple_tasks) {
      return reply.status(422).send({
        error: 'Multiple tasks detected. Please create them one at a time.',
        intent,
      });
    }

    const task = createTask({
      userId: request.user.userId,
      title: d.title,
      description: d.description,
      due_date: d.due_date,
      priority: d.priority,
      original_voice_input: result.data.text,
      parse_confidence: intent.confidence,
      ambiguous_fields: d.ambiguous_fields,
    });

    const warnings: string[] = [];
    if (d.ambiguous_fields.includes('due_date')) warnings.push('No due date detected.');
    if (intent.confidence < 0.5) warnings.push('Low confidence extraction — please review the title.');

    return reply.status(201).send({ task: serializeTask(task), intent, warnings });
  });

  // Get single task
  fastify.get('/:id', async (request: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply) => {
    const task = getTaskById(request.params.id);
    if (!assertOwner(task, request.user.userId)) {
      return reply.status(404).send({ error: 'Task not found' });
    }
    return reply.send({ task: serializeTask(task) });
  });

  // Get task history
  fastify.get('/:id/history', async (request: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply) => {
    const task = getTaskById(request.params.id);
    if (!assertOwner(task, request.user.userId)) {
      return reply.status(404).send({ error: 'Task not found' });
    }
    return reply.send({ history: getTaskHistory(request.params.id) });
  });

  // Update task fields
  fastify.patch('/:id', async (request: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply) => {
    const result = UpdateTaskBody.safeParse(request.body);
    if (!result.success) {
      return reply.status(400).send({ error: 'Validation failed', details: result.error.flatten() });
    }
    const task = getTaskById(request.params.id);
    if (!assertOwner(task, request.user.userId)) {
      return reply.status(404).send({ error: 'Task not found' });
    }
    const updated = updateTask(request.params.id, result.data);
    return reply.send({ task: serializeTask(updated!) });
  });

  // Mark complete
  fastify.post('/:id/complete', async (request: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply) => {
    const task = getTaskById(request.params.id);
    if (!assertOwner(task, request.user.userId)) {
      return reply.status(404).send({ error: 'Task not found' });
    }
    if (task.status === 'completed' || task.status === 'cancelled') {
      return reply.status(409).send({ error: `Task is already ${task.status}` });
    }
    return reply.send({ task: serializeTask(completeTask(request.params.id)!) });
  });

  // Mark cancelled
  fastify.post('/:id/cancel', async (request: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply) => {
    const task = getTaskById(request.params.id);
    if (!assertOwner(task, request.user.userId)) {
      return reply.status(404).send({ error: 'Task not found' });
    }
    if (task.status === 'completed' || task.status === 'cancelled') {
      return reply.status(409).send({ error: `Task is already ${task.status}` });
    }
    return reply.send({ task: serializeTask(cancelTask(request.params.id)!) });
  });

  // Delay task
  fastify.post('/:id/delay', async (request: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply) => {
    const result = DelayBody.safeParse(request.body);
    if (!result.success) {
      return reply.status(400).send({ error: 'Validation failed', details: result.error.flatten() });
    }
    const task = getTaskById(request.params.id);
    if (!assertOwner(task, request.user.userId)) {
      return reply.status(404).send({ error: 'Task not found' });
    }
    if (task.status === 'completed' || task.status === 'cancelled') {
      return reply.status(409).send({ error: `Cannot delay a ${task.status} task` });
    }
    return reply.send({ task: serializeTask(delayTask(request.params.id, result.data.new_due_date, result.data.reason)!) });
  });
}
