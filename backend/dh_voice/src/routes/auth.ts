import { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import bcrypt from 'bcryptjs';
import { v4 as uuidv4 } from 'uuid';
import { z } from 'zod';
import { queryOne, execute } from '../config/db-helpers.js';
import { verifyAuth, signToken } from '../middleware/auth.js';
import { nowUnix } from '../services/dateParser.js';
import { User } from '../types/index.js';

const RegisterBody = z.object({
  email: z.string().email(),
  name: z.string().min(1).max(100),
  password: z.string().min(8).max(128),
});

const LoginBody = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

export async function authRoutes(fastify: FastifyInstance): Promise<void> {
  fastify.post('/register', async (request: FastifyRequest, reply: FastifyReply) => {
    const result = RegisterBody.safeParse(request.body);
    if (!result.success) {
      return reply.status(400).send({ error: 'Validation failed', details: result.error.flatten() });
    }
    const { email, name, password } = result.data;

    const existing = queryOne('SELECT id FROM users WHERE email = ?', [email]);
    if (existing) {
      return reply.status(409).send({ error: 'Email already registered' });
    }

    const id = uuidv4();
    const password_hash = await bcrypt.hash(password, 12);

    execute(
      'INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)',
      [id, email, name, password_hash, nowUnix()]
    );

    const token = signToken(id, email);
    return reply.status(201).send({ token, user: { id, email, name } });
  });

  fastify.post('/login', async (request: FastifyRequest, reply: FastifyReply) => {
    const result = LoginBody.safeParse(request.body);
    if (!result.success) {
      return reply.status(400).send({ error: 'Validation failed', details: result.error.flatten() });
    }
    const { email, password } = result.data;

    const user = queryOne<User>('SELECT * FROM users WHERE email = ?', [email]);
    if (!user) {
      return reply.status(401).send({ error: 'Invalid credentials' });
    }

    const valid = await bcrypt.compare(password, user.password_hash);
    if (!valid) {
      return reply.status(401).send({ error: 'Invalid credentials' });
    }

    const token = signToken(user.id, user.email);
    return reply.send({ token, user: { id: user.id, email: user.email, name: user.name } });
  });

  fastify.get(
    '/me',
    { preHandler: verifyAuth },
    async (request: FastifyRequest, reply: FastifyReply) => {
      const user = queryOne<Omit<User, 'password_hash'>>(
        'SELECT id, email, name, created_at FROM users WHERE id = ?',
        [request.user.userId]
      );
      if (!user) return reply.status(404).send({ error: 'User not found' });
      return reply.send({ user });
    }
  );
}
