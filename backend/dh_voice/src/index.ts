import 'dotenv/config';
import { initDb } from './config/database.js';
import { buildServer } from './server.js';

const PORT = parseInt(process.env.PORT ?? '3000', 10);

async function main() {
  if (!process.env.JWT_SECRET) {
    console.error('ERROR: JWT_SECRET is not set. Copy .env.example to .env and fill in the values.');
    process.exit(1);
  }
  if (!process.env.GROQ_API_KEY) {
    console.warn('WARNING: GROQ_API_KEY not set. Voice parsing will use fallback rule-based extraction.');
  }

  await initDb();

  const fastify = await buildServer();

  try {
    await fastify.listen({ port: PORT, host: '0.0.0.0' });
    console.log(`Server running on http://localhost:${PORT}`);
  } catch (err) {
    fastify.log.error(err);
    process.exit(1);
  }
}

main();
