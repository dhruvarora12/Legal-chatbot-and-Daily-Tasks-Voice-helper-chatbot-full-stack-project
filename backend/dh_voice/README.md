# Voice-First Task Manager — Backend API

A REST API for a task management app where the primary input method is **natural language voice**. Speak a sentence like _"Urgently fix the production bug"_ and the server figures out the title, due date, priority, and intent — then acts on it.

---

## What This Project Does

- Accepts **natural language text** (transcribed from voice on the client) and extracts structured task data using an LLM
- Detects **intent** automatically: create a task, complete it, cancel it, delay it, or query your list — all from a single voice endpoint
- Stores tasks in a **local SQLite database** (no server required, no cloud dependency)
- Authenticates users with **JWT tokens**
- Provides an **analytics API** with KPIs ready to be wired into dashboard charts

### Example

```
User says: "Remind me to submit the quarterly report by next Friday"

POST /api/voice/action
→ { action: "created", task: { title: "Submit quarterly report", due_date: "2026-05-15", priority: "medium" } }
```

```
User says: "Urgently fix the login bug — it's breaking production"

POST /api/voice/action
→ { action: "created", task: { title: "Fix the login bug", priority: "high", description: "Breaking production" } }
```

```
User says: "Mark the quarterly report as done"

POST /api/voice/action
→ { action: "completed", task: { title: "Submit quarterly report", status: "completed" } }
```

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Runtime | Node.js + TypeScript | Typed, fast, rich ecosystem |
| Framework | Fastify v5 | ~2x faster than Express, built-in validation |
| Database | sql.js (WASM SQLite) | Zero native compilation — works on any machine |
| NLP | Groq API (`llama-3.3-70b-versatile`) | Free tier, ~300 tokens/sec, strong reasoning |
| Date parsing | chrono-node | Resolves "next Friday" locally before LLM call |
| Auth | JWT + bcryptjs | Fully offline, no external auth service |
| Validation | Zod | Runtime type safety at API boundaries |

> **Why sql.js?** `better-sqlite3` and `sqlite3` require Visual Studio C++ Build Tools to compile on Windows. `sql.js` is a pure WebAssembly port of SQLite — no compilation, works everywhere.

> **Why Groq?** It offers a genuinely free tier (14,400 requests/day, 30 req/min) with extremely fast inference. The `llama-3.3-70b-versatile` model is strong enough to handle complex natural language reliably.

---

## Prerequisites

