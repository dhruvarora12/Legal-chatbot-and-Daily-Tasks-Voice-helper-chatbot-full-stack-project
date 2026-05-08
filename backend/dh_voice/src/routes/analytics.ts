import { FastifyInstance, FastifyRequest, FastifyReply } from 'fastify';
import { queryOne, queryAll } from '../config/db-helpers.js';
import { verifyAuth } from '../middleware/auth.js';
import { nowUnix } from '../services/dateParser.js';

export async function analyticsRoutes(fastify: FastifyInstance): Promise<void> {
  fastify.addHook('preHandler', verifyAuth);

  /**
   * GET /analytics/overview
   * All key KPIs in one call — primary endpoint for the dashboard.
   */
  fastify.get('/overview', async (request: FastifyRequest, reply: FastifyReply) => {
    const uid = request.user.userId;
    const now = nowUnix();
    const weekAgo = now - 7 * 86400;

    const statusRows = queryAll<{ status: string; count: number }>(
      'SELECT status, COUNT(*) as count FROM tasks WHERE user_id = ? GROUP BY status',
      [uid]
    );
    const statusMap: Record<string, number> = { pending: 0, completed: 0, cancelled: 0, delayed: 0 };
    for (const row of statusRows) statusMap[row.status] = row.count;

    const completion = queryOne<{ total: number; on_time: number; late: number }>(
      `SELECT
         COUNT(*) as total,
         SUM(CASE WHEN completed_at <= due_date THEN 1 ELSE 0 END) as on_time,
         SUM(CASE WHEN completed_at > due_date  THEN 1 ELSE 0 END) as late
       FROM tasks
       WHERE user_id = ? AND status = 'completed' AND due_date IS NOT NULL AND completed_at IS NOT NULL`,
      [uid]
    ) ?? { total: 0, on_time: 0, late: 0 };

    const overduePending = (queryOne<{ count: number }>(
      `SELECT COUNT(*) as count FROM tasks
       WHERE user_id = ? AND status = 'pending' AND due_date IS NOT NULL AND due_date < ?`,
      [uid, now]
    ) ?? { count: 0 }).count;

    const delay = queryOne<{
      total_delayed_ever: number;
      avg_delay_days: number | null;
      multi_delayed: number;
    }>(
      `SELECT
         COUNT(*) as total_delayed_ever,
         AVG(CAST((due_date - original_due_date) AS REAL) / 86400.0) as avg_delay_days,
         SUM(CASE WHEN delay_count >= 2 THEN 1 ELSE 0 END) as multi_delayed
       FROM tasks
       WHERE user_id = ? AND delay_count > 0 AND due_date IS NOT NULL AND original_due_date IS NOT NULL`,
      [uid]
    ) ?? { total_delayed_ever: 0, avg_delay_days: null, multi_delayed: 0 };

    const velocity = queryOne<{ created_this_week: number; completed_this_week: number }>(
      `SELECT
         SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) as created_this_week,
         SUM(CASE WHEN status = 'completed' AND completed_at >= ? THEN 1 ELSE 0 END) as completed_this_week
       FROM tasks WHERE user_id = ?`,
      [weekAgo, weekAgo, uid]
    ) ?? { created_this_week: 0, completed_this_week: 0 };

    return reply.send({
      status_counts: statusMap,
      completion: {
        total_with_due_date: completion.total,
        on_time: completion.on_time ?? 0,
        late: completion.late ?? 0,
        on_time_rate: completion.total > 0
          ? Math.round(((completion.on_time ?? 0) / completion.total) * 100) / 100
          : null,
      },
      overdue_pending: overduePending,
      delay_summary: {
        total_delayed_ever: delay.total_delayed_ever,
        avg_delay_days: delay.avg_delay_days != null
          ? Math.round(delay.avg_delay_days * 10) / 10
          : null,
        multi_delayed: delay.multi_delayed,
      },
      velocity: {
        created_this_week: velocity.created_this_week,
        completed_this_week: velocity.completed_this_week,
      },
    });
  });

  /**
   * GET /analytics/completion-rate
   * Weekly completion rate time series for a line/bar chart. Last 12 weeks, newest first.
   */
  fastify.get('/completion-rate', async (request: FastifyRequest, reply: FastifyReply) => {
    const uid = request.user.userId;

    const rows = queryAll<{
      week: string;
      created: number;
      completed: number;
      on_time_completed: number;
    }>(
      `SELECT
         strftime('%Y-%W', datetime(created_at, 'unixepoch')) as week,
         COUNT(*) as created,
         SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
         SUM(CASE WHEN status = 'completed' AND completed_at IS NOT NULL
                       AND (due_date IS NULL OR completed_at <= due_date)
                  THEN 1 ELSE 0 END) as on_time_completed
       FROM tasks
       WHERE user_id = ?
       GROUP BY week
       ORDER BY week DESC
       LIMIT 12`,
      [uid]
    );

    const data = rows.map((r) => ({
      week: r.week,
      created: r.created,
      completed: r.completed,
      on_time_completed: r.on_time_completed,
      completion_rate: r.created > 0 ? Math.round((r.completed / r.created) * 100) / 100 : 0,
      on_time_rate: r.completed > 0
        ? Math.round((r.on_time_completed / r.completed) * 100) / 100
        : null,
    }));

    return reply.send({ data });
  });

  /**
   * GET /analytics/status-breakdown
   * Count by status including overdue pending — for a pie/donut chart.
   */
  fastify.get('/status-breakdown', async (request: FastifyRequest, reply: FastifyReply) => {
    const uid = request.user.userId;
    const now = nowUnix();

    const breakdown = queryAll<{ status: string; count: number }>(
      'SELECT status, COUNT(*) as count FROM tasks WHERE user_id = ? GROUP BY status',
      [uid]
    );

    const overdue = (queryOne<{ count: number }>(
      `SELECT COUNT(*) as count FROM tasks
       WHERE user_id = ? AND status = 'pending' AND due_date IS NOT NULL AND due_date < ?`,
      [uid, now]
    ) ?? { count: 0 }).count;

    return reply.send({ breakdown, overdue_pending: overdue });
  });

  /**
   * GET /analytics/delay-analysis
   * Delay depth, recurrence, and avg slip size — for gauging estimation quality.
   */
  fastify.get('/delay-analysis', async (request: FastifyRequest, reply: FastifyReply) => {
    const uid = request.user.userId;

    const summary = queryOne<{
      total_delayed_tasks: number;
      avg_delay_days: number | null;
      max_delay_days: number | null;
      delayed_once: number;
      delayed_multiple: number;
      max_delay_count: number;
      avg_delay_count: number | null;
    }>(
      `SELECT
         COUNT(*) as total_delayed_tasks,
         AVG(CAST((due_date - original_due_date) AS REAL) / 86400.0) as avg_delay_days,
         MAX(CAST((due_date - original_due_date) AS REAL) / 86400.0) as max_delay_days,
         SUM(CASE WHEN delay_count = 1 THEN 1 ELSE 0 END) as delayed_once,
         SUM(CASE WHEN delay_count >= 2 THEN 1 ELSE 0 END) as delayed_multiple,
         MAX(delay_count) as max_delay_count,
         AVG(CAST(delay_count AS REAL)) as avg_delay_count
       FROM tasks
       WHERE user_id = ? AND delay_count > 0 AND due_date IS NOT NULL AND original_due_date IS NOT NULL`,
      [uid]
    ) ?? {
      total_delayed_tasks: 0, avg_delay_days: null, max_delay_days: null,
      delayed_once: 0, delayed_multiple: 0, max_delay_count: 0, avg_delay_count: null,
    };

    const totalWithDue = (queryOne<{ count: number }>(
      'SELECT COUNT(*) as count FROM tasks WHERE user_id = ? AND due_date IS NOT NULL',
      [uid]
    ) ?? { count: 0 }).count;

    return reply.send({
      total_delayed_tasks: summary.total_delayed_tasks,
      delay_rate: totalWithDue > 0
        ? Math.round((summary.total_delayed_tasks / totalWithDue) * 100) / 100
        : null,
      avg_delay_days: summary.avg_delay_days != null
        ? Math.round(summary.avg_delay_days * 10) / 10 : null,
      max_delay_days: summary.max_delay_days != null
        ? Math.round(summary.max_delay_days * 10) / 10 : null,
      recurrence: {
        delayed_once: summary.delayed_once,
        delayed_multiple: summary.delayed_multiple,
        multi_delayed_rate: summary.total_delayed_tasks > 0
          ? Math.round((summary.delayed_multiple / summary.total_delayed_tasks) * 100) / 100
          : null,
        max_delay_count: summary.max_delay_count,
        avg_delay_count: summary.avg_delay_count != null
          ? Math.round(summary.avg_delay_count * 10) / 10 : null,
      },
    });
  });

  /**
   * GET /analytics/task-velocity
   * Tasks created vs completed per week — shows whether backlog is growing or shrinking.
   */
  fastify.get('/task-velocity', async (request: FastifyRequest, reply: FastifyReply) => {
    const uid = request.user.userId;

    const rows = queryAll<{
      week: string;
      created: number;
      completed: number;
      cancelled: number;
    }>(
      `SELECT
         strftime('%Y-%W', datetime(created_at, 'unixepoch')) as week,
         COUNT(*) as created,
         SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
         SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled
       FROM tasks
       WHERE user_id = ?
       GROUP BY week
       ORDER BY week DESC
       LIMIT 8`,
      [uid]
    );

    return reply.send({ data: rows.map((r) => ({ ...r, net: r.completed - r.created })) });
  });
}
