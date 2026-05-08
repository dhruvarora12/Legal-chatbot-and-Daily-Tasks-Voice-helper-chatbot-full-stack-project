import Fastify from 'fastify';
import cors from '@fastify/cors';
import helmet from '@fastify/helmet';
import rateLimit from '@fastify/rate-limit';
import { authRoutes } from './routes/auth.js';
import { voiceRoutes } from './routes/voice.js';
import { taskRoutes } from './routes/tasks.js';
import { analyticsRoutes } from './routes/analytics.js';

export async function buildServer() {
  const fastify = Fastify({
    logger: {
      level: process.env.LOG_LEVEL ?? 'info',
      transport: process.env.NODE_ENV !== 'production'
        ? { target: 'pino-pretty', options: { colorize: true } }
        : undefined,
    },
  });

  // Allow POST requests with Content-Type: application/json but no body (e.g. /complete, /cancel)
  fastify.addContentTypeParser('application/json', { parseAs: 'string' }, (_req, body: string, done) => {
    if (!body || body.trim() === '') { done(null, {}); return; }
    try { done(null, JSON.parse(body)); } catch (err) { done(err as Error); }
  });

  await fastify.register(helmet, { contentSecurityPolicy: false });
  await fastify.register(cors, { origin: true });
  await fastify.register(rateLimit, {
    max: 100,
    timeWindow: '1 minute',
    errorResponseBuilder: () => ({ error: 'Too many requests, slow down.' }),
  });

  fastify.setErrorHandler((error: { statusCode?: number; message?: string }, _request, reply) => {
    const status = error.statusCode ?? 500;
    if (status >= 500) fastify.log.error(error);
    reply.status(status).send({ error: error.message ?? 'Internal server error' });
  });

  await fastify.register(authRoutes,      { prefix: '/api/auth' });
  await fastify.register(voiceRoutes,     { prefix: '/api/voice' });
  await fastify.register(taskRoutes,      { prefix: '/api/tasks' });
  await fastify.register(analyticsRoutes, { prefix: '/api/analytics' });

  fastify.get('/health', async () => ({ status: 'ok', timestamp: new Date().toISOString() }));

  return fastify;
}