- **Node.js 18+** (tested on Node 24)
- A free **Groq API key** from [console.groq.com](https://console.groq.com) _(optional — a fallback rule-based parser works without it, but with lower accuracy)_

---

## Setup

### 1. Install dependencies

```bash
npm install
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

```env
# Free key from https://console.groq.com
GROQ_API_KEY=your_groq_api_key_here

# Any long random string — used to sign login tokens
JWT_SECRET=change_me_to_a_long_random_string

PORT=3000
DB_PATH=./data/tasks.db
```

To generate a secure `JWT_SECRET`:
```bash
node -e "console.log(require('crypto').randomBytes(64).toString('hex'))"
```

### 3. Start the development server

```bash
npm run dev
```

The server starts at `http://localhost:3000`. The SQLite database is created automatically at `./data/tasks.db` on first run.

### 4. Build for production

```bash
npm run build
npm start
```

---

## API Reference

All endpoints under `/api/tasks`, `/api/voice`, and `/api/analytics` require a JWT in the `Authorization` header:

```
Authorization: Bearer <token>
```

---

### Auth

#### Register
```
POST /api/auth/register
```
```json
{ "email": "you@example.com", "name": "Your Name", "password": "yourpassword" }
```
Returns: `{ token, user }`

#### Login
```
POST /api/auth/login
```
```json
{ "email": "you@example.com", "password": "yourpassword" }
```
Returns: `{ token, user }`

#### Get current user
```
GET /api/auth/me
```
Returns: `{ user }`

---

### Voice (the primary interface)

#### `POST /api/voice/action` — **The main endpoint**

Send any natural language text. The server detects the intent and executes the action in one call.

```json
{ "text": "Remind me to call the client before Thursday" }
```

**Supported intents:**

| What you say | Intent | What happens |
|---|---|---|
| "Remind me to X by Y" | `create` | New task created |
| "Add X to my list" | `create` | New task created |
| "Urgently do X" | `create` | Task created with `priority: "high"` |
| "No rush, clean up the docs" | `create` | Task created with `priority: "low"` |
| "Mark X as done" | `complete` | Finds task by keywords, marks complete |
| "I finished X" | `complete` | Finds task by keywords, marks complete |
| "Cancel X" | `cancel` | Finds task by keywords, cancels it |
| "Push X to next Tuesday" | `delay` | Finds task, delays to new date |
| "Show me overdue tasks" | `query` | Returns filtered task list |
| "What's due this week?" | `query` | Returns tasks due within 7 days |

**Response (create example):**
```json
{
  "action": "created",
  "task": {
    "id": "uuid",
    "title": "Call the client",
    "due_date": "2026-05-14T00:00:00.000Z",
    "priority": "medium",
    "status": "pending"
  },
  "intent": {
    "intent": "create",
    "confidence": 0.94,
    "task_data": { "title": "Call the client", "due_date": "2026-05-14", "priority": "medium" }
  },
  "warnings": []
}
```

**Ambiguity handling:**
- If multiple tasks match a "complete" / "cancel" / "delay" phrase → returns `422` with the list of matches so the client can confirm
- If no due date is detected → task created with `due_date: null`, warning included
- If multiple tasks detected in one sentence → returns `422` asking to create one at a time

---

#### `POST /api/voice/parse` — Preview without executing

Returns the parsed intent and task data **without creating or modifying anything**. Use this to show a confirmation dialog before committing.

```json
{ "text": "Remind me to submit the quarterly report by next Friday" }
```

**Response:**
```json
{
  "intent": {
    "intent": "create",
    "confidence": 0.95,
    "task_ref": null,
    "task_data": {
      "title": "Submit quarterly report",
      "due_date": "2026-05-15",
      "priority": "medium",
      "ambiguous_fields": [],
      "multiple_tasks": false
    },
    "query_filters": null
  },
  "warnings": []
}
```

---

### Tasks

#### List tasks
```
GET /api/tasks
```

Query parameters:

| Param | Values | Description |
|---|---|---|
| `status` | `pending`, `completed`, `cancelled`, `delayed` | Filter by status |
| `priority` | `high`, `medium`, `low` | Filter by priority |
| `overdue` | `true` | Only tasks past their due date |
| `from` | `YYYY-MM-DD` | Due date range start |
| `to` | `YYYY-MM-DD` | Due date range end |
| `q` | any string | Full-text search across task titles |

Results are sorted: **high priority first**, then by due date ascending.

#### Create task (structured)
```
POST /api/tasks
```
```json
{
  "title": "Write unit tests",
  "description": "Cover auth and task routes",
  "due_date": "2026-05-20",
  "priority": "high"
}
```

#### Create task from voice (parse + create)
```
POST /api/tasks/voice
```
```json
{ "text": "Fix the login bug before end of day tomorrow" }
```

#### Get single task
```
GET /api/tasks/:id
```

#### Update task
```
PATCH /api/tasks/:id
```
```json
{ "title": "Updated title", "due_date": "2026-05-25", "priority": "low" }
```

#### Mark complete
```
POST /api/tasks/:id/complete
```

#### Cancel task
```
POST /api/tasks/:id/cancel
```

#### Delay task
```
POST /api/tasks/:id/delay
```
```json
{ "new_due_date": "2026-05-25", "reason": "Waiting on design approval" }
```

#### Get task history
```
GET /api/tasks/:id/history
```
Returns a full audit log of every status change.

---

### Analytics

All analytics endpoints return data ready to be plugged into charting libraries (Chart.js, Recharts, etc.).

#### `GET /api/analytics/overview`
All KPIs in one call — use this as the primary dashboard data source.

```json
{
  "status_counts": { "pending": 3, "completed": 12, "cancelled": 1, "delayed": 2 },
  "completion": {
    "total_with_due_date": 10,
    "on_time": 7,
    "late": 3,
    "on_time_rate": 0.70
  },
  "overdue_pending": 2,
  "delay_summary": {
    "total_delayed_ever": 4,
    "avg_delay_days": 3.5,
    "multi_delayed": 1
  },
  "velocity": {
    "created_this_week": 5,
    "completed_this_week": 3
  }
}
```

#### `GET /api/analytics/completion-rate`
Weekly completion rate time series for a line or bar chart. Returns last 12 weeks.

```json
{
  "data": [
    { "week": "2026-19", "created": 5, "completed": 4, "on_time_completed": 3, "completion_rate": 0.8, "on_time_rate": 0.75 }
  ]
}
```

#### `GET /api/analytics/status-breakdown`
Count by status for a pie/donut chart, plus overdue pending count.

#### `GET /api/analytics/delay-analysis`
How often tasks are delayed, by how much, and whether users delay the same tasks repeatedly.

```json
{
  "total_delayed_tasks": 4,
  "delay_rate": 0.33,
  "avg_delay_days": 3.5,
  "max_delay_days": 7.0,
  "recurrence": {
    "delayed_once": 3,
    "delayed_multiple": 1,
    "multi_delayed_rate": 0.25,
    "max_delay_count": 2,
    "avg_delay_count": 1.2
  }
}
```

#### `GET /api/analytics/task-velocity`
Tasks created vs completed per week. If `net` is consistently negative, the backlog is shrinking (healthy). If positive, it's growing.

```json
{
  "data": [
    { "week": "2026-19", "created": 5, "completed": 4, "cancelled": 0, "net": -1 }
  ]
}
```

---

## Project Structure

```
dh_voice/
├── src/
│   ├── index.ts                  ← entry point
│   ├── server.ts                 ← Fastify setup + plugins
│   ├── config/
│   │   ├── database.ts           ← sql.js init, migrations, file persistence
│   │   └── db-helpers.ts         ← queryOne / queryAll / execute wrappers
│   ├── routes/
│   │   ├── auth.ts               ← register, login, /me
│   │   ├── voice.ts              ← /parse and /action (the AI voice endpoints)
│   │   ├── tasks.ts              ← full task CRUD + status transitions
│   │   └── analytics.ts          ← 5 analytics endpoints
│   ├── services/
│   │   ├── nlp.ts                ← Groq LLM + fallback intent parser
│   │   ├── dateParser.ts         ← chrono-node relative-date resolver
│   │   └── taskService.ts        ← business logic, task search, serialization
│   ├── middleware/
│   │   └── auth.ts               ← JWT preHandler + signToken
│   └── types/
│       └── index.ts              ← shared TypeScript interfaces
├── config/
│   └── schema.sql                ← reference schema (migrations run automatically)
├── .env.example
├── package.json
└── tsconfig.json
```

---

## How the NLP Works

1. **Date pre-processing** — `chrono-node` resolves relative dates locally before the LLM call. "Next Friday" becomes `2026-05-15` with zero API cost.

2. **Intent + data extraction** — The resolved text is sent to `llama-3.3-70b-versatile` on Groq with a carefully engineered system prompt that includes 8 few-shot examples. The model returns structured JSON with:
   - `intent` — what the user wants to do
   - `task_data` — title, description, due_date, priority, ambiguous_fields
   - `task_ref` — keywords to find an existing task (for complete/cancel/delay)
   - `query_filters` — for list queries
   - `confidence` — 0–1 score

3. **Priority inference** — Detected from signal words: "urgently" / "ASAP" / "critical" → `high`; "whenever" / "no rush" / "low priority" → `low`; everything else → `medium`.

4. **Ambiguity rules:**
   - No due date → `due_date: null`, never guessed
   - Vague date ("soon", "eventually") → `due_date: null` + flag in `ambiguous_fields`
   - Multiple tasks in one sentence → `multiple_tasks: true`, action blocked, user prompted
   - Multiple tasks match a reference → `422` with all matches, user must confirm

5. **Fallback** — If Groq is unavailable (no key, rate limit), a rule-based regex parser handles intent detection and chrono-node handles date extraction. Confidence is set to `0.4` so the client knows to prompt for confirmation.

---

## Quick Test (curl)

```bash
# 1. Register
curl -X POST http://localhost:3000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","name":"You","password":"password123"}'

# Copy the token from the response, then:
TOKEN="paste_token_here"

# 2. Create a task via voice
curl -X POST http://localhost:3000/api/voice/action \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"Urgently fix the login bug before end of day tomorrow"}'

# 3. Mark it done via voice
curl -X POST http://localhost:3000/api/voice/action \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"Mark the login bug as done"}'

# 4. Check analytics
curl http://localhost:3000/api/analytics/overview \
  -H "Authorization: Bearer $TOKEN"
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `JWT_SECRET` | Yes | Secret key for signing JWT tokens. Use a long random string. |
| `GROQ_API_KEY` | Recommended | Free key from [console.groq.com](https://console.groq.com). Without it, fallback parser is used. |
| `PORT` | No | Server port. Default: `3000` |
| `DB_PATH` | No | Path for the SQLite database file. Default: `./data/tasks.db` |
| `LOG_LEVEL` | No | Pino log level (`info`, `debug`, `warn`). Default: `info` |
