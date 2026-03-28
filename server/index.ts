/**
 * server/index.ts
 *
 * Express server entry point for the PACT Dashboard backend.
 * Binds to 127.0.0.1:3001 (localhost only). Serves REST API endpoints
 * that read from ~/.claude/ filesystem and pact-memory SQLite database.
 * Strictly read-only -- never writes to any data source.
 */

import express from 'express';
import cors from 'cors';
import { SERVER_PORT, SERVER_HOST } from './config.js';
import { closeMemoryDb } from './scanners/memory-reader.js';

// Route modules
import overviewRoutes from './routes/overview.js';
import sessionRoutes from './routes/sessions.js';
import teamRoutes from './routes/teams.js';
import taskRoutes from './routes/tasks.js';
import messageRoutes from './routes/messages.js';
import memoryRoutes from './routes/memory.js';
import agentMemoryRoutes from './routes/agent-memory.js';
import worktreeRoutes from './routes/worktrees.js';
import telegramRoutes from './routes/telegram.js';
import healthRoutes from './routes/health.js';

const app = express();

// CORS: allow requests from the Vite dev server
app.use(
  cors({
    origin: ['http://localhost:5173', 'http://127.0.0.1:5173'],
  }),
);

app.use(express.json());

// Mount all API routes under /api
app.use('/api', overviewRoutes);
app.use('/api', sessionRoutes);
app.use('/api', teamRoutes);
app.use('/api', taskRoutes);
app.use('/api', messageRoutes);
app.use('/api', memoryRoutes);
app.use('/api', agentMemoryRoutes);
app.use('/api', worktreeRoutes);
app.use('/api', telegramRoutes);
app.use('/api', healthRoutes);

// Start server
const server = app.listen(SERVER_PORT, SERVER_HOST, () => {
  console.log(`PACT Dashboard API running at http://${SERVER_HOST}:${SERVER_PORT}`);
});

// Graceful shutdown
function shutdown() {
  console.log('\nShutting down...');
  closeMemoryDb();
  server.close(() => {
    process.exit(0);
  });
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
