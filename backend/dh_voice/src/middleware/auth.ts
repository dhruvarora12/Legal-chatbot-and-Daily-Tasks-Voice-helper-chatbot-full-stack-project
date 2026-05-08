import { FastifyRequest, FastifyReply } from 'fastify';
import jwt from 'jsonwebtoken';
import { JwtPayload } from '../types/index.js';

export async function verifyAuth(
  request: FastifyRequest,
  reply: FastifyReply
): Promise<void> {
  const auth = request.headers.authorization;
  if (!auth?.startsWith('Bearer ')) {
    reply.status(401).send({ error: 'Missing or invalid Authorization header' });
    return;
  }

  const token = auth.slice(7);
  try {
    const payload = jwt.verify(token, process.env.JWT_SECRET!) as JwtPayload;
    request.user = { userId: payload.userId, email: payload.email };
  } catch {
    reply.status(401).send({ error: 'Invalid or expired token' });
  }
}

export function signToken(userId: string, email: string): string {
  return jwt.sign(
    { userId, email } satisfies JwtPayload,
    process.env.JWT_SECRET!,
    { expiresIn: '7d' }
  );
}
